"""Main orchestration: IMAP fetch → parse → match → upsert → log."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .config import EMAIL_LOOKBACK_DAYS, MARK_AS_READ, POLL_INTERVAL_MIN
from .database import DatabaseManager
from .email_client import EmailClient, FetchedEmail, is_nav_email
from .fund_matcher import FundMatcher
from .logger import log
from .parser import (
    NavRecord,
    deduplicate,
    parse_attachment,
    parse_html_body,
    parse_text_body,
)


@dataclass
class RunStats:
    emails_scanned: int = 0
    emails_matched: int = 0
    emails_succeeded: int = 0
    emails_failed: int = 0
    emails_ignored: int = 0
    nav_inserted: int = 0
    nav_updated: int = 0
    aborted: bool = False


class SyncEngine:
    """Stateless engine; holds shared deps. Call run_once() or daemon()."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or DatabaseManager()
        self.matcher = FundMatcher(self.db)

    # ---------------------------------------------------------------- public
    def run_once(self, since_days: int = EMAIL_LOOKBACK_DAYS, only_unseen: bool = False) -> RunStats:
        stats = RunStats()
        try:
            with EmailClient() as client:
                uids = client.search_nav_emails(since_days=since_days, only_unseen=only_unseen)
                stats.emails_scanned = len(uids)
                if not uids:
                    log.info("No candidate emails found")
                    return stats

                # Skip already-finalized emails to keep work incremental.
                fresh: list[int] = []
                for uid in uids:
                    if not self.db.is_email_processed(str(uid)):
                        fresh.append(uid)
                log.info(f"{len(fresh)}/{len(uids)} emails are new (rest already processed)")

                for fetched in client.fetch(fresh, save_attachments=True):
                    self._process_email(fetched, stats)
                    if MARK_AS_READ:
                        try:
                            client.mark_as_read(int(fetched.uid))
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            log.exception(f"run_once aborted: {e}")
            stats.aborted = True
        log.info(
            f"Run complete: scanned={stats.emails_scanned} succeeded={stats.emails_succeeded} "
            f"failed={stats.emails_failed} ignored={stats.emails_ignored} "
            f"nav_inserted={stats.nav_inserted} nav_updated={stats.nav_updated}"
        )
        return stats

    def daemon(self, interval_min: int = POLL_INTERVAL_MIN) -> None:
        log.info(f"Daemon started, interval={interval_min} min")
        while True:
            try:
                self.run_once()
            except Exception as e:
                log.exception(f"Daemon iteration error: {e}")
            sleep_s = max(60, interval_min * 60)
            log.info(f"Sleeping {sleep_s}s")
            time.sleep(sleep_s)

    def retry_failed(self) -> RunStats:
        stats = RunStats()
        failed = self.db.get_failed_emails()
        if not failed:
            log.info("No failed emails to retry")
            return stats
        uids_int: list[int] = []
        for f in failed:
            try:
                uids_int.append(int(f["email_uid"]))
            except (TypeError, ValueError):
                continue
        log.info(f"Retrying {len(uids_int)} failed emails")
        with EmailClient() as client:
            for fetched in client.fetch(uids_int, save_attachments=True):
                self._process_email(fetched, stats)
        return stats

    # --------------------------------------------------------------- private
    def _process_email(self, fetched: FetchedEmail, stats: RunStats) -> None:
        uid = fetched.uid
        log.info(f"[UID={uid}] subject={fetched.subject!r} from={fetched.sender!r}")

        if not is_nav_email(fetched.subject, fetched.body_text):
            log.info(f"[UID={uid}] not a NAV email → ignored")
            self.db.log_email(
                email_uid=uid,
                subject=fetched.subject,
                sender=fetched.sender,
                received_at=fetched.received_at,
                status="ignored",
                records_count=0,
            )
            stats.emails_ignored += 1
            return

        stats.emails_matched += 1

        # 1) Collect candidate NAV records: attachments first, then bodies.
        all_records: list[NavRecord] = []
        for att in fetched.attachments:
            recs = parse_attachment(att.filename, att.content)
            if recs:
                log.info(f"[UID={uid}] attachment {att.filename} → {len(recs)} candidates")
                all_records.extend(recs)
            else:
                log.debug(f"[UID={uid}] attachment {att.filename} produced 0 records")

        if not all_records and fetched.body_html:
            recs = parse_html_body(fetched.body_html)
            log.info(f"[UID={uid}] html body → {len(recs)} candidates")
            all_records.extend(recs)

        if not all_records and fetched.body_text:
            recs = parse_text_body(fetched.body_text)
            log.info(f"[UID={uid}] text body → {len(recs)} candidates")
            all_records.extend(recs)

        all_records = deduplicate(all_records)

        if not all_records:
            log.warning(f"[UID={uid}] no parsable NAV data")
            self.db.log_email(
                email_uid=uid,
                subject=fetched.subject,
                sender=fetched.sender,
                received_at=fetched.received_at,
                status="no_data",
                records_count=0,
                raw_snapshot=_snapshot(fetched),
            )
            return

        # 2) Match against tracked funds and upsert.
        inserted = updated = 0
        unmatched: list[str] = []
        errors: list[str] = []
        for rec in all_records:
            try:
                fund_id = self.matcher.match(rec.fund_name)
                if fund_id is None:
                    unmatched.append(rec.fund_name)
                    continue
                ins, upd = self.db.upsert_nav(
                    fund_id=fund_id,
                    nav_date=rec.nav_date,
                    unit_nav=rec.unit_nav,
                    cumulative_nav=rec.cumulative_nav,
                    source_email_id=uid,
                    source_filename=rec.source_filename,
                    raw_data=rec.to_dict(),
                )
                inserted += int(ins)
                updated += int(upd)
            except Exception as e:
                errors.append(f"{rec.fund_name}@{rec.nav_date}: {e}")
                log.exception(f"[UID={uid}] upsert failed for {rec.fund_name}: {e}")

        stats.nav_inserted += inserted
        stats.nav_updated += updated

        # 3) Outcome bookkeeping.
        snapshot_payload = {
            "unmatched_names": sorted(set(unmatched))[:200],
            "errors": errors[:50],
            "candidate_count": len(all_records),
        }
        if inserted == 0 and updated == 0 and (unmatched or errors):
            status = "failed" if errors else "no_data"
            err = "; ".join(errors) if errors else None
            self.db.log_email(
                email_uid=uid,
                subject=fetched.subject,
                sender=fetched.sender,
                received_at=fetched.received_at,
                status=status,
                error_message=err,
                records_count=0,
                raw_snapshot=json.dumps(snapshot_payload, ensure_ascii=False),
            )
            stats.emails_failed += int(status == "failed")
            log.warning(
                f"[UID={uid}] status={status}, unmatched={len(unmatched)} errors={len(errors)}"
            )
            return

        self.db.log_email(
            email_uid=uid,
            subject=fetched.subject,
            sender=fetched.sender,
            received_at=fetched.received_at,
            status="success",
            error_message="; ".join(errors) if errors else None,
            records_count=inserted + updated,
            raw_snapshot=json.dumps(snapshot_payload, ensure_ascii=False) if (unmatched or errors) else None,
        )
        stats.emails_succeeded += 1
        log.info(
            f"[UID={uid}] success: inserted={inserted} updated={updated} "
            f"unmatched={len(unmatched)} errors={len(errors)}"
        )


def _snapshot(fetched: FetchedEmail) -> str:
    payload = {
        "subject": fetched.subject,
        "sender": fetched.sender,
        "received_at": fetched.received_at.isoformat() if isinstance(fetched.received_at, datetime) else None,
        "attachments": [a.filename for a in fetched.attachments],
        "body_text_preview": (fetched.body_text or "")[:2000],
    }
    return json.dumps(payload, ensure_ascii=False)

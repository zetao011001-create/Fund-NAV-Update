"""One-shot backfill: populate nav_history.cumulative_nav from saved attachments.

Walks every nav_history row whose cumulative_nav is NULL, finds the original
attachment file (resolved against source_filename or source_email_id), re-parses
it, and updates the row when a matching (fund, date) record carries a
cumulative_nav.

Idempotent: rerunnable; only fills NULL cells.

Usage:
    python -m scripts.backfill_cumulative [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Allow running as `python scripts/backfill_cumulative.py` from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update

from src.config import ATTACHMENT_DIR
from src.database import DatabaseManager, Fund, NavHistory
from src.email_client import _safe_filename
from src.logger import log
from src.parser import parse_attachment


def _attachment_path(source_filename: Optional[str], source_email_id: Optional[str]) -> Optional[Path]:
    """Locate the saved attachment for a NAV row.

    The email_client saves attachments as `_safe_filename(<UID>_<filename>)`
    under ATTACHMENT_DIR, which substitutes characters like '~' with '_'.
    parser.py stamps source_filename like 'orig_filename#sheet'.
    """
    if not source_filename:
        return None
    base = source_filename.split("#", 1)[0]
    if not base:
        return None
    if source_email_id:
        safe = _safe_filename(f"{source_email_id}_{base}")
        candidate = ATTACHMENT_DIR / safe
        if candidate.exists():
            return candidate
        # Fallback: ignore UID prefix (rare, but safe).
        for path in ATTACHMENT_DIR.glob(f"{source_email_id}_*"):
            if path.is_file():
                return path
    matches = list(ATTACHMENT_DIR.glob(f"*_{_safe_filename(base)}"))
    if matches:
        return matches[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report only; don't write.")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N rows scanned (0 = no limit).")
    args = ap.parse_args()

    db = DatabaseManager()

    with db.session() as s:
        rows = s.execute(
            select(
                NavHistory.id,
                NavHistory.fund_id,
                NavHistory.nav_date,
                NavHistory.source_filename,
                NavHistory.source_email_id,
                Fund.fund_name,
            )
            .join(Fund, Fund.id == NavHistory.fund_id)
            .where(NavHistory.cumulative_nav.is_(None))
        ).all()

    log.info(f"Found {len(rows)} rows with NULL cumulative_nav")
    if args.limit:
        rows = rows[: args.limit]

    # Group rows by attachment so we parse each file once.
    by_path: dict[Path, list[tuple[int, int, object, str]]] = defaultdict(list)
    missing_path = 0
    for nav_id, fund_id, nav_date, src_file, src_uid, fund_name in rows:
        path = _attachment_path(src_file, src_uid)
        if path is None:
            missing_path += 1
            continue
        by_path[path].append((nav_id, fund_id, nav_date, fund_name))

    log.info(f"Resolved {sum(len(v) for v in by_path.values())} rows across {len(by_path)} attachment files; "
             f"{missing_path} rows had no findable attachment")

    filled = 0
    no_value = 0
    for path, items in by_path.items():
        try:
            content = path.read_bytes()
        except OSError as e:
            log.warning(f"Cannot read {path}: {e}")
            continue
        records = parse_attachment(path.name, content)
        if not records:
            continue
        # Index parsed records by (fund_name_substring, date) → cumulative_nav.
        # Use prefix substring matching: parser may emit longer fund names.
        index: dict[tuple[str, object], object] = {}
        for r in records:
            if r.cumulative_nav is None:
                continue
            index[(r.fund_name, r.nav_date)] = r.cumulative_nav

        for nav_id, fund_id, nav_date, fund_name in items:
            cum = None
            # 1) exact name match
            cum = index.get((fund_name, nav_date))
            # 2) any record with same date whose name contains the tracked name
            if cum is None:
                for (rname, rdate), rcum in index.items():
                    if rdate == nav_date and (fund_name in rname or rname in fund_name):
                        cum = rcum
                        break
            if cum is None:
                no_value += 1
                continue
            if args.dry_run:
                filled += 1
                continue
            with db.session() as s:
                s.execute(
                    update(NavHistory)
                    .where(NavHistory.id == nav_id)
                    .values(cumulative_nav=cum)
                )
            filled += 1

    print("=== BACKFILL SUMMARY ===")
    print(f"  rows_scanned        : {len(rows)}")
    print(f"  attachments_used    : {len(by_path)}")
    print(f"  rows_missing_path   : {missing_path}")
    print(f"  rows_filled         : {filled}{'  (DRY RUN)' if args.dry_run else ''}")
    print(f"  rows_no_value       : {no_value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

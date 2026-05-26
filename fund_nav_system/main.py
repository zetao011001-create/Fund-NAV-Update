"""CLI entrypoint for the fund-NAV automation system."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Optional

import click
import pandas as pd

from src.database import DatabaseManager
from src.logger import log
from src.query_api import (
    export_to_excel,
    get_fund_list,
    get_latest_nav_all,
    query_nav,
)
from src.sync_engine import SyncEngine


@click.group(help="Fund NAV automated collection system.")
def cli() -> None:
    pass


@cli.command("init-db", help="Create tables and seed the 21 preset tracked funds.")
def init_db_cmd() -> None:
    from init_db import main as init_main

    init_main()


@cli.command("run-once", help="Single pass: fetch new emails, parse, store.")
@click.option("--since-days", type=int, default=None, help="Look back N days (default: env EMAIL_LOOKBACK_DAYS)")
@click.option("--only-unseen", is_flag=True, help="Restrict to UNSEEN emails")
def run_once_cmd(since_days: Optional[int], only_unseen: bool) -> None:
    engine = SyncEngine()
    kwargs = {}
    if since_days is not None:
        kwargs["since_days"] = since_days
    kwargs["only_unseen"] = only_unseen
    stats = engine.run_once(**kwargs)
    click.echo(_format_stats(stats))
    if stats.aborted:
        sys.exit(1)


@cli.command("daemon", help="Long-running poller (default 30 min interval).")
@click.option("--interval", type=int, default=None, help="Poll interval in minutes")
def daemon_cmd(interval: Optional[int]) -> None:
    engine = SyncEngine()
    if interval is not None:
        engine.daemon(interval_min=interval)
    else:
        engine.daemon()


@cli.command("retry-failed", help="Re-process all emails currently marked as failed.")
def retry_failed_cmd() -> None:
    engine = SyncEngine()
    stats = engine.retry_failed()
    click.echo(_format_stats(stats))


@cli.command("query", help="Query NAV history.")
@click.option("--fund", default=None, help="Fund name (substring match)")
@click.option("--start", default=None, help="Start date YYYY-MM-DD")
@click.option("--end", default=None, help="End date YYYY-MM-DD")
@click.option("--limit", type=int, default=50)
def query_cmd(fund: Optional[str], start: Optional[str], end: Optional[str], limit: int) -> None:
    df = query_nav(fund_name=fund, start_date=start, end_date=end)
    if df.empty:
        click.echo("(no records)")
        return
    out = df.head(limit).to_string(index=False)
    click.echo(out)
    click.echo(f"\nTotal rows: {len(df)} (showing {min(limit, len(df))})")


@cli.command("latest", help="Show the latest NAV for every fund.")
def latest_cmd() -> None:
    df = get_latest_nav_all()
    if df.empty:
        click.echo("(no NAV data yet)")
        return
    click.echo(df.to_string(index=False))


@cli.command("export", help="Export NAVs to Excel (one sheet per category).")
@click.option("--start", default=None)
@click.option("--end", default=None)
@click.option("--output", default=None, help="Output .xlsx path")
def export_cmd(start: Optional[str], end: Optional[str], output: Optional[str]) -> None:
    path = export_to_excel(start_date=start, end_date=end, output_path=output)
    click.echo(f"Exported to: {path}")


@cli.command("list-funds", help="List tracked funds.")
@click.option("--category", default=None)
@click.option("--manager", default=None)
def list_funds_cmd(category: Optional[str], manager: Optional[str]) -> None:
    df = get_fund_list(category=category, manager=manager)
    if df.empty:
        click.echo("(no funds)")
        return
    cols = ["id", "fund_name", "manager", "strategy", "category", "is_tracking"]
    cols = [c for c in cols if c in df.columns]
    click.echo(df[cols].to_string(index=False))


@cli.command("add-fund", help="Add (or upsert) a tracked fund.")
@click.argument("fund_name")
@click.option("--manager", default=None)
@click.option("--strategy", default=None)
@click.option("--category", default=None)
@click.option("--short-name", default=None)
def add_fund_cmd(
    fund_name: str,
    manager: Optional[str],
    strategy: Optional[str],
    category: Optional[str],
    short_name: Optional[str],
) -> None:
    db = DatabaseManager()
    fid = db.add_fund(
        fund_name=fund_name,
        manager=manager,
        strategy=strategy,
        category=category,
        short_name=short_name,
        is_tracking=True,
    )
    click.echo(f"Fund upserted: id={fid} name={fund_name}")


@cli.command("update-fund", help="Update fields on an existing fund.")
@click.argument("fund_name")
@click.option("--manager", default=None)
@click.option("--strategy", default=None)
@click.option("--category", default=None)
@click.option("--short-name", default=None)
@click.option("--tracking/--no-tracking", default=None)
def update_fund_cmd(
    fund_name: str,
    manager: Optional[str],
    strategy: Optional[str],
    category: Optional[str],
    short_name: Optional[str],
    tracking: Optional[bool],
) -> None:
    db = DatabaseManager()
    fields = {
        "manager": manager,
        "strategy": strategy,
        "category": category,
        "short_name": short_name,
        "is_tracking": tracking,
    }
    ok = db.update_fund(fund_name, **{k: v for k, v in fields.items() if v is not None})
    if not ok:
        click.echo(f"Fund not found: {fund_name}")
        sys.exit(1)
    click.echo(f"Updated: {fund_name}")


@cli.command("status", help="Show system status.")
def status_cmd() -> None:
    db = DatabaseManager()
    summary = db.get_status_summary()
    click.echo("=== SYSTEM STATUS ===")
    for k, v in summary.items():
        click.echo(f"  {k}: {v}")


def _format_stats(stats) -> str:
    return (
        f"emails_scanned   : {stats.emails_scanned}\n"
        f"emails_matched   : {stats.emails_matched}\n"
        f"emails_succeeded : {stats.emails_succeeded}\n"
        f"emails_failed    : {stats.emails_failed}\n"
        f"emails_ignored   : {stats.emails_ignored}\n"
        f"nav_inserted     : {stats.nav_inserted}\n"
        f"nav_updated      : {stats.nav_updated}"
    )


if __name__ == "__main__":
    cli()

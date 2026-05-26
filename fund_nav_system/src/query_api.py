"""Lightweight read-side query API. Returns pandas DataFrame or dict.

Stays free of any web framework so it can be wrapped later.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import EXPORT_DIR
from .database import DatabaseManager
from .logger import log

_db: Optional[DatabaseManager] = None


def _get_db() -> DatabaseManager:
    global _db
    if _db is None:
        _db = DatabaseManager()
    return _db


def query_nav(
    fund_name: Optional[str] = None,
    start_date: Optional[date | str] = None,
    end_date: Optional[date | str] = None,
) -> pd.DataFrame:
    rows = _get_db().get_nav_history(
        fund_name=fund_name,
        start_date=_coerce_date(start_date),
        end_date=_coerce_date(end_date),
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["nav_date"] = pd.to_datetime(df["nav_date"])
    return df


def get_latest_nav_all() -> pd.DataFrame:
    rows = _get_db().get_latest_nav()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["nav_date"] = pd.to_datetime(df["nav_date"])
    return df


def get_fund_list(category: Optional[str] = None, manager: Optional[str] = None) -> pd.DataFrame:
    rows = _get_db().get_all_funds()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if category:
        df = df[df["category"].fillna("") == category]
    if manager:
        df = df[df["manager"].fillna("") == manager]
    return df.reset_index(drop=True)


def get_nav_series(fund_name: str) -> pd.Series:
    """Return a date-indexed series of unit_nav for one fund."""
    df = query_nav(fund_name=fund_name)
    if df.empty:
        return pd.Series(dtype="float64", name=fund_name)
    df = df[df["fund_name"] == fund_name] if (df["fund_name"] == fund_name).any() else df
    return pd.Series(
        df["unit_nav"].values, index=pd.DatetimeIndex(df["nav_date"]), name=fund_name
    ).sort_index()


def export_to_excel(
    fund_names: Optional[list[str]] = None,
    start_date: Optional[date | str] = None,
    end_date: Optional[date | str] = None,
    output_path: Optional[Path | str] = None,
) -> Path:
    """Wide-format export grouped by category. One sheet per category.

    Each sheet: column A = nav_date, then per fund two columns side-by-side:
    '<基金名>-单位净值' and '<基金名>-累计净值'. Funds with no NAV data still
    get their two empty columns to keep the layout stable.
    """
    db = _get_db()
    funds = pd.DataFrame(db.get_all_funds())
    if funds.empty:
        raise RuntimeError("No funds in database")

    if fund_names:
        funds = funds[funds["fund_name"].isin(fund_names)]

    rows = db.get_nav_history(start_date=_coerce_date(start_date), end_date=_coerce_date(end_date))
    nav_df = pd.DataFrame(rows)
    if nav_df.empty:
        log.warning("export_to_excel: no NAV records")
    else:
        nav_df["nav_date"] = pd.to_datetime(nav_df["nav_date"])

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = EXPORT_DIR / f"nav_export_{ts}.xlsx"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    categories = sorted(funds["category"].fillna("未分类").unique())
    with pd.ExcelWriter(output_path, engine="openpyxl", datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as writer:
        for cat in categories:
            cat_funds = funds[funds["category"].fillna("未分类") == cat]["fund_name"].tolist()
            if not cat_funds:
                continue
            wide = _build_category_sheet(nav_df, cat_funds)
            sheet_name = cat[:31] or "未分类"
            wide.to_excel(writer, sheet_name=sheet_name, index=False)
    log.info(f"Exported NAVs to {output_path}")
    return output_path


_UNIT_SUFFIX = "-单位净值"
_CUM_SUFFIX = "-累计净值"


def _build_category_sheet(nav_df: pd.DataFrame, cat_funds: list[str]) -> pd.DataFrame:
    """Build the wide layout for one category sheet.

    Columns: nav_date, then for each fund two columns (unit, cumulative).
    """
    paired_cols: list[str] = []
    for fn in cat_funds:
        paired_cols.append(f"{fn}{_UNIT_SUFFIX}")
        paired_cols.append(f"{fn}{_CUM_SUFFIX}")

    if nav_df.empty:
        return pd.DataFrame(columns=["nav_date"] + paired_cols)

    sub = nav_df[nav_df["fund_name"].isin(cat_funds)]
    if sub.empty:
        return pd.DataFrame(columns=["nav_date"] + paired_cols)

    unit_wide = sub.pivot_table(
        index="nav_date", columns="fund_name", values="unit_nav", aggfunc="last"
    )
    if "cumulative_nav" in sub.columns:
        cum_wide = sub.pivot_table(
            index="nav_date", columns="fund_name", values="cumulative_nav", aggfunc="last"
        )
    else:
        cum_wide = pd.DataFrame(index=unit_wide.index)

    out = pd.DataFrame(index=unit_wide.index)
    for fn in cat_funds:
        out[f"{fn}{_UNIT_SUFFIX}"] = unit_wide[fn] if fn in unit_wide.columns else pd.NA
        out[f"{fn}{_CUM_SUFFIX}"] = cum_wide[fn] if fn in cum_wide.columns else pd.NA

    out = out.sort_index().reset_index()
    out["nav_date"] = pd.to_datetime(out["nav_date"]).dt.strftime("%Y-%m-%d")
    return out


def _coerce_date(value: Optional[date | str]) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value if not isinstance(value, datetime) else value.date()
    s = str(value).strip()
    if not s:
        return None
    return pd.to_datetime(s).date()

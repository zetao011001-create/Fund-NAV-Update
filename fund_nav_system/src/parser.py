"""Parse NAV data from Excel/CSV attachments and HTML/text email bodies.

Core logic: WIDE table (date column + one column per fund). Vertical (long)
tables are also supported as a fallback.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd
from bs4 import BeautifulSoup

from .config import COLUMN_ALIASES, GARBAGE_COLUMN_KEYWORDS, KV_LABELS
from .logger import log


@dataclass
class NavRecord:
    fund_name: str
    nav_date: date
    unit_nav: Decimal
    cumulative_nav: Optional[Decimal] = None
    source_filename: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_name": self.fund_name,
            "nav_date": self.nav_date.isoformat(),
            "unit_nav": float(self.unit_nav),
            "cumulative_nav": float(self.cumulative_nav) if self.cumulative_nav is not None else None,
            "source_filename": self.source_filename,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_attachment(filename: str, content: bytes) -> list[NavRecord]:
    """Parse an attachment by extension; returns NAV records (unfiltered by tracker)."""
    name = filename.lower()
    try:
        if name.endswith(".xlsx") or name.endswith(".xlsm"):
            return _parse_excel(content, filename, engine="openpyxl")
        if name.endswith(".xls"):
            return _parse_excel(content, filename, engine="xlrd")
        if name.endswith(".csv"):
            return _parse_csv(content, filename)
    except Exception as e:
        log.warning(f"parse_attachment({filename}) failed: {e}")
    return []


def parse_html_body(html: str) -> list[NavRecord]:
    if not html:
        return []
    try:
        tables = pd.read_html(io.StringIO(html))
    except (ValueError, ImportError) as e:
        log.debug(f"pd.read_html failed: {e}; falling back to BS4")
        tables = _bs4_extract_tables(html)
    out: list[NavRecord] = []
    for df in tables:
        out.extend(_parse_dataframe(df, source="email_html"))
    return out


def parse_text_body(text: str) -> list[NavRecord]:
    if not text or not text.strip():
        return []
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[\t\|,，；;]+|\s{2,}", line)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            rows.append(parts)
    if len(rows) < 2:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    df = pd.DataFrame(rows[1:], columns=rows[0])
    return _parse_dataframe(df, source="email_text")


# ---------------------------------------------------------------------------
# Internal: file readers
# ---------------------------------------------------------------------------
def _parse_excel(content: bytes, filename: str, engine: str) -> list[NavRecord]:
    out: list[NavRecord] = []
    try:
        xls = pd.ExcelFile(io.BytesIO(content), engine=engine)
    except Exception as e:
        log.warning(f"Cannot open Excel {filename}: {e}")
        return out
    for sheet in xls.sheet_names:
        sheet_recs: list[NavRecord] = []
        wide_fallback: list[NavRecord] = []
        for header_row in (0, 1, 2):
            try:
                df = xls.parse(sheet_name=sheet, header=header_row)
            except Exception:
                continue
            if df.empty or df.shape[1] < 2:
                continue
            df_clean = df.copy()
            df_clean.columns = [_clean_col(c) for c in df_clean.columns]
            if _looks_vertical(df_clean):
                vrecs = _parse_vertical(df_clean, source=f"{filename}#{sheet}")
                if vrecs:
                    sheet_recs = vrecs
                    break
            if not wide_fallback:
                wrecs = _parse_dataframe(df, source=f"{filename}#{sheet}")
                if wrecs:
                    wide_fallback = wrecs
        if not sheet_recs:
            sheet_recs = wide_fallback

        # Multi-section sweep — works for stacked sub-tables (e.g. 守心6号 双段表).
        if not sheet_recs:
            try:
                raw = xls.parse(sheet_name=sheet, header=None)
                sheet_recs = _parse_multisection(raw, source=f"{filename}#{sheet}")
            except Exception:
                pass

        # KV announcement fallback.
        if not sheet_recs:
            try:
                raw = xls.parse(sheet_name=sheet, header=None)
                sheet_recs = _parse_kv_announcement(raw, source=f"{filename}#{sheet}")
            except Exception:
                pass
        out.extend(sheet_recs)
    return out


def _parse_multisection(raw: pd.DataFrame, source: str) -> list[NavRecord]:
    """Sheet may contain multiple sub-tables stacked vertically (band tables).

    Walk every row; treat it as the header of a potential subtable formed by the
    rows below it (stopping at the next blank line). Pick the first band that
    yields records via the normal vertical/wide pipeline.
    """
    if raw is None or raw.empty:
        return []

    n_rows = len(raw)
    i = 0
    while i < n_rows:
        header = raw.iloc[i].tolist()
        non_null = sum(1 for c in header if c is not None and not (isinstance(c, float) and pd.isna(c)))
        if non_null < 2:
            i += 1
            continue

        # Collect rows below until a blank row or EOF.
        j = i + 1
        while j < n_rows:
            row = raw.iloc[j].tolist()
            if all(c is None or (isinstance(c, float) and pd.isna(c)) for c in row):
                break
            j += 1
        if j - (i + 1) < 1:
            i = j + 1
            continue

        sub = raw.iloc[i + 1 : j].copy()
        sub.columns = [_clean_col(c) for c in header]
        # Drop columns whose header is blank or "Unnamed".
        sub = sub.loc[:, [c for c in sub.columns if c and not c.startswith("Unnamed")]]
        if sub.shape[1] < 2:
            i = j + 1
            continue

        recs = _parse_dataframe(sub, source=source)
        if recs:
            return recs
        i = j + 1

    return []


def _parse_csv(content: bytes, filename: str) -> list[NavRecord]:
    encodings = ("utf-8-sig", "utf-8", "gbk", "gb18030")
    df: Optional[pd.DataFrame] = None
    for enc in encodings:
        try:
            df = pd.read_csv(io.BytesIO(content), encoding=enc)
            break
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    if df is None or df.empty:
        return []
    return _parse_dataframe(df, source=filename)


def _bs4_extract_tables(html: str) -> list[pd.DataFrame]:
    soup = BeautifulSoup(html, "lxml")
    out: list[pd.DataFrame] = []
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if len(rows) < 2:
            continue
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out.append(pd.DataFrame(rows[1:], columns=rows[0]))
    return out


# ---------------------------------------------------------------------------
# Internal: DataFrame dispatcher
# ---------------------------------------------------------------------------
def _parse_dataframe(df: pd.DataFrame, source: str) -> list[NavRecord]:
    if df is None or df.empty or df.shape[1] < 2:
        return []
    df = df.copy()
    df.columns = [_clean_col(c) for c in df.columns]

    if _looks_vertical(df):
        recs = _parse_vertical(df, source)
        if recs:
            return recs

    return _parse_wide(df, source)


def _looks_vertical(df: pd.DataFrame) -> bool:
    cols = {str(c) for c in df.columns}
    has_date = any(alias in cols for alias in COLUMN_ALIASES["date"])
    has_name = any(alias in cols for alias in COLUMN_ALIASES["fund_name"])
    has_nav = any(alias in cols for alias in COLUMN_ALIASES["unit_nav"])
    return has_date and has_name and has_nav


def _parse_vertical(df: pd.DataFrame, source: str) -> list[NavRecord]:
    date_col = _find_alias_col(df, "date")
    name_col = _find_alias_col(df, "fund_name")
    nav_col = _find_alias_col(df, "unit_nav")
    cum_col = _find_alias_col(df, "cumulative_nav")
    if not (date_col and name_col and nav_col):
        return []

    out: list[NavRecord] = []
    for _, row in df.iterrows():
        d = _to_date(row.get(date_col))
        n = _to_decimal(row.get(nav_col))
        nm = str(row.get(name_col) or "").strip()
        if d and n is not None and nm and not _is_garbage_fund_name(nm):
            cum = _to_decimal(row.get(cum_col)) if cum_col else None
            out.append(
                NavRecord(
                    fund_name=nm,
                    nav_date=d,
                    unit_nav=n,
                    cumulative_nav=cum,
                    source_filename=source,
                )
            )
    return out


def _parse_wide(df: pd.DataFrame, source: str) -> list[NavRecord]:
    """First column is date; remaining columns are fund names → values are NAVs."""
    date_col = _find_alias_col(df, "date") or df.columns[0]

    # Fund-name columns: every column except the date column, with garbage filtered.
    fund_cols: list[str] = []
    for col in df.columns:
        if col == date_col:
            continue
        c = str(col).strip()
        if not c or c.startswith("Unnamed"):
            continue
        if _is_garbage_fund_name(c):
            continue
        fund_cols.append(col)

    if not fund_cols:
        return []

    out: list[NavRecord] = []
    for _, row in df.iterrows():
        d = _to_date(row.get(date_col))
        if not d:
            continue
        for col in fund_cols:
            n = _to_decimal(row.get(col))
            if n is None:
                continue
            fund_name = str(col).strip()
            if not fund_name or _is_garbage_fund_name(fund_name):
                continue
            out.append(NavRecord(fund_name=fund_name, nav_date=d, unit_nav=n, source_filename=source))
    return out


# ---------------------------------------------------------------------------
# Key-value announcement parser
# ---------------------------------------------------------------------------
def _parse_kv_announcement(raw: pd.DataFrame, source: str) -> list[NavRecord]:
    """Some custodians emit one fact per row: '基金名称 | XXX', '基金份额净值 | 1.23'."""
    if raw is None or raw.empty:
        return []
    fund_name: Optional[str] = None
    nav_value: Optional[Decimal] = None
    cum_value: Optional[Decimal] = None
    nav_date: Optional[date] = None

    flat: list[tuple[str, Any]] = []
    for _, row in raw.iterrows():
        cells = [c for c in row.tolist() if c is not None and not (isinstance(c, float) and pd.isna(c))]
        if len(cells) < 2:
            continue
        label = str(cells[0]).strip().rstrip("：:").strip()
        value = cells[1]
        flat.append((label, value))

    for label, value in flat:
        if fund_name is None and any(label == lab or lab in label for lab in KV_LABELS["fund_name"]):
            fund_name = str(value).strip()
        elif cum_value is None and any(label == lab or lab in label for lab in KV_LABELS["cumulative_nav"]):
            cum_value = _to_decimal(value)
        elif nav_value is None and any(label == lab or lab in label for lab in KV_LABELS["unit_nav"]):
            nav_value = _to_decimal(value)
        elif nav_date is None and any(label == lab or lab in label for lab in KV_LABELS["nav_date"]):
            nav_date = _to_date(value)

    if fund_name and nav_value is not None:
        if nav_date is None:
            nav_date = _infer_date_from_text(source)
        if nav_date:
            return [
                NavRecord(
                    fund_name=fund_name,
                    nav_date=nav_date,
                    unit_nav=nav_value,
                    cumulative_nav=cum_value,
                    source_filename=source,
                )
            ]
    return []


_DATE_IN_TEXT_RE = re.compile(r"(20\d{2})[.\-/年]?(\d{1,2})[.\-/月]?(\d{1,2})")


def _infer_date_from_text(text: str) -> Optional[date]:
    """Last-resort: pull a YYYYMMDD-ish date out of a filename or sheet path."""
    if not text:
        return None
    m = _DATE_IN_TEXT_RE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean_col(c: Any) -> str:
    s = str(c).strip()
    s = re.sub(r"\s+", "", s)
    return s


def _find_alias_col(df: pd.DataFrame, key: str) -> Optional[str]:
    aliases = COLUMN_ALIASES[key]
    for col in df.columns:
        if col in aliases:
            return col
    return None


def _is_garbage_text(s: str) -> bool:
    """Used for COLUMN names — substring match against garbage keywords."""
    if not s:
        return True
    low = s.lower()
    for kw in GARBAGE_COLUMN_KEYWORDS:
        if kw.lower() in low:
            return True
    return False


_GARBAGE_NAME_EXACT = {
    "合计", "总计", "sum", "total", "备注", "说明",
    "基金代码", "产品代码", "基金账号", "客户名称",
    "期初单位净值", "期末单位净值", "期初累计净值", "期末累计净值",
    "期末累计单位净值",
    "虚拟净值提取前单位净值", "虚拟净值提取后单位净值",
    "虚拟净值提取前累计单位净值", "虚拟净值提取后累计单位净值",
    "虚拟净值", "虚拟单位净值", "单位净值", "累计净值",
    "累计单位净值", "累积净值", "累积单位净值",
    "资产份额净值(元)", "资产份额累计净值(元)",
    "资产份额净值", "资产份额累计净值", "基金份额净值", "基金份额累计净值",
    "母基金单位净值", "母基金累计单位净值",
    "投资者占比", "份额余额", "客户净资产", "业绩提成",
    "估值日期", "净值日期", "期初日期", "期末日期",
    "参与计提份额", "虚拟计提金额", "持有人虚拟参考市值", "产品净值规模",
}


def _is_garbage_fund_name(s: str) -> bool:
    """Stricter check used for fund-NAME values: exact match against known labels."""
    if not s:
        return True
    s2 = s.strip()
    if s2 in _GARBAGE_NAME_EXACT:
        return True
    # All-digits or all-punctuation isn't a fund name.
    if re.fullmatch(r"[\d.\-/]+", s2):
        return True
    return False


_DATE_RE_NUMERIC = re.compile(r"^\s*(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})日?\s*$")


def _to_date(value: Any) -> Optional[date]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, (int, float)):
        n = int(value)
        # YYYYMMDD-as-integer (e.g. 20250929) — common in custodian Excel.
        if 19000101 <= n <= 21001231:
            try:
                return date(n // 10000, (n // 100) % 100, n % 100)
            except ValueError:
                return None
        # Excel serial number (days since 1899-12-30): roughly 1..80000.
        if 1 <= float(value) <= 80000:
            try:
                return (datetime(1899, 12, 30) + pd.Timedelta(days=float(value))).date()
            except (OverflowError, ValueError):
                return None
        return None
    s = str(value).strip()
    if not s:
        return None
    # Pure 8-digit YYYYMMDD string.
    if re.fullmatch(r"\d{8}", s):
        try:
            return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    m = _DATE_RE_NUMERIC.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    try:
        ts = pd.to_datetime(s, errors="raise")
        return ts.date() if not pd.isna(ts) else None
    except (ValueError, TypeError):
        return None


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            return None
    else:
        s = str(value).strip().replace(",", "").replace("，", "").replace("%", "")
        if not s or s in {"-", "—", "N/A", "n/a", "NA", "/", "无", "暂无"}:
            return None
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None
    # Plausible NAV range: roughly 0.1 ~ 100.
    if d <= 0 or d > 1000:
        return None
    return d.quantize(Decimal("0.0001"))


def deduplicate(records: Iterable[NavRecord]) -> list[NavRecord]:
    """Merge entries with the same (fund_name, nav_date).

    Later writes win for unit_nav; cumulative_nav is back-filled from any
    member that has it so we don't drop a value when one source happens to
    lack it.
    """
    table: dict[tuple[str, date], NavRecord] = {}
    for r in records:
        key = (r.fund_name, r.nav_date)
        prev = table.get(key)
        if prev is None:
            table[key] = r
            continue
        merged_cum = r.cumulative_nav if r.cumulative_nav is not None else prev.cumulative_nav
        table[key] = NavRecord(
            fund_name=r.fund_name,
            nav_date=r.nav_date,
            unit_nav=r.unit_nav,
            cumulative_nav=merged_cum,
            source_filename=r.source_filename or prev.source_filename,
        )
    return list(table.values())

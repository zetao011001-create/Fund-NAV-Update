"""Configuration center. Loads .env and exposes constants.

Supports two runtime modes:

  * Source checkout (dev): paths anchor to the project root.
  * Frozen exe (PyInstaller): bundled resources live in `sys._MEIPASS`,
    while writable data (DB, logs, attachments) goes under a per-user
    app-data directory so it survives reinstalls.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Final, Optional

from dotenv import load_dotenv

_IS_FROZEN: Final[bool] = bool(getattr(sys, "frozen", False))

if _IS_FROZEN:
    # PyInstaller extracts the bundle to _MEIPASS (read-only).
    PROJECT_ROOT: Final[Path] = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    if sys.platform == "win32":
        _user_root: Path = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        ) / "FundNAV"
    elif sys.platform == "darwin":
        _user_root = Path.home() / "Library" / "Application Support" / "FundNAV"
    else:
        _user_root = Path.home() / ".fund_nav"
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _user_root = PROJECT_ROOT

ENV_PATH: Final[Path] = PROJECT_ROOT / ".env"
LOG_DIR: Final[Path] = _user_root / "logs"
DATA_DIR: Final[Path] = _user_root / "data"
ATTACHMENT_DIR: Final[Path] = DATA_DIR / "attachments"
EXPORT_DIR: Final[Path] = DATA_DIR / "exports"

for _p in (LOG_DIR, DATA_DIR, ATTACHMENT_DIR, EXPORT_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# First-run seeding: copy bundled DB into the writable user dir.
if _IS_FROZEN:
    _db_target = DATA_DIR / "fund_nav.db"
    if not _db_target.exists():
        _db_seed = PROJECT_ROOT / "dist_seed" / "fund_nav.db"
        if _db_seed.exists():
            shutil.copy(_db_seed, _db_target)

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)


def _get_env(key: str, default: Optional[str] = None) -> str:
    return os.getenv(key, default) or ""


# ---------- Email / IMAP ----------
EMAIL_ADDRESS: Final[str] = _get_env("EMAIL_ADDRESS", "datareception@ansurefo.com")
IMAP_SERVER: Final[str] = _get_env("IMAP_SERVER", "imap.qiye.aliyun.com")
IMAP_PORT: Final[int] = int(_get_env("IMAP_PORT", "993"))


def get_email_password() -> str:
    pw = os.getenv("EMAIL_PASSWORD", "")
    if pw:
        return pw
    raise RuntimeError(
        "EMAIL_PASSWORD is not configured. Add EMAIL_PASSWORD=... to .env."
    )


# ---------- Database ----------
_db_url_raw: Final[str] = _get_env("DATABASE_URL", "")
if _db_url_raw.startswith("sqlite:///") and not _db_url_raw.startswith("sqlite:////"):
    _rel = _db_url_raw.replace("sqlite:///", "", 1)
    _abs = (_user_root / _rel).resolve()
    DATABASE_URL: Final[str] = f"sqlite:///{_abs}"
elif _db_url_raw:
    DATABASE_URL = _db_url_raw
else:
    DATABASE_URL = f"sqlite:///{(DATA_DIR / 'fund_nav.db').resolve()}"

# ---------- Scheduling ----------
POLL_INTERVAL_MIN: Final[int] = int(_get_env("POLL_INTERVAL", "30"))
EMAIL_LOOKBACK_DAYS: Final[int] = int(_get_env("EMAIL_LOOKBACK_DAYS", "7"))
MARK_AS_READ: Final[bool] = _get_env("MARK_AS_READ", "false").lower() == "true"

# ---------- Logging ----------
LOG_LEVEL: Final[str] = _get_env("LOG_LEVEL", "INFO").upper()

# ---------- Domain constants ----------
NAV_SUBJECT_KEYWORDS: Final[tuple[str, ...]] = (
    "净值", "基金净值", "产品净值", "估值表", "NAV", "nav",
    "估值", "周报", "日报", "净值更新", "产品估值", "净值披露",
)

FUND_NAME_SUFFIXES: Final[tuple[str, ...]] = (
    "私募证券投资基金", "私募投资基金", "证券投资基金", "私募基金",
    "集合资金信托计划", "集合信托计划", "资产管理计划",
    "私募基金A类", "私募基金B类", "私募基金C类",
    "A类", "B类", "C类", "A期", "B期", "C期",
    "(A类)", "(B类)", "(C类)", "（A类）", "（B类）", "（C类）",
)

GARBAGE_COLUMN_KEYWORDS: Final[tuple[str, ...]] = (
    "合计", "总计", "sum", "total", "备注", "说明", "操作", "状态",
    "管理人", "托管人", "成立日", "份额", "规模",
)

COLUMN_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "date": (
        "日期", "净值日期", "Date", "data_date", "时间",
        "估值日期", "估值基准日", "业务日期", "期末日期", "披露日期",
    ),
    "fund_name": (
        "产品名称", "基金名称", "产品全称", "Name", "name", "基金",
        "基金全称", "产品", "资产名称",
    ),
    "unit_nav": (
        "单位净值", "净值", "NAV", "Unit NAV", "nav", "产品净值", "最新净值",
        "虚拟净值", "虚拟单位净值", "虚拟净值提取后单位净值",
        "资产份额净值(元)", "资产份额净值", "基金份额净值",
        "期末单位净值",
    ),
    "cumulative_nav": (
        "累计净值", "累计单位净值", "累积净值", "累积单位净值",
        "资产份额累计净值(元)", "资产份额累计净值", "基金份额累计净值",
        "母基金累计单位净值",
        "期末累计净值", "期末累计单位净值",
        "虚拟净值提取后累计单位净值", "虚拟净值提取前累计单位净值",
    ),
}

KV_LABELS: Final[dict[str, tuple[str, ...]]] = {
    "fund_name": ("基金名称", "产品名称", "产品全称", "基金全称"),
    "unit_nav": ("基金份额净值", "单位净值", "基金单位净值", "产品净值"),
    "cumulative_nav": (
        "累计净值", "累计单位净值", "基金份额累计净值",
        "资产份额累计净值", "期末累计净值",
    ),
    "nav_date": ("估值日期", "净值日期", "披露日期", "业务日期", "估值基准日"),
}

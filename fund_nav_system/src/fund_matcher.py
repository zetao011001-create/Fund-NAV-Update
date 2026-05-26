"""Fund-name matcher: exact + fuzzy (suffix-stripped + substring)."""

from __future__ import annotations

import re
from typing import Optional

from .config import FUND_NAME_SUFFIXES
from .database import DatabaseManager
from .logger import log


def normalize_fund_name(name: str) -> str:
    """Strip whitespace, common suffixes, and class markers."""
    if not name:
        return ""
    s = re.sub(r"\s+", "", name)
    s = s.replace("（", "(").replace("）", ")")
    changed = True
    while changed:
        changed = False
        for suffix in FUND_NAME_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                changed = True
    s = re.sub(r"\(?[A-Ca-c]类\)?$", "", s)
    s = re.sub(r"[A-Ca-c]类$", "", s)
    return s.strip()


class FundMatcher:
    """Matches an incoming fund name to a tracked fund row.

    Only funds with is_tracking=True are matched; new names are NOT auto-registered.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._cache: dict[str, int] = {}
        self._normalized: dict[str, int] = {}
        self._tracked_names: list[tuple[str, int]] = []
        self.reload()

    def reload(self) -> None:
        self._cache.clear()
        self._normalized.clear()
        self._tracked_names.clear()
        for f in self.db.get_all_tracking_funds():
            full = f["fund_name"]
            fid = f["id"]
            self._cache[full] = fid
            norm = normalize_fund_name(full)
            if norm:
                self._normalized[norm] = fid
            self._tracked_names.append((full, fid))
            if f.get("short_name"):
                self._cache[f["short_name"]] = fid
        log.debug(f"FundMatcher loaded {len(self._cache)} tracked funds")

    def match(self, raw_name: str) -> Optional[int]:
        if not raw_name:
            return None
        name = raw_name.strip()

        # 1. Exact match
        if name in self._cache:
            return self._cache[name]

        # 2. Normalized (suffix stripped) match
        norm = normalize_fund_name(name)
        if norm and norm in self._normalized:
            return self._normalized[norm]

        # 3. Substring containment: tracked short name is contained in incoming name.
        for tracked_full, fid in self._tracked_names:
            tracked_norm = normalize_fund_name(tracked_full)
            if tracked_norm and (tracked_norm in name or tracked_norm in norm):
                return fid
            if tracked_full in name:
                return fid

        return None

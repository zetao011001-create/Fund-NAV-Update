"""One-click entry for the packaged Windows executable.

Pulls latest NAV emails and writes 净值跟踪表.xlsx to the user's Desktop.
Designed to be invoked by double-clicking the packaged exe — the console
window stays open at the end so the user can read the result.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _setup_console_utf8() -> None:
    """Force UTF-8 in Windows cmd so Chinese output renders correctly."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _desktop_path() -> Path:
    home = Path.home()
    for c in (home / "Desktop", home / "桌面"):
        if c.exists():
            return c
    return home


def _wait_for_keypress(prompt: str = "按回车键关闭...") -> None:
    try:
        input(prompt)
    except EOFError:
        pass


def main() -> int:
    _setup_console_utf8()

    print("=" * 50)
    print("  基金净值更新工具")
    print("=" * 50)
    print()

    try:
        # Defer heavy imports so any failure is captured by the outer try.
        from src.query_api import export_to_excel
        from src.sync_engine import SyncEngine

        print("[1/2] 正在同步最新邮件...")
        stats = SyncEngine().run_once()
        print(
            f"      扫描 {stats.emails_scanned} 封, "
            f"新增 {stats.nav_inserted} 条, 更新 {stats.nav_updated} 条"
        )
        if stats.aborted:
            print("      警告: 同步过程中出错,可能拉取的数据不完整")

        print()
        print("[2/2] 正在导出净值跟踪表...")
        output = _desktop_path() / "净值跟踪表.xlsx"
        path = export_to_excel(output_path=output)
        print(f"      已保存到: {path}")

        print()
        print("完成。")
        _wait_for_keypress()
        return 0

    except Exception as exc:  # noqa: BLE001 - present any failure to the user
        print()
        print("出错了:")
        print(f"  {exc}")
        print()
        print("--- 详细信息 ---")
        traceback.print_exc()
        print()
        _wait_for_keypress("出错了,按回车键关闭...")
        return 1


if __name__ == "__main__":
    sys.exit(main())

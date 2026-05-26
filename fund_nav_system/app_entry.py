"""One-click entry for the packaged Windows executable.

Pulls latest NAV emails and writes 净值跟踪表.xlsx to the user's Desktop.
Designed to be invoked by double-clicking the packaged exe — the console
window stays open at the end so the user can read the result.

Robustness:
- Forces UTF-8 + line-buffered stdout so prints flush immediately.
- Writes a crash log to the Desktop on any failure (including SystemExit
  and native-level crashes that bypass Exception).
- Uses Windows `pause` instead of input() so the window survives even when
  stdin is detached / EOF.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------- bootstrap ---
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
                stream.reconfigure(encoding="utf-8", line_buffering=True)
            except Exception:
                pass


def _desktop_path() -> Path:
    home = Path.home()
    for candidate in (home / "Desktop", home / "桌面"):
        if candidate.exists():
            return candidate
    return home


def _crash_log_path() -> Path:
    return _desktop_path() / "净值跟踪更新-错误日志.txt"


def _pause(prompt: str = "按回车键关闭...") -> None:
    print(prompt, flush=True)
    if sys.platform == "win32":
        try:
            os.system("pause >nul")
            return
        except Exception:
            pass
    try:
        input()
    except Exception:
        pass


# --------------------------------------------------------------------- main ---
def main() -> int:
    _setup_console_utf8()

    log_buffer: list[str] = []

    def emit(msg: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} | {msg}"
        log_buffer.append(line)
        print(msg, flush=True)

    def dump_crash_log(reason: str, tb_text: str) -> None:
        try:
            path = _crash_log_path()
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"原因: {reason}\n\n")
                f.write("--- 控制台输出 ---\n")
                f.write("\n".join(log_buffer) + "\n\n")
                f.write("--- Python traceback ---\n")
                f.write(tb_text)
            emit(f"详细错误已保存到: {path}")
        except Exception as exc:  # noqa: BLE001
            print(f"(无法写入错误日志: {exc})", flush=True)

    emit("=" * 50)
    emit("  基金净值更新工具")
    emit("=" * 50)
    emit("")

    try:
        emit("[0/2] 正在加载模块... (首次启动可能需要 10-30 秒)")
        from src.query_api import export_to_excel
        from src.sync_engine import SyncEngine

        emit("[1/2] 正在同步最新邮件...")
        stats = SyncEngine().run_once()
        emit(
            f"      扫描 {stats.emails_scanned} 封, "
            f"新增 {stats.nav_inserted} 条, 更新 {stats.nav_updated} 条"
        )
        if stats.aborted:
            emit("      警告: 同步过程中出错,可能拉取的数据不完整")

        emit("")
        emit("[2/2] 正在导出净值跟踪表...")
        output = _desktop_path() / "净值跟踪表.xlsx"
        path = export_to_excel(output_path=output)
        emit(f"      已保存到: {path}")

        emit("")
        emit("完成。")
        _pause()
        return 0

    except BaseException as exc:  # noqa: BLE001 - capture SystemExit too
        tb_text = traceback.format_exc()
        emit("")
        emit("出错了:")
        emit(f"  {exc!r}")
        emit("")
        emit("--- 详细信息 ---")
        for line in tb_text.splitlines():
            emit(line)
        dump_crash_log(repr(exc), tb_text)
        _pause("出错了,按回车键关闭...")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException as exc:  # last-resort guard so exe never silently dies
        try:
            with open(_crash_log_path(), "w", encoding="utf-8") as f:
                f.write(f"主入口崩溃: {exc!r}\n\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        try:
            print(f"主入口崩溃: {exc!r}", flush=True)
            traceback.print_exc()
        except Exception:
            pass
        _pause("出错了,按回车键关闭...")
        sys.exit(1)

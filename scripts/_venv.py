"""跑錯 Python 的話，先警告再說。

**這個檔案我刪過一次，然後三支腳本當場掛掉**（2026-07-14）：
我查了「哪個 .bat 呼叫它」，得到「沒有」，就當它是死碼刪了——
但 `.bat` 不是唯一的呼叫者，**Python 檔會 import Python 檔**。

教訓：**要刪一個檔案，先問「誰 import 它」，不是只問「誰執行它」。**
（`git grep _venv` 十秒就能查完。我沒查。）

現在的角色：`_py.bat` 決定「用哪個 python」，這裡只負責在**真的跑錯環境時**出聲——
例如有人直接 `python scripts/analyze_all.py` 而不是雙擊 .bat。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV = PROJECT_ROOT / ".venv"


def in_project_venv() -> bool:
    try:
        return VENV.resolve() in Path(sys.executable).resolve().parents
    except OSError:  # pragma: no cover
        return False


def warn_if_global() -> None:
    """不在專案的 .venv 裡 → 提醒一句就好，不擋。

    **不擋**是刻意的：這只是環境提醒，不是錯誤。
    有人可能就是想用系統 Python 跑，那是他的自由。
    """
    if VENV.exists() and not in_project_venv():
        print("⚠ 你不是用專案的 .venv 在跑（套件可能找不到）。改用「.bat」開，或：")
        print(f"    {VENV / 'Scripts' / 'python.exe'} {' '.join(sys.argv)}\n")


__all__ = ["warn_if_global", "in_project_venv", "VENV"]

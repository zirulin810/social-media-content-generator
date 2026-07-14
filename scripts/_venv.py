"""找出該用哪個 python。

.bat 不該自己判斷——批次檔的 if/else 很脆弱，而且不能放中文提示。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def python() -> str:
    for p in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if p.exists():
            return str(p)
    return sys.executable


def warn_if_global() -> None:
    if ".venv" not in python():
        print("⚠ 沒有虛擬環境，正在用全域 Python。")
        print("  建議先雙擊「安裝.bat」——套件會裝在專案裡，不污染全域。\n")

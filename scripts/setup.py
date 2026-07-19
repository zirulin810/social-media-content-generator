"""一次裝好所有東西——裝在專案資料夾裡，不污染全域 Python。

    安裝.bat   （或 python scripts/setup.py）

**為什麼要虛擬環境**：
不用的話，套件會裝進 C:\\Python312\\Lib\\site-packages\\——你電腦上所有 Python 專案共用。
版本一衝突就很難查，而且刪掉這個專案資料夾也清不掉那些垃圾。

用了之後，套件全部住在 `.venv\\`。刪資料夾 = 全部清光。

（Chromium 那 150MB 例外，它放在 AppData 的共用快取，多個專案共用一份。）
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"

WINDOWS = os.name == "nt"


def venv_python() -> Path:
    """venv 裡的 python 在哪。

    **用作業系統判斷，不是用「資料夾存不存在」判斷。**
    第一版寫成 `if not (VENV/"Scripts").exists(): 用 bin/python`——
    但那行跑在 venv 還沒建之前，Scripts/ 當然不存在，於是 Windows 上也走了 Linux 的路徑，
    等 venv 建好（Windows 建的是 Scripts\\）就拿著一個不存在的路徑去執行：

        FileNotFoundError: [WinError 2] 系統找不到指定的檔案。

    存在性檢查回答不了「這是什麼系統」。
    """
    return VENV / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")


def run(*args) -> None:
    print(f"\n$ {' '.join(str(a) for a in args)}")
    subprocess.check_call([str(a) for a in args])


def main() -> int:
    print("=" * 62)
    print("  圖文產生器 — 安裝")
    print("=" * 62)
    print(f"\n專案：{ROOT}")
    print(f"系統：{'Windows' if WINDOWS else os.name}")

    py = venv_python()

    if not py.exists():
        print(f"\n[1/3] 建立虛擬環境 → {VENV}")
        print("      （套件會裝在這裡，不會污染你的全域 Python）")
        venv.create(VENV, with_pip=True, clear=False)
    else:
        print(f"\n[1/3] 虛擬環境已存在 → {VENV}")

    if not py.exists():  # venv 建了卻找不到 python —— 講清楚，不要讓 subprocess 丟天書
        print(f"\n✗ 建好了 venv 卻找不到 {py}")
        print("  請把整個 .venv 資料夾刪掉再跑一次。")
        return 1

    print("\n[2/3] 安裝套件")
    run(py, "-m", "pip", "install", "-q", "--upgrade", "pip")
    run(py, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"))

    print("\n[3/3] 下載 Chromium（約 150MB，第一次會跑一陣子）")
    print("      放在 AppData 的共用快取，多個專案共用一份")
    run(py, "-m", "playwright", "install", "chromium")

    print("\n" + "=" * 62)
    print("  裝好了。東西在哪：")
    print(f"    套件      {VENV}")
    print("    Chromium  C:\\Users\\<你>\\AppData\\Local\\ms-playwright\\")
    print("\n  安裝完成——編輯台即將開啟")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

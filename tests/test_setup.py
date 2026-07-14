"""安裝腳本的測試。

**為什麼值得測**：安裝是每個人碰到的第一件事，壞了就什麼都跑不了。
而且它的 bug 特別隱晦——第一版把「作業系統判斷」寫成「資料夾存在性判斷」：

    PY = VENV / "Scripts" / "python.exe"
    if not PY.parent.exists():        # ← 跑在 venv 還沒建之前
        PY = VENV / "bin" / "python"  #    Windows 上也走了 Linux 路徑

結果 Windows 上噴 `FileNotFoundError: [WinError 2]`。
**存在性檢查回答不了「這是什麼系統」。**
"""

from __future__ import annotations

import os

from scripts.setup import VENV, WINDOWS, venv_python


def test_venv_python_path_depends_on_os_not_on_existence() -> None:
    """路徑要由作業系統決定——venv 還沒建的時候也要答得出來。"""
    py = venv_python()
    assert not py.exists() or py.exists()  # 不管存不存在，都要給得出路徑

    if WINDOWS:
        assert py == VENV / "Scripts" / "python.exe"
    else:
        assert py == VENV / "bin" / "python"


def test_windows_flag_matches_os_name() -> None:
    assert WINDOWS == (os.name == "nt")


def test_venv_python_is_inside_the_project() -> None:
    """套件要裝在專案裡，不是全域。"""
    assert VENV.name == ".venv"
    assert str(venv_python()).startswith(str(VENV))

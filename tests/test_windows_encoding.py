"""Windows 原生工具讀的檔案，一律只能放 ASCII。

**根因：這些工具不用 UTF-8，用系統預設編碼**（繁中 Windows 是 cp950）。
我們的原始碼是 UTF-8，中文塞進去它們就爆炸。

2026-07-14 一天踩到兩次：

1. `.bat` 裡的中文 `echo` —— cmd 逐位元組讀批次檔，加上 `chcp` 改編碼，
   解析器對不準位置，把 `echo` 吃成 `ho`、`render_sample.py` 吃成 `er_sample.py`：

       'ho' 不是內部或外部命令、可執行的程式或批次檔。

2. `requirements.txt` 裡的中文註解 —— pip 用 cp950 讀它：

       UnicodeDecodeError: 'cp950' codec can't decode byte 0x9e

**規則：`.bat` 與 `requirements.txt` 只放 ASCII。中文寫進 Python 或 README。**
（檔名可以是中文——那是檔案系統的事，不是解析器的事。）

Python 檔不受影響：Python 3 預設就用 UTF-8 讀原始碼。
"""

from __future__ import annotations

import re

import pytest

from src.paths import PROJECT_ROOT

# 這些檔案由 Windows 原生工具（cmd / pip）讀，它們不懂 UTF-8
BATS = sorted(PROJECT_ROOT.glob("*.bat")) + sorted((PROJECT_ROOT / "tools").glob("*.bat"))
ASCII_ONLY = BATS + [PROJECT_ROOT / "requirements.txt"]


def test_the_guarded_files_exist() -> None:
    assert len(ASCII_ONLY) > 1, "找不到要守的檔案 —— 測試本身失效了"


@pytest.mark.parametrize("f", ASCII_ONLY, ids=lambda p: p.name)
def test_content_is_pure_ascii(f) -> None:
    raw = f.read_bytes()
    bad = [(i, b) for i, b in enumerate(raw) if b > 0x7F]
    assert not bad, (
        f"{f.name} 第 {bad[0][0]} 個位元組是非 ASCII（0x{bad[0][1]:02X}）。"
        f"Windows 的 cmd / pip 用 cp950 讀它，會爆炸——中文請放到 Python 或 README。"
    )


@pytest.mark.parametrize("f", ASCII_ONLY, ids=lambda p: p.name)
def test_no_bom(f) -> None:
    """BOM 會讓 cmd 把第一行當成亂碼，也會讓 pip 讀到怪東西。"""
    assert not f.read_bytes().startswith(b"\xef\xbb\xbf"), f"{f.name} 有 UTF-8 BOM"


def test_every_bat_resolves_python_through_one_place() -> None:
    """**「我明明跑了安裝.bat」——是的，只是裝到另一個 Python 去了。**

    2026-07-14：`安裝.bat` 直接叫 `python`（系統 Python），
    但執行用的 .bat 走 `.venv\\Scripts\\python.exe`。
    套件裝進 A，程式跑在 B，於是「裝好了卻說找不到」。

    十個 .bat 裡有五個不一致——**同一個決定散在十個地方，遲早走散。**
    現在只有 `_py.bat` 一個地方決定「用哪個 python」。
    """
    bats = [p for p in BATS if p.name != "_py.bat"]
    assert bats, "找不到任何 .bat"

    offenders = []
    for b in bats:
        text = b.read_text(encoding="ascii")
        runs_python = re.search(r"^\s*(\"?python|.*python\.exe)", text, re.M | re.I)
        if runs_python and "_py.bat" not in text:
            offenders.append(b.name)
    assert not offenders, f"這些 .bat 自己決定用哪個 python，沒走 _py.bat：{offenders}"

    helper = (PROJECT_ROOT / "_py.bat").read_text(encoding="ascii")
    assert ".venv" in helper and "python" in helper

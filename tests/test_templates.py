"""版型的守門測試。

版型是 CSS，不是程式——但有些規則值得用測試釘死，
因為它們是**設計決策**，改壞了不會報錯，只會變醜而且沒人發現。
"""

from __future__ import annotations

import json
import re

from src.paths import PROJECT_ROOT

CSS = (PROJECT_ROOT / "templates" / "card.css").read_text(encoding="utf-8")
JS = (PROJECT_ROOT / "templates" / "card.js").read_text(encoding="utf-8")


def _rules_for(selector_substr: str, theme: str) -> list[str]:
    """撈出某主題下、選擇器含某字串的 CSS 規則內容。"""
    out = []
    for m in re.finditer(r'\[data-theme="(\w)"\]([^{]*)\{([^}]*)\}', CSS):
        if m.group(1) == theme and selector_substr in m.group(2):
            out.append(m.group(3))
    return out


def test_accent_is_reserved_for_emphasis_in_dark_theme() -> None:
    """**螢光色是「重點」的專屬語言。**

    Human 在第一版指出：對照卡打勾的螢光底，會跟句子裡的重點字混在一起——
    讀者分不出「哪個是內容重點」「哪個是版面裝飾」。

    所以結構元素（步驟號碼、✓✗ 標籤）一律不准用 --accent。
    """
    for sel in (".num", ".lab"):
        for rule in _rules_for(sel, "b"):
            assert "--accent" not in rule, f"主題 B 的 {sel} 用了螢光色：{rule.strip()}"


def test_structural_colors_exist_in_dark_theme() -> None:
    """結構元素要有自己的語意色，不是隨手挑的。"""
    for var in ("--chip-bg", "--ok-bg", "--no-bg"):
        assert var in CSS, f"主題 B 缺少結構色 {var}"


def test_cover_strips_emphasis_marks() -> None:
    """封面的焦點是大數字。再加螢光重點就是兩個焦點打架——用機制擋，不靠約定。"""
    cover = re.search(r"cover: \(c, ctx\) => `(.*?)`,", JS, re.S).group(1)
    assert "plain(c.angle)" in cover
    assert "hi(c.angle)" not in cover


# 只有固定的 UI 元素可以寫死字級——它們不隨內容多寡改變
UI_ELEMENTS = (".kicker", ".footer", ".pager")


def test_no_hardcoded_content_font_sizes() -> None:
    """**內容字級一律由 autofit 決定。**

    寫死字級 = 版型又開始限制內容了，那正是我們花了整輪對話推翻的東西。
    """
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", CSS):
        selector, body = m.group(1).strip(), m.group(2)
        if not re.search(r"font-size:\s*\d+px", body):
            continue
        assert any(ui in selector for ui in UI_ELEMENTS), (
            f"內容元素 `{selector}` 寫死了字級——應該用 calc(var(--fs) * n)"
        )


def test_autofit_has_a_readability_floor() -> None:
    """字級可以縮，但有下限。低於它就不叫圖卡，叫掃描件。"""
    min_fs = int(re.search(r"MIN_FS = (\d+)", JS).group(1))
    assert 30 <= min_fs <= 40, f"可讀性下限 {min_fs}px 不合理"


def test_every_card_type_has_a_template() -> None:
    """新增卡型忘了寫 template → 渲染時才炸。這裡先攔。"""
    sample = json.loads((PROJECT_ROOT / "samples" / "kaggle-day1-intro.json").read_text(encoding="utf-8"))
    used = {c["type"] for c in sample["cards"] + sample["_stress"]}
    defined = set(re.findall(r"^  (\w+): \(c, ctx\)", JS, re.M))
    assert used <= defined, f"缺少 template：{used - defined}"


def test_both_themes_still_exist() -> None:
    """Human 選了 B，但 A 不准刪——之後可能要換回來。"""
    assert '[data-theme="a"]' in CSS
    assert '[data-theme="b"]' in CSS


def test_autofit_measures_the_viewport_not_the_card() -> None:
    """**第 11 張卡被切掉的根因。**

    `.card` 是 flex 項目、min-height 預設 auto —— 它會被內容撐大（撐到 1300px），
    再被 body 的 overflow:hidden 裁掉。
    原本量的是 `card.scrollHeight <= card.clientHeight` → 1300 <= 1300 → 「塞得下」。
    **量尺跟著卡片一起長高，永遠量不出溢出。**

    正解：量視窗（body 的高度是固定的，不會跟著長）。
    """
    assert "root.clientHeight" in JS, "溢出偵測必須以視窗為基準"

    # 只看真正的程式碼，跳過註解——註解裡本來就會提到這個 bug
    code = [
        ln for ln in JS.splitlines()
        if not ln.strip().startswith(("*", "//", "/*"))
    ]
    for ln in code:
        assert "card.clientHeight" not in ln, (
            f"不准再拿卡片自己當量尺——它會跟著內容長高：{ln.strip()}"
        )


def test_card_cannot_be_stretched_by_its_content() -> None:
    """卡片是容器，不是內容。min-height:0 關掉 flex 的「不能小於內容」預設。"""
    m = re.search(r"^\.card \{([^}]*)\}", CSS, re.M)
    assert m and "min-height:0" in m.group(1).replace(" ", "")


def test_there_is_an_independent_clipping_audit() -> None:
    """autofit 是推論，audit 是量測。

    我已經在推論上栽過一次了——所以截圖前要有一道**跟 CSS 邏輯無關**的檢查：
    逐一量每個元素的 bounding box，有人跑出邊界就拒絕出圖。
    """
    assert "function audit(root)" in JS
    assert "getBoundingClientRect" in JS
    render = (PROJECT_ROOT / "src" / "render" / "render_cards.py").read_text(encoding="utf-8")
    assert 'a.get("clipped")' in render, "截圖前必須檢查 audit 結果"


BROWSER_PY = (PROJECT_ROOT / "src" / "render" / "browser.py").read_text(encoding="utf-8")


def test_we_use_the_system_browser_and_never_download() -> None:
    """**只用系統既有的瀏覽器。找不到就報錯，不偷偷下載。**（Human 2026-07-14 的決定）

    Playwright 預設會自己下載一顆——那是「AI 為了保險而重複安裝你已經有的東西」，
    成本是使用者在付：每次升版重載一組、舊的不刪、`playwright install` 還會裝三顆。

    Windows 一定有 Edge，Chromium 核心，排版能力一模一樣。用你已經有的。
    """
    assert "msedge" in BROWSER_PY and "chrome" in BROWSER_PY, "要先找系統的 Edge / Chrome"

    # 除非人明講 CARD_BROWSER=bundled，否則不准 fallback 到下載的那顆
    fallback = re.search(r'forced == "bundled"', BROWSER_PY)
    assert fallback, "只有人明確指定 bundled 時，才准用下載的那顆"

    # 候選清單裡不准偷偷混進 None（None = 用 bundled）
    channels = re.search(r"^CHANNELS = \(([^)]*)\)", BROWSER_PY, re.M).group(1)
    assert "None" not in channels, "CHANNELS 不准有 None——那會靜默 fallback 到下載的瀏覽器"


def test_browser_errors_speak_human() -> None:
    """環境問題不該噴 traceback。Human 撞到時看到的是 20 行 Python 堆疊——那是我的錯。"""
    assert "找不到可用的系統瀏覽器" in BROWSER_PY, "要認得出「沒有瀏覽器」這個狀況"
    assert "playwright install`" in BROWSER_PY or "playwright install" in BROWSER_PY
    assert "不要" in BROWSER_PY, "要警告別跑不帶參數的 playwright install"

    for script in ("render_sample.py", "test_split.py", "check_browser.py"):
        src = (PROJECT_ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "except PipelineError" in src, f"{script} 要接住環境錯誤，不要噴 traceback"


def test_bat_files_are_ascii_only() -> None:
    """`.bat` 由 cmd 用系統編碼（cp950）讀，中文會讓它逐位元組錯位。

    檔名可以是中文（那是檔案系統的事），內容不行。
    """
    for bat in PROJECT_ROOT.glob("*.bat"):
        raw = bat.read_bytes()
        assert all(b < 128 for b in raw), f"{bat.name} 有非 ASCII 內容"


def test_no_hardcoded_colors_outside_the_token_blocks() -> None:
    """顏色一律走 token。

    2026-07-14 巡筆記時抓到：主題 A 的對照卡標籤寫死 `color:#fff`——
    但主題 A 的底色是米白 `#F7F5F1`，不是純白。寫死的白在那張卡上是「差一點點」的白，
    而且換主題時它不會跟著換。已改成 `var(--bg)`。
    """
    import re

    css = (PROJECT_ROOT / "templates" / "card.css").read_text(encoding="utf-8")
    # 砍掉 token 定義區（[data-theme="x"] { ... } 的那兩塊），剩下的地方不准出現色碼
    body = re.sub(r'\[data-theme="[ab]"\]\s*\{[^}]*\}', "", css, flags=re.S)
    leaked = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", body)
    assert not leaked, f"寫死的顏色（應該用 var(--token)）：{leaked}"

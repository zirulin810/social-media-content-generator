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

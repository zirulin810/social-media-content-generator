"""主題與 4:5 版型（[[圖卡版型與卡型擴充]]）。

主題＝一組 CSS token。這裡釘三件事：
1. 面板清單（editor.html 的 THEMES）與 card.css 的 [data-theme=…] **一致**——
   清單有的主題 CSS 必須有，否則切過去就是一張沒上色的卡
2. 每個主題的 token 組完整（漏一個變數＝某個元素印不出顏色）
3. 4:5 的直式平衡規則存在（內容不再頂錨）
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CSS = (ROOT / "templates/card.css").read_text(encoding="utf-8")
EDITOR = (ROOT / "templates/editor.html").read_text(encoding="utf-8")

# 每個主題都必須定義的 token（card.css 的版型元素會用到）
REQUIRED_TOKENS = ("--bg", "--ink", "--ink-soft", "--ink-mute", "--accent",
                   "--rule", "--hi-bg", "--hi-ink", "--font", "--font-ui", "--pad")


def _panel_themes() -> list[str]:
    m = re.search(r"const THEMES = \[(.*?)\];", EDITOR, re.S)
    assert m, "editor.html 找不到 THEMES 清單"
    return re.findall(r'\["([a-z])",', m.group(1))


def _css_themes() -> set[str]:
    return set(re.findall(r'\[data-theme="([a-z])"\]\s*\{', CSS))


def test_panel_theme_list_matches_css() -> None:
    panel = _panel_themes()
    css = _css_themes()
    assert len(panel) >= 5, "至少 A/B＋三個新主題"
    for t in panel:
        assert t in css, f"面板列了主題 {t}，card.css 卻沒有它的 token 組"


def test_every_theme_defines_all_tokens() -> None:
    for t in _panel_themes():
        block = re.search(r'\[data-theme="%s"\]\s*\{(.*?)\}' % t, CSS, re.S)
        assert block, t
        for token in REQUIRED_TOKENS:
            assert token + ":" in block.group(1), f"主題 {t} 漏了 {token}"


def test_structural_colors_exist_for_new_themes() -> None:
    """步驟號碼與對照 ✓✗ 是結構色——新主題不能漏，漏了就會印出沒上色的元素。"""
    for t in _panel_themes():
        assert re.search(r'\[data-theme="%s"\][^{]*\.num' % t, CSS), f"主題 {t} 沒定義步驟號碼的顏色"
        assert re.search(r'\[data-theme="%s"\][^{]*\.vs \.wrong \.lab' % t, CSS), f"主題 {t} 沒定義對照標籤"


def test_45_ratio_is_vertically_balanced() -> None:
    """4:5 的病根是內容頂錨（2026-07-15 Human：排版好醜）。平衡規則不准消失。"""
    assert 'body[data-ratio="4x5"] .card > .body' in CSS
    assert "margin-top: auto" in CSS.split('body[data-ratio="4x5"]', 1)[1]

"""把 samples/kaggle-day1-intro.json 出成真的 PNG。

    python scripts/render_sample.py           # 主題 b、1:1
    python scripts/render_sample.py a 4x5     # 指定主題與尺寸
    python scripts/render_sample.py --both    # 兩個主題都跑

這支不碰 Gemini、不碰 schema——只驗一件事：**版型能不能出圖、拆卡對不對。**

註：所有中文都在這裡印，**.bat 檔一律只放 ASCII**。
Windows 的 cmd 逐位元組讀批次檔，中文 + chcp 會讓解析器位置錯亂，
把 `echo` 吃成 `ho`、把 `render_sample.py` 吃成 `er_sample.py`。踩過一次就夠了。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._venv import warn_if_global  # noqa: E402

warn_if_global()

from src.errors import PipelineError  # noqa: E402
from src.paths import PROJECT_ROOT  # noqa: E402
from src.render.browser import launch_chromium, sync_playwright_or_die  # noqa: E402
from src.render.layout import plan_all  # noqa: E402
from src.render.render_cards import RATIOS, Renderer  # noqa: E402

THEME_NAME = {"a": "編輯大字", "b": "深色螢光"}


def run(theme: str, ratio: str) -> int:
    data = json.loads((PROJECT_ROOT / "samples" / "kaggle-day1-intro.json").read_text(encoding="utf-8"))
    ctx = data["ctx"]
    out = PROJECT_ROOT / "out" / "_sample" / f"{theme}-{ratio}"
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    w, h = RATIOS[ratio]
    print(f"\n{'=' * 60}")
    print(f"  主題 {theme.upper()}（{THEME_NAME[theme]}）｜{ratio}　{w}×{h}")
    print(f"{'=' * 60}")

    with sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto((PROJECT_ROOT / "templates" / "card.html").as_uri())
        page.evaluate(
            "([t, r]) => { document.body.dataset.theme = t; document.body.dataset.ratio = r; }",
            [theme, ratio],
        )
        page.evaluate("document.fonts.ready")
        r = Renderer(page, ctx)

        cards = data["cards"] + data["_stress"]
        try:
            deck = plan_all(cards, r.fits)          # ← 塞不下的在這裡被拆開
        except PipelineError as e:
            print(f"  ✗ {e.render()}")
            browser.close()
            return 1

        split = len(deck) - len(cards)
        print(f"  原本 {len(cards)} 張 → 拆完 {len(deck)} 張"
              + (f"（自動拆出 {split} 張）" if split else "（都塞得下，沒拆）") + "\n")
        low = 0
        for i, card in enumerate(deck, 1):
            name = f"{i:02d}_{card['type']}.png"
            fit = r.shoot(card, out / name)
            tag = "  ← 拆卡" if card.get("pager") else ""
            from src.render.render_cards import COMFORT_FS as _c
            warn = "  ⚠ 仍低於舒適下限" if fit["fs"] < _c else ""
            if fit["fs"] < _c:
                low += 1
            title = card.get("title") or card.get("angle") or card.get("text") or ""
            print(f"  {name:22} 字級 {fit['fs']:>2}px  {title[:26]}{tag}{warn}")
        browser.close()

    print(f"\n  ✓ {len(deck)} 張 PNG → {out}")
    if low:
        print(f"  ⚠ {low} 張逼近可讀性下限（34px）——內容可能該再精簡")
    return 0


def main() -> int:
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("✗ 沒裝 playwright。先跑這兩行：")
        print("    pip install -r requirements.txt")
        print("    playwright install chromium")
        return 1

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    both = "--both" in sys.argv

    themes = ["b", "a"] if both else [args[0] if args else "b"]
    ratio = args[1] if len(args) > 1 else "1x1"

    for t in themes:
        rc = run(t, ratio)
        if rc:
            return rc

    print("\n" + "=" * 60)
    print("  圖在 out\\_sample\\ 底下。打開來看，然後告訴我哪裡要改。")
    print("  最該盯的是**拆開的那幾張**：編號有沒有接對、續卡的標題看起來蠢不蠢。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as e:
        # 環境問題不該噴 traceback——直接講怎麼修
        print(f"\n✗ {e.render()}")
        raise SystemExit(1) from None

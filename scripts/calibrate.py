"""量出版面的真實容量 —— 契約的字數上限要照這個訂，不是照我猜的。

    python scripts/calibrate.py        （或雙擊「校準字數上限.bat」）

背景：schema 的字數上限是我在還沒有版型的時候**猜**的，schema 自己的註解也寫著
「暫定值——版型做出來後要回頭校準」。版型做出來了，實機一量才發現：

    契約允許的最壞卡片（4 步 × 60 字），實際只縮到 57px——遠在舒適線 44px 之上。
    **上限比版面容量還緊。** 於是模型在源頭就把話講一半，拆卡永遠不會被觸發。

砍內容的不是版面，是契約。

這支就是那把尺：對每種卡型二分搜尋「字級還在舒適線上時，最多能塞幾個字」。

兩種上限，性質完全不同：

    可拆的卡型（point / steps）→ 上限只是**理智界線**。
        超過單張容量不是錯，版面會拆成多張。上限訂在「單張容量 × 可拆張數」。

    不可拆的卡型（quote / contrast / cover）→ 上限是**硬牆**。
        它們的結構切不開，超過就真的印不出來。上限 = 實測容量（留一點餘裕）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.errors import PipelineError  # noqa: E402
from src.paths import PROJECT_ROOT  # noqa: E402
from src.render.browser import launch_chromium, sync_playwright_or_die  # noqa: E402
from src.render.layout import MAX_SPLITS  # noqa: E402
from src.render.render_cards import COMFORT_FS, RATIOS, Renderer  # noqa: E402

CTX = {"title": "T", "author": "Kaggle", "url": "x", "handle": "@h", "series": "S"}

FILLER = "建立一份設定檔並寫清楚你希望 AI 用什麼方式跟你協作，把它放進筆記庫的根目錄裡隨時可以讀取"
MARGIN = 0.9  # 實測容量打九折當上限——字體、標點、換行位置都會讓實際表現有些浮動


def text(n: int) -> str:
    return (FILLER * (n // len(FILLER) + 1))[:n]


def biggest(r: Renderer, build, lo: int = 4, hi: int = 600) -> int:
    """二分搜尋：字級還在舒適線上（且沒溢出）時，最多塞幾個字。"""
    def ok(n: int) -> bool:
        m = r.measure(build(n))
        return not m["overflow"] and m["fs"] >= COMFORT_FS

    if not ok(lo):
        return 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def main() -> int:
    sync_playwright = sync_playwright_or_die()
    w, h = RATIOS["1x1"]

    print(f"舒適下限 {COMFORT_FS}px：字級掉到這之下就該拆卡（或退回改文案）")
    print("量的是「字級還在舒適線上時，單張卡最多塞得下幾個字」\n")

    with sync_playwright() as p:
        b = launch_chromium(p)
        page = b.new_page(viewport={"width": w, "height": h})
        page.goto((PROJECT_ROOT / "templates" / "card.html").as_uri())
        page.evaluate(
            "([t,r])=>{document.body.dataset.theme=t;document.body.dataset.ratio=r}", ["b", "1x1"]
        )
        page.evaluate("document.fonts.ready")
        r = Renderer(page, CTX)

        T = "讓 AI 讀懂你的筆記庫"  # 標題固定，量的是內文

        # ---- 可拆的卡型：單張容量 × 可拆張數 = 契約上限 ----
        print("可拆的卡型（超過單張容量 → 版面自動拆卡，不是錯誤）")
        print(f"{'卡型':<22}{'單張容量':>8}{'×拆':>5}{'  → 契約上限':<14}現行")
        print("-" * 68)

        point_cap = biggest(r, lambda n: {"type": "point", "title": T, "body": text(n)})
        sug_point = int(point_cap * MAX_SPLITS * MARGIN)
        print(f"{'point  body':<22}{point_cap:>6} 字{MAX_SPLITS:>4}{'  → ' + str(sug_point):<14}120")

        steps_caps = {}
        for n in (2, 3, 4, 5, 6):
            cap = biggest(
                r,
                lambda c, n=n: {
                    "type": "steps",
                    "title": T,
                    "steps": [{"text": text(c)} for _ in range(n)],
                },
            )
            steps_caps[n] = cap
            print(f"{'steps  ' + str(n) + ' 步，每步':<22}{cap:>6} 字{'':>5}{'':<14}"
                  f"{'60' if n <= 4 else '（契約不允許）'}")

        # ---- 不可拆的卡型：實測容量就是硬牆 ----
        print("\n不可拆的卡型（結構切不開，超過就印不出來 → 上限是硬牆）")
        print(f"{'卡型':<22}{'實測容量':>8}{'  → 建議上限':<14}現行")
        print("-" * 68)

        quote_cap = biggest(r, lambda n: {"type": "quote", "text": text(n), "verbatim": True})
        print(f"{'quote  text':<22}{quote_cap:>6} 字{'  → ' + str(int(quote_cap * MARGIN)):<14}40")

        con_cap = biggest(
            r,
            lambda n: {
                "type": "contrast",
                "title": T,
                "wrong": {"text": text(n)},
                "right": {"text": text(n)},
            },
        )
        print(f"{'contrast 每邊':<22}{con_cap:>6} 字{'  → ' + str(int(con_cap * MARGIN)):<14}60")

        angle_cap = biggest(
            r, lambda n: {"type": "cover", "angle": text(n), "hook": "副標一句話", "stat": "01"}
        )
        hook_cap = biggest(
            r, lambda n: {"type": "cover", "angle": "封面主標", "hook": text(n), "stat": "01"}
        )
        print(f"{'cover  angle':<22}{angle_cap:>6} 字{'  → ' + str(int(angle_cap * MARGIN)):<14}30")
        print(f"{'cover  hook':<22}{hook_cap:>6} 字{'  → ' + str(int(hook_cap * MARGIN)):<14}70")

        b.close()

    print("\n把上面這張表貼回對話，我照它改 schema 與 prompt——不再用猜的。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as e:
        print(f"\n✗ {e.render()}")
        raise SystemExit(1) from None

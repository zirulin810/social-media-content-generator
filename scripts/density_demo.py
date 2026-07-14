"""同一個知識，三種密度 —— 用眼睛決定一張卡該放多少。

    python scripts/density_demo.py     （或雙擊「比較密度.bat」）

為什麼需要這支：

校準（scripts/calibrate.py）量出來，一張卡在 44px 之下塞得下 **548 個字**。
技術上「讀得到」，實際上那是一面文字牆。**版面根本不是限制。**

也就是說，字數上限從來不是技術問題，是**編輯判斷**：
一張卡該放多少，才讓人滑到時願意讀完？這件事量尺答不出來，只有人的眼睛能答。

所以這裡不放假字，**放真的內容**——同一組步驟、同一個重點，寫成三種密度，
渲染成圖，讓人挑。挑完的數字才進 schema。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.errors import PipelineError  # noqa: E402
from src.paths import PROJECT_ROOT  # noqa: E402
from src.render.browser import launch_chromium, sync_playwright_or_die  # noqa: E402
from src.render.render_cards import RATIOS, Renderer  # noqa: E402

CTX = {
    "title": "Whitepaper Companion Podcast",
    "author": "Kaggle",
    "url": "x",
    "handle": "@your_handle",
    "series": "AI Agents 入門",
}

TITLE = "讓 AI 讀懂你的筆記庫"

# 同一組步驟，三種寫法。**內容是真的**（來自 Obsidian + AI 那批素材）。
STEPS = {
    "精簡": [
        "建立 me.md：寫清楚你是誰、在做什麼",
        "建立 vault map：每個資料夾放什麼",
        "建立 skill map：有哪些技能、何時用",
        "開場貼啟動語，把三份檔案讀進去",
    ],
    "中等": [
        "建立 me.md：寫清楚你是誰、現在在做什麼、希望 AI 用什麼方式跟你協作",
        "建立 vault map：每個資料夾放什麼、命名規則是什麼，AI 才不用掃整個庫",
        "建立 skill map：有哪些技能、各自解決什麼問題、什麼時候該用",
        "新對話開頭貼一段啟動語，確保這三份檔案都被讀進去",
    ],
    "充分": [
        "建立 me.md：寫清楚你是誰、現在在做什麼、希望 AI 用什麼語氣跟你協作。"
        "這是它每次對話的起點，沒有它就只能猜你要什麼",
        "建立 vault map：每個資料夾放什麼、命名規則是什麼。有了它，AI 不必掃描"
        "整個筆記庫就知道東西在哪，省下大量往返",
        "建立 skill map：列出有哪些技能、各自解決什麼問題、什麼時候該用它們，"
        "並各附一個實際例子",
        "新對話開頭貼一段啟動語把這三份檔案讀進去；設好快捷鍵或文字替換，"
        "兩個鍵就能貼上，不然你會懶得做",
    ],
}

POINTS = {
    "精簡": "AI 不是不夠聰明，是不知道你的脈絡。先給它一張地圖，再叫它做事。",
    "中等": "AI 不是不夠聰明，是**不知道你的脈絡**。你每次都從零開始解釋，它每次都只能猜。"
            "先給它一張地圖，再叫它做事——這一步省下的往返，比任何提示詞技巧都多。",
    "充分": "AI 不是不夠聰明，是**不知道你的脈絡**。你每次都從零開始解釋自己是誰、筆記庫長怎樣、"
            "想要什麼格式，它每次都只能用通用答案敷衍你。先花一次時間給它一張地圖——你是誰、"
            "東西放哪、有哪些工具可用——再叫它做事。這一步省下的往返，比任何提示詞技巧都多，"
            "而且只需要做一次。",
}


def main() -> int:
    sync_playwright = sync_playwright_or_die()
    out = PROJECT_ROOT / "out" / "_density"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    w, h = RATIOS["1x1"]
    print("同一個知識，三種密度。**用眼睛挑**，挑完的數字才進 schema。\n")
    print(f"{'檔名':<22}{'內容量':>8}{'字級':>7}   感覺")
    print("-" * 62)

    with sync_playwright() as p:
        b = launch_chromium(p)
        page = b.new_page(viewport={"width": w, "height": h})
        page.goto((PROJECT_ROOT / "templates" / "card.html").as_uri())
        page.evaluate(
            "([t,r])=>{document.body.dataset.theme=t;document.body.dataset.ratio=r}", ["b", "1x1"]
        )
        page.evaluate("document.fonts.ready")
        r = Renderer(page, CTX)

        for label, steps in STEPS.items():
            card = {"type": "steps", "title": TITLE, "steps": [{"text": s} for s in steps]}
            longest = max(len(s) for s in steps)
            name = f"steps_{label}_{longest}字.png"
            fit = r.shoot(card, out / name)
            print(f"{name:<22}{'每步 ' + str(longest) + ' 字':>8}{str(fit['fs']) + 'px':>7}")

        for label, body in POINTS.items():
            card = {"type": "point", "title": "先給 AI 一張地圖", "body": body}
            name = f"point_{label}_{len(body)}字.png"
            fit = r.shoot(card, out / name)
            print(f"{name:<22}{str(len(body)) + ' 字':>8}{str(fit['fs']) + 'px':>7}")

        b.close()

    print(f"\n圖在：{out}")
    print("挑一個密度（精簡／中等／充分），或直接說『步驟用中等、重點用精簡』。")
    try:  # 順手把資料夾開起來，省得自己找
        subprocess.run(["explorer", str(out)], check=False)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as e:
        print(f"\n✗ {e.render()}")
        raise SystemExit(1) from None

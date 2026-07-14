"""拆卡的實機測試 —— 開真的瀏覽器，證明它到底拆不拆。

    python scripts/test_split.py

單元測試（tests/test_layout.py）用假量尺，只驗「決策邏輯對不對」。
真正會錯的是**量尺本身**，我已經在那上面栽過三次：

    1. 量錯東西（量卡片，卡片會跟著內容長高）→ 卡片被切掉
    2. 門檻訂錯（只有硬底線 34px）→ 塞得下就不拆 → 一面文字牆
    3. **測試本身測錯範圍** → 掃 2–6 步 × 30 字，但契約只准 2–4 步 × 60 字。
       於是我測了 pipeline 永遠產不出來的輸入（5、6 步），
       卻從沒測過它真的會產出的最壞情況（4 步 × 60 字）。
       五列全部「保留單張」——**拆卡那條路一次都沒被執行，測試卻是綠的。**

教訓：**測試要掃契約的邊界，不是掃我隨手想到的數字。**
schema 說什麼是合法的，這支就測什麼——尤其測「合法但最壞」的那一張。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.errors import PipelineError  # noqa: E402
from src.paths import PROJECT_ROOT, SCHEMA_DIR  # noqa: E402
from src.render.browser import launch_chromium, sync_playwright_or_die  # noqa: E402
from src.render.layout import plan  # noqa: E402
from src.render.render_cards import COMFORT_FS, RATIOS, Renderer  # noqa: E402

CTX = {"title": "T", "author": "Kaggle", "url": "x", "handle": "@h", "series": "S"}

FILLER = "建立一份設定檔，寫清楚你希望 AI 用什麼方式跟你協作，並把它放進筆記庫的根目錄方便隨時讀取"


def limits() -> dict[str, int]:
    """**直接去問 schema**，不要把上限抄進這裡。

    抄一份就是多一個會跟契約走散的地方——而這支測試的全部價值，
    就在於它掃的是契約真正允許的範圍。
    """
    s = json.loads((SCHEMA_DIR / "highlights.schema.json").read_text(encoding="utf-8"))
    steps = s["$defs"]["stepsCard"]["properties"]["steps"]
    point = s["$defs"]["pointCard"]["properties"]["body"]
    return {
        "steps_min": steps["minItems"],
        "steps_max": steps["maxItems"],
        "step_chars": steps["items"]["properties"]["text"]["maxLength"],
        "point_chars": point["maxLength"],
    }


def text(n: int) -> str:
    """剛好 n 個字的內容（拿來逼近字數上限，不是拿來讀的）。"""
    return (FILLER * (n // len(FILLER) + 1))[:n]


def steps_card(n: int, chars: int) -> dict:
    return {
        "type": "steps",
        "title": "讓 AI 讀懂你的筆記庫",
        "steps": [{"text": text(chars)} for _ in range(n)],
    }


def point_card(chars: int) -> dict:
    return {"type": "point", "title": "先給 AI 一張地圖", "body": text(chars)}


def main() -> int:
    sync_playwright = sync_playwright_or_die()
    lim = limits()
    w, h = RATIOS["1x1"]

    print(f"契約允許的範圍（讀自 schema）："
          f"steps {lim['steps_min']}–{lim['steps_max']} 步 × ≤{lim['step_chars']} 字"
          f"｜point ≤{lim['point_chars']} 字")
    print(f"舒適下限 {COMFORT_FS}px（低於此就該拆）｜硬底線 34px（低於此不出圖）\n")

    # 掃契約的邊界：每種卡型都測到它合法的最大值
    cases: list[tuple[str, dict]] = []
    for n in range(lim["steps_min"], lim["steps_max"] + 1):
        for chars in (lim["step_chars"] // 3, lim["step_chars"] * 2 // 3, lim["step_chars"]):
            cases.append((f"steps  {n} 步 × {chars} 字", steps_card(n, chars)))
    for chars in (lim["point_chars"] // 3, lim["point_chars"] * 2 // 3, lim["point_chars"]):
        cases.append((f"point  {chars} 字", point_card(chars)))

    print(f"{'內容量':<20}{'單張':>7}  {'該拆嗎':<6}{'實際':<14}結果")
    print("-" * 72)

    failures: list[str] = []
    split_seen = whole_seen = 0

    with sync_playwright() as p:
        b = launch_chromium(p)
        page = b.new_page(viewport={"width": w, "height": h})
        page.goto((PROJECT_ROOT / "templates" / "card.html").as_uri())
        page.evaluate(
            "([t,r])=>{document.body.dataset.theme=t;document.body.dataset.ratio=r}", ["b", "1x1"]
        )
        page.evaluate("document.fonts.ready")
        r = Renderer(page, CTX)

        for label, card in cases:
            fs = r.measure(card)["fs"]          # 不拆的話會縮到幾 px
            want = fs < COMFORT_FS              # 規則：不舒適就該拆。**沒有寫死的步數**

            try:
                cards = plan(card, r.fits)
            except PipelineError as e:
                # 拆不動 = 這張卡合法卻印不出來 → 是契約的洞，不是「預期行為」
                print(f"{label:<20}{fs:>5}px  {'要':<6}{'拆不開':<14}✗ 契約允許卻印不出來")
                failures.append(f"{label}: {e.message}")
                continue

            got = len(cards) > 1
            ok = got == want
            split_seen += got
            whole_seen += not got

            note = ""
            if got:
                # 拆完的每一張都必須自己站得住：不溢出、字級在舒適線上
                fits_all = []
                for c in cards:
                    m = r.measure(c)
                    fits_all.append(not m["overflow"] and m["fs"] >= COMFORT_FS)
                    note += f" {m['fs']}px"
                if not all(fits_all):
                    ok = False
                    failures.append(f"{label}: 拆完仍有卡片不合格")

                if card["type"] == "steps":
                    nums: list[int] = []
                    for c in cards:
                        s = c.get("startIndex", 1)
                        nums += list(range(s, s + len(c["steps"])))
                    if nums != list(range(1, len(card["steps"]) + 1)):
                        ok = False
                        failures.append(f"{label}: 編號沒接好 {nums}")
                    kept = sum(len(c["steps"]) for c in cards)
                    if kept != len(card["steps"]):
                        ok = False
                        failures.append(f"{label}: 步驟掉了（{len(card['steps'])} → {kept}）")

            if not ok and label not in str(failures):
                failures.append(label)

            decision = f"拆成 {len(cards)} 張{note}" if got else "保留單張"
            print(f"{label:<20}{fs:>5}px  {'要' if want else '不用':<6}{decision:<14}"
                  f"{'OK' if ok else '✗ 不對'}")

        b.close()

    # **非空洞性檢查。** 上一版就是死在這裡：五列全綠，但拆卡一次都沒跑到。
    # 一支從不執行受測程式碼的測試，全綠也證明不了任何事。
    print()
    if not split_seen:
        print("✗ 整輪沒有任何一張卡被拆——這支測試根本沒測到拆卡（等於沒測）")
        failures.append("vacuous: no split exercised")
    elif not whole_seen:
        print("✗ 每一張卡都被拆了——門檻可能訂得太高，一樣沒測到「不該拆就別拆」")
        failures.append("vacuous: nothing left whole")
    else:
        print(f"涵蓋度：{split_seen} 張走了拆卡、{whole_seen} 張保留單張（兩條路都跑到了）")

    print("\n" + ("✗ 有問題：\n  " + "\n  ".join(map(str, failures)) if failures else "✓ 拆卡行為正確"))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as e:
        print(f"\n✗ {e.render()}")
        raise SystemExit(1) from None

"""跑分析，並印出一張「主張 ↔ 原文」的審稿表。

    python scripts/analyze_all.py [slug]

機器只做機械的部分：這句 source_text，在它宣稱的那一段裡找不找得到。

- ✓  找得到（不代表中文重述沒超譯——那要你看）
- ⚠  句子是真的，只是段落標錯（無害，索引偏一格而已）
- ✗  **原文中完全找不到這句話**（這才是要警戒的）

**判斷是你的。** 機器不擋，只是把表排好。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._venv import warn_if_global  # noqa: E402

warn_if_global()

from src.analyze.extract_highlights import extract, review_slug  # noqa: E402
from src.errors import PipelineError  # noqa: E402
from src.paths import article_dir, out_root  # noqa: E402

SKIP = {"ai-llm-wiki-obsidian"}  # 逐步教學，抽不出可用知識（見〈文章來源清單與挑選標準〉）

TYPE_NAME = {"point": "重點", "steps": "步驟", "contrast": "對照", "quote": "金句"}


def show(slug: str) -> bool:
    article = json.loads((article_dir(slug) / "article.json").read_text(encoding="utf-8"))
    src = article["source"]

    print("\n" + "=" * 74)
    print(f"  {src['title'][:60]}")
    # author 是選填（契約 v3.3：課程、官方文件、白皮書常常沒有個人作者）。
    # 沒有就整段不印——不要留一個孤零零的「｜」開頭，那看起來像出錯。
    meta = [src.get("author"), f"{article['word_count']} 字", article["language"]]
    print("  " + "｜".join(m for m in meta if m))
    print("=" * 74)

    t0 = time.perf_counter()
    try:
        extract(slug, force=True)
    except PipelineError as e:
        print(f"✗ {e.render()}\n")
        return False
    print(f"({time.perf_counter() - t0:.1f}s)\n")

    findings, h = review_slug(slug)
    by_where = {f.claim.where: f for f in findings}

    for pi, post in enumerate(h["posts"], 1):
        print(f"─── 第 {pi} 則貼文：{post['angle']} " + "─" * 26)
        if post.get("hook"):
            print(f"    {post['hook']}")
        print()

        for ci, card in enumerate(post["cards"], 1):
            t = card["type"]
            print(f"  [{TYPE_NAME.get(t, t)}卡]", end=" ")

            if t == "quote":
                print(f"「{card['text']}」")
            elif t == "point":
                print(f"{card['title']}")
                print(f"          {card['body']}")
            elif t == "steps":
                print(f"{card['title']}")
                for si, s in enumerate(card["steps"], 1):
                    print(f"          {si}. {s['text']}")
            elif t == "contrast":
                print(f"{card['title']}")
                print(f"          ✗ {card['wrong']['text']}")
                print(f"          ✓ {card['right']['text']}")
            print()

    print("─── 審稿表：每條主張 vs 它宣稱的原文 " + "─" * 22)
    print()
    MARK = {"ok": "✓", "misindexed": "⚠", "fabricated": "✗"}
    counts = {"ok": 0, "misindexed": 0, "fabricated": 0}

    for f in findings:
        counts[f.severity] += 1
        print(f"  {MARK[f.severity]} {f.claim.where}")
        print(f"      主張：{f.claim.text}")
        for ev in f.claim.evidence:
            print(f"      原文（第 {ev['para_index']} 段）：{ev['source_text'][:88]}")
        if not f.ok:
            print(f"      → {f.problem}")
        print()

    total = len(findings)
    print(f"  ✓ {counts['ok']}/{total} 對得上原文")
    if counts["misindexed"]:
        print(f"  ⚠ {counts['misindexed']} 條段落標錯（句子是真的，索引偏了——無害）")
    if counts["fabricated"]:
        print(f"  ✗ {counts['fabricated']} 條**原文中完全找不到** ← 這幾條要看仔細")
    print("\n  機器不判斷「中文有沒有超譯」——那要你看上面的並排。")

    # 用語只標記、不判決。**同一個詞，語意不同就是兩件事**：
    # 「程序正義」是台灣話，「這個程序有 bug」是中國話——黑名單看得到字串，看不到語意。
    from src.analyze import locale

    terms = [i for i in locale.scan(h) if i.kind == "term"]
    if terms:
        print(f"\n─── 用語提醒（{len(terms)} 處，**機器判不準，你自己看**）" + "─" * 18)
        for i in terms:
            print(f"  ⚑ {i.where}：「{i.found}」→ 台灣通常說「{i.suggest}」")
            print(f"      {i.context}")
        print("  真的是中國用語就改；是台灣的正常說法（程序正義、大數據、電影腳本）就不用理它。")

    return counts["fabricated"] == 0


def main() -> int:
    slugs = (
        [sys.argv[1]]
        if len(sys.argv) > 1
        else [
            d.name
            for d in sorted(out_root().iterdir())
            if d.is_dir() and (d / "article.json").exists() and d.name not in SKIP
        ]
    )
    if not slugs:
        print("out/ 裡沒有 article.json，先跑 ingest。")
        return 1

    ok = all([show(s) for s in slugs])
    print("\n" + "=" * 74)
    print("  請看「審稿表」：這些卡片，你敢不敢直接發？把輸出貼給我。")
    print("=" * 74)
    return 0 if ok else 0  # 對不上也不算失敗——判斷是人的


if __name__ == "__main__":
    raise SystemExit(main())

"""煙霧測試：確認 LLM 供應商真的接得上，並跑一篇真實文章的金句抽取。

    python scripts/smoke_test.py

它會依序回報每一步，出錯時直接告訴你卡在哪——不要看到紅字就慌，往下讀就好。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._venv import warn_if_global  # noqa: E402

warn_if_global()

from src import llm  # noqa: E402
from src.analyze.extract_highlights import extract  # noqa: E402
from src.errors import PipelineError  # noqa: E402
from src.paths import article_dir  # noqa: E402

SLUG = "how-i-use-obsidian-claude-cowork-to-run-my-life"


def step(n: int, msg: str) -> None:
    print(f"\n[{n}] {msg}")


def main() -> int:
    print("=" * 60)
    print("自動化輸出 — LLM 煙霧測試")
    print("=" * 60)

    step(1, "環境變數")
    print(f"  LLM_PROVIDER = {llm.PROVIDER}")
    found = [n for n in llm.GEMINI_KEY_NAMES if os.environ.get(n)]
    if not found:
        print(f"  ✗ 找不到 key。找過：{' / '.join(llm.GEMINI_KEY_NAMES)}")
        print("    → 這兩個名字都沒有的話，請告訴我你的環境變數叫什麼名字")
        return 1
    key = os.environ[found[0]]
    print(f"  ✓ {found[0]} = {key[:6]}…{key[-4:]}（長度 {len(key)}）")

    step(2, "這把 key 能用哪些模型？")
    try:
        models = llm.list_models()
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 列不出來：{e}")
        print("    → key 無效、未開通 Generative Language API，或網路被擋")
        return 1
    print(f"  ✓ 共 {len(models)} 個。跟 gemini 有關的：")
    for m in [m for m in models if m.startswith("gemini")][:12]:
        mark = " ← 目前設定" if m == llm.GEMINI_MODEL else ""
        print(f"      {m}{mark}")
    if llm.GEMINI_MODEL not in models:
        print(f"  ✗ 設定的模型 `{llm.GEMINI_MODEL}` 不在清單裡！")
        print("    → 從上面挑一個：set GEMINI_MODEL=gemini-2.0-flash")
        return 1
    print("\n    要換模型的話（例如 2.5-flash 一直忙）：set GEMINI_MODEL=gemini-2.0-flash")

    step(3, "呼叫模型（最小測試）")
    t0 = time.perf_counter()
    try:
        out = llm.get_llm()('只回這個 JSON，不要多說：{"ok": true}')
    except PipelineError as e:
        print(f"  ✗ {e.render()}")
        return 1
    print(f"  ✓ {time.perf_counter() - t0:.1f}s，回應：{out.strip()[:80]}")

    step(4, f"真實文章金句抽取（{SLUG}）")
    if not (article_dir(SLUG) / "article.json").exists():
        print("  ✗ 找不到 article.json，先跑：")
        print('    python -c "from pathlib import Path; from src.ingest.read_article import read;'
              ' read(Path(r\'C:\\Users\\HMG\\Documents\\Obsidian Vault\\Clippings'
              '\\How I Use Obsidian + Claude Cowork to Run My Life.md\'), force=True)"')
        return 1
    print(f"    （{llm.GEMINI_MODEL}，大 prompt，可能要 10–60 秒；忙碌時會自動重試）")
    t0 = time.perf_counter()
    try:
        path = extract(SLUG, force=True)
    except PipelineError as e:
        print(f"  ✗ {e.render()}")
        if e.code == "QUOTE_NOT_GROUNDED":
            print("\n    這其實是好消息：模型編了一句原文沒有的話，被幻覺防線擋下了。")
            print("    防線在運作。把上面的訊息貼給我，我調 prompt。")
        return 1
    took = time.perf_counter() - t0

    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"  ✓ {took:.1f}s，模型：{data['model']}")
    print(f"  ✓ 產物：{path}")
    print(f"\n  摘要（{len(data['summary'])} 條）：")
    for s in data["summary"]:
        print(f"    · {s}")
    print(f"\n  金句（{len(data['quotes'])} 句，全部通過幻覺防線）：")
    for i, q in enumerate(data["quotes"], 1):
        print(f"    {i}. 「{q['text']}」（{len(q['text'])} 字，第 {q['para_index']} 段）")
        print(f"       原文：{q['source_text'][:70]}")
    print(f"\n  hashtags：{' '.join(data.get('hashtags', []))}")

    print("\n" + "=" * 60)
    print("全部通過。把上面的輸出貼給我。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

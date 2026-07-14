"""幫所有已出圖的貼文寫文案，並把成品印出來給人審。

    python scripts/compose_all.py          （或雙擊「產生文案.bat」）
    python scripts/compose_all.py --force   重寫

印出來的東西就是你要貼到 IG／Threads 的**完整文字**——包含出處與 hashtag。
不是摘要，是成品。**看到什麼就是會發出去什麼。**
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.compose.write_post import IG_FOLD_CHARS, compose_post  # noqa: E402
from src.errors import PipelineError  # noqa: E402
from src.paths import highlights_path, images_dir, out_root  # noqa: E402
from src.schema import read_json  # noqa: E402


def ready() -> list[tuple[str, int]]:
    """找出「有 highlights 也有圖卡」的貼文。"""
    root = out_root()
    if not root.is_dir():
        return []
    out: list[tuple[str, int]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or not highlights_path(d.name).is_file():
            continue
        h = read_json("highlights", highlights_path(d.name))
        for i in range(1, len(h["posts"]) + 1):
            if any(images_dir(d.name, i).glob("*.png")):
                out.append((d.name, i))
    return out


def show(path: Path) -> None:
    import json
    import re

    from src.analyze.locale import TERMS

    term_re = re.compile("|".join(sorted(TERMS, key=len, reverse=True)))

    data = json.loads(path.read_text(encoding="utf-8"))
    for p in data["posts"]:
        name = "Instagram" if p["platform"] == "instagram" else "Threads"
        cap = p["caption"]
        print(f"\n  ── {name}（{len(cap)} 字，{len(p['image_paths'])} 張圖）" + "─" * 30)
        for line in cap.splitlines():
            print(f"  │ {line}")
        if p["platform"] == "instagram":
            hook = cap.splitlines()[0]
            print(f"  └ hook（{len(hook)} 字）：「{hook}」  ← 值得為它停下手指嗎？")
        else:
            print("  └")

        # 用語只標記、不改。**同一個詞，語意不同就是兩件事**——機器判不準，你自己看。
        # （出處那幾行是原文標題，不算。）
        body = cap.split("\n\n原文：")[0]
        hits = dict.fromkeys(term_re.findall(body))
        if hits:
            pairs = "、".join(f"「{h}」→「{TERMS[h]}」" for h in hits)
            print(f"     ⚑ 用語提醒：{pairs}  （是台灣的正常用法就不用理它）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已有 post.json 也重寫")
    ap.add_argument("slug", nargs="?", help="只做這一篇")
    args = ap.parse_args()

    todo = [t for t in ready() if not args.slug or t[0] == args.slug]
    if not todo:
        print("沒有可寫文案的貼文——先跑「分析全部素材.bat」和「出圖.bat」")
        return 1

    failed = []
    for slug, i in todo:
        h = read_json("highlights", highlights_path(slug))
        post = h["posts"][i - 1]
        print("\n" + "=" * 74)
        print(f"  {h['source']['title'][:52]}")
        print(f"  第 {i} 則：{post['angle']}")
        print("=" * 74)
        t0 = time.perf_counter()
        try:
            path = compose_post(slug, i, force=args.force)
        except PipelineError as e:
            print(f"✗ {e.render()}")
            failed.append(f"{slug} p{i}")
            continue
        print(f"({time.perf_counter() - t0:.1f}s)")
        show(path)

    print("\n" + "=" * 74)
    if failed:
        print(f"  {len(todo) - len(failed)}/{len(todo)} 則完成；失敗：{', '.join(failed)}")
    else:
        print(f"  {len(todo)} 則文案完成。上面就是可以直接複製貼上的成品。")
    print("  最後一關是你：**這些話，你敢不敢用自己的名字發出去？**")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as e:
        print(f"\n✗ {e.render()}")
        raise SystemExit(1) from None

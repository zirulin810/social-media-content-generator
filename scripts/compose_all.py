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

import src.compose.write_post as src_compose  # noqa: E402
from src.compose.write_post import compose_post  # noqa: E402
from src.errors import PipelineError  # noqa: E402
from src.paths import (  # noqa: E402
    PROMPT_DIR,
    highlights_path,
    images_dir,
    is_stale,
    out_root,
    post_path,
)
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
    """**一份文案，兩個平台。** 不要再假裝它們是兩篇東西——差別只有 hashtag。"""
    import json
    import re

    from src.analyze.locale import TERMS

    term_re = re.compile("|".join(sorted(TERMS, key=len, reverse=True)))

    data = json.loads(path.read_text(encoding="utf-8"))
    by = {p["platform"]: p for p in data["posts"]}
    ig, th = by["instagram"], by["threads"]

    ig_body = ig["caption"].split("\n\n#")[0]
    same = ig_body.strip() == th["caption"].strip()

    print(f"\n  出處：{data['source'].get('url', '（無）')}  ← 印在最後一張圖卡上，不放進文案")

    n_img = len(ig["image_paths"])
    print(f"\n  ── 文案（IG 與 Threads 共用，{n_img} 張圖）" + "─" * 34)
    for line in th["caption"].splitlines():
        print(f"  │ {line}")

    tags = ig.get("hashtags") or []
    if tags:
        print("  │")
        print(f"  │ {' '.join(tags)}   ← 只有 IG 加這行")

    hook = th["caption"].splitlines()[0]
    print(f"  └ hook（{len(hook)} 字）：「{hook}」  ← 值得為它停下手指嗎？")

    if not same:
        # Threads 硬上限 500，太長時程式會砍尾巴——**砍了就要說**
        print(f"     ✂ Threads 版被砍短了（{len(th['caption'])} 字），IG 版是完整的")

    # 用語只標記、不改。**同一個詞，語意不同就是兩件事**——機器判不準，你自己看。
    body = th["caption"].split("\n\n原文：")[0]
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
    reused = 0
    for slug, i in todo:
        h = read_json("highlights", highlights_path(slug))
        post = h["posts"][i - 1]
        print("\n" + "=" * 74)
        print(f"  {h['source']['title'][:52]}")
        print(f"  第 {i} 則：{post['angle']}")
        print("=" * 74)

        # **產物沒重跑就要講出來。** 原本這裡只印「(0.0s)」——看起來像跑完了，
        # 其實是直接讀舊檔。**靜靜地拿舊產物充數，比報錯還危險。**
        fresh = not args.force and not is_stale(
            post_path(slug, i),
            highlights_path(slug),
            images_dir(slug, i),
            PROMPT_DIR,
            Path(src_compose.__file__).parent,  # 程式碼也是輸入
        )
        t0 = time.perf_counter()
        try:
            path = compose_post(slug, i, force=args.force)
        except PipelineError as e:
            print(f"✗ {e.render()}")
            failed.append(f"{slug} p{i}")
            continue

        if fresh:
            reused += 1
            print("  ↩ 沿用既有文案（輸入沒變）。要重寫：「產生文案.bat」拖進來加 --force")
        else:
            print(f"  ✎ 重新寫過（{time.perf_counter() - t0:.1f}s）")
        show(path)

    print("\n" + "=" * 74)
    if failed:
        print(f"  {len(todo) - len(failed)}/{len(todo)} 則完成；失敗：{', '.join(failed)}")
    else:
        wrote = len(todo) - reused
        detail = f"（{wrote} 則重寫、{reused} 則沿用舊檔）" if reused else ""
        print(f"  {len(todo)} 則文案完成{detail}。上面就是可以直接複製貼上的成品。")
    print("  最後一關是你：**這些話，你敢不敢用自己的名字發出去？**")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as e:
        print(f"\n✗ {e.render()}")
        raise SystemExit(1) from None

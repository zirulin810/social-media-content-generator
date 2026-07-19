"""拆卡邏輯（純函式，不碰瀏覽器）。

**塞不下就拆卡，不砍內容。** 這是 2026-07-13 跟 Human 吵出來的原則：
版面應該去適應知識，不是反過來把知識閹割成剛好塞得下的樣子。

為什麼要跟 render_cards.py 分開：
    「怎麼拆」是決策，「怎麼截圖」是勞動。
    決策要能在沒有瀏覽器的地方測——把 fits() 抽成參數注入，
    測試就能塞一個假的量尺進來，不必真的開一顆 Chromium。
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .. import settings
from ..errors import ErrorCode, PipelineError

# fits(card) -> True 表示這張卡在可讀性下限之上塞得進版面
FitFn = Callable[[dict[str, Any]], bool]

MAX_SPLITS = 4  # 出廠預設；實際值由後台設定管（_max_splits()）。再多就是內容問題，不是版型的錯


def _max_splits() -> int:
    return int(settings.render("max_splits"))


def _clone(card: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {**card, **over}


def _balanced(items: list[Any], per: int) -> list[list[Any]]:
    """把 items 平均分成 ceil(n/per) 份，而不是「切滿再切剩下的」。

    6 步、每張最多 4 步 → 貪婪切法給 [4, 2]，平均切法給 [3, 3]。
    後者好看得多——最後一張只剩一步的卡片，看起來就像做壞了。
    """
    n = len(items)
    k = -(-n // per)                       # ceil
    base, extra = divmod(n, k)
    out, i = [], 0
    for j in range(k):
        size = base + (1 if j < extra else 0)
        out.append(items[i : i + size])
        i += size
    return out


def _split_steps(card: dict[str, Any], fits: FitFn) -> list[dict[str, Any]]:
    """步驟卡：從中間切開，編號續接。

    5 步塞不下 → 3 + 2，不是砍成 3 步。
    第二張的編號從 4 開始（startIndex），標題重複，右上角標 2/2。
    """
    steps = card["steps"]
    if len(steps) < 2:
        return [card]  # 只有一步還塞不下 → 那是文字本身太長，拆不動

    # 找「每張最多幾步」——從多到少試，取第一個全部都讀得舒服的。
    # 從多到少 = 用最少的卡片數。
    for per in range(len(steps) - 1, 0, -1):
        chunks = _balanced(steps, per)
        if len(chunks) > _max_splits():
            continue
        cards = []
        start = 1
        for i, chunk in enumerate(chunks):
            cards.append(
                _clone(
                    card,
                    steps=chunk,
                    startIndex=start,
                    pager=f"{i + 1} / {len(chunks)}",
                    kicker=card.get("kicker", "步驟") + ("（續）" if i else ""),
                )
            )
            start += len(chunk)
        if all(fits(c) for c in cards):
            return cards

    return [card]  # 拆到剩一步還是塞不下


def _split_point(card: dict[str, Any], fits: FitFn) -> list[dict[str, Any]]:
    """重點卡：說明太長 → **從句號切開**，一句句往卡片裡塞，塞不下就開新的一張。

    切在句號上，不切在字數上——切在句子中間就是把話講一半。
    比起砍字，多一張圖是更便宜的代價：輪播本來就能多放。
    """
    body = card.get("body", "")
    parts = [p for p in re.split(r"(?<=[。！？])", body) if p.strip()]
    if len(parts) < 2:
        return [card]  # 一整句話沒有斷點，切不了

    # 貪婪裝箱：一句句加進去，加到塞不下就收工開新的一張
    chunks: list[str] = []
    cur = ""
    for part in parts:
        trial = cur + part
        if cur and not fits(_clone(card, body=trial)):
            chunks.append(cur)
            cur = part
        else:
            cur = trial
    if cur:
        chunks.append(cur)

    if len(chunks) < 2 or len(chunks) > _max_splits():
        return [card]
    if not all(fits(_clone(card, body=c)) for c in chunks):
        return [card]  # 連單句都塞不下——那是句子本身太長

    n = len(chunks)
    return [
        _clone(
            card,
            body=c,
            pager=f"{i + 1} / {n}",
            kicker=card.get("kicker", "重點") + ("（續）" if i else ""),
        )
        for i, c in enumerate(chunks)
    ]


SPLITTERS = {"steps": _split_steps, "point": _split_point}


# 這些欄位是**渲染器擁有的**，不接受上游帶進來。
# 上游偷偷塞一個 pager 進來，就會印出一個假的「2 / 2」——而且不會有人發現。
RENDERER_OWNED = ("pager", "startIndex")


def plan(card: dict[str, Any], fits: FitFn) -> list[dict[str, Any]]:
    """一張卡進來，一到多張卡出去。

    塞得下 → 原樣回傳。
    塞不下 → 依卡型拆。拆不動 → 拋 RENDER_OVERFLOW（**不默默截掉半個字送出去**）。
    """
    card = {k: v for k, v in card.items() if k not in RENDERER_OWNED}

    if fits(card):
        return [card]

    splitter = SPLITTERS.get(card["type"])
    if splitter:
        cards = splitter(card, fits)
        if len(cards) > 1:
            return cards

    # contrast / quote / cover / outro 拆不了——它們的結構本來就不可分割。
    # 這種時候是內容太長，該回頭改文案，不是把字縮到看不見。
    raise PipelineError(
        ErrorCode.RENDER_OVERFLOW,
        f"{card['type']} 卡塞不進版面，而且拆不開",
        hint="這張卡的文字太長。回頭請模型精簡，或改用可以拆的卡型（point / steps）",
    )


def plan_all(cards: list[dict[str, Any]], fits: FitFn) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in cards:
        out.extend(plan(c, fits))
    return out


__all__ = ["plan", "plan_all", "FitFn", "MAX_SPLITS"]

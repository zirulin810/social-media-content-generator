"""階段 4：highlights.json + p<N>/images/ → p<N>/post.json

實作任務：[[貼文文案產生器]]

**分工原則（2026-07-14 這一整天學到的）：機械的事程式做，判斷的事模型做。**

    程式做：出處標註、hashtag 接到文末、字數上限、簡繁轉換、剝表情符號、圖片清單
    模型做：怎麼把這則貼文的論點講成一段話

**IG 與 Threads 共用同一份文案**（Human 2026-07-14：「IG 太冗長了，可以跟 Threads 共用」）。
一次呼叫產出 hook + 正文 + hashtag；差別只在 Threads 不放 hashtag。

caption 的骨架是程式組的：

    <hook：唯一保證會被讀到的一句>

    <模型寫的正文>

    原文：<標題>｜<作者>
    <連結>

    <hashtags：只有 IG>

**出處是紅線（docs/style.md 第 3 條：不省出處）——不能靠模型記得。**
表情符號也是紅線（第 5 條），而「有沒有 emoji」是機械可驗的，所以程式直接剝掉，
不花一輪 LLM 去請它「不要用 emoji」。
"""

from __future__ import annotations

import json
import os
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..analyze import locale
from ..errors import ErrorCode, PipelineError
from ..llm import LLMFn, current_model, get_llm
from ..paths import (
    PROMPT_DIR,
    article_path,
    highlights_path,
    images_dir,
    is_stale,
    post_path,
)
from ..schema import read_json, validate, write_json

# ---------------------------------------------------------------------------
# 兩種上限，性質完全不同。**混在一起就會發生第一次實跑那種事：12 則全滅，
# 沒有一則是因為內容爛，全部是因為我的數字。**
#
#   平台硬上限   IG 2200、Threads 500。**這是別人定的，超過就發不出去** → 超過才報錯
#   編輯目標     正文 350 字左右。**這是我的偏好** → 寫進 prompt 當建議，不當判決
#
# 第一版我把「500 字」（我自己猜的偏好）寫成硬上限，還在上面疊了精算
# （500 − 出處 − hashtag = 正文只剩 264 字），把模型逼進一個它做不到的框。
# 它自然會寫 500 字。**要求不合理時，該改的是要求。**
# ---------------------------------------------------------------------------
IG_MAX_CHARS = 2200  # Instagram 的真實上限
THREADS_MAX_CHARS = 500  # Threads 的真實上限

IG_FOLD_CHARS = 125  # IG 超過這個長度就折疊成「…更多」——前 125 字要能自己站著

IG_HASHTAG_MAX = 10

# hook 不合格時請它重寫幾次。改不動就放行並印警告——
# **那是編輯品質，不是平台規則**，沒必要為它丟掉整篇。最後一關本來就是人。
MAX_REWRITE_ROUNDS = 2

# ---------------------------------------------------------------------------
# hook = 貼文的第一句。**它的唯一任務是讓人停止滑動。**
#
# 第一版我沒有把它當成一個東西——它只是正文的第一行。結果模型寫出來的都是這種：
#
#     「用『槓鈴策略』駕馭 AI，兼顧防禦與進攻，讓 AI 成為你的思考夥伴。」
#
# **那是摘要，不是 hook。** 太長、太平、沒有張力，沒有人會為它停下手指。
# 而且它只是把 angle 換句話說——angle 是「這則在講什麼」（給人看的索引），
# hook 是「你為什麼該停下來」（給讀者看的鉤子）。兩者的讀者不同，任務不同。
#
# 所以現在 hook 是**獨立欄位**，而且受機器檢查：長度、不准複述 angle。
HOOK_MAX_CHARS = 30

# ---------------------------------------------------------------------------
# **ask / claim 這個二分法已經拿掉了。**
#
# 我原本把語氣切成「提問型」與「斷言型」讓人挑。跑出來之後 Human 指出真正的地雷
# 根本不在句型上：
#
#     ✗「你的筆記散落在各處，從來沒有真正屬於你。」
#     ✗「你的筆記之所以難用，是因為你沒有真正擁有它。」
#
#     「這個才是真正的雷點，因為他們並不一定屬實，而且有點像是在給人戴帽子。」
#
# **他是對的。** 這兩句的問題不是「用了斷言句」，是**替讀者的人生下判斷**——
# 它假裝了解讀者的缺陷，然後叫他來看解方。而讀者沒有否認的餘地。
#
# 對比：「你是不是也把舊筆記全部匯進來了？」是**問句，他可以說沒有**，
# 而且原文真的講過那是常見錯誤。
#
# 所以紅線從「句型」改成「**不准替讀者斷言他的狀態**」，
# 而 hook 的手法直接放行（八種，見 prompts/caption.md），由模型挑最貼合的。
# ---------------------------------------------------------------------------

# 表情符號（含變體選擇子與零寬連接）。紅線 5：圖卡與 caption 皆不用。
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F000-\U0001F0FF"
    "\U00002600-\U000027BF\U00002B00-\U00002BFF️‍]"
)

ROLE_RE = re.compile(r"^(\d{2})_(cover|point|steps|contrast|quote|outro)(?:_(\d+))?\.png$")


def _png_ratio(path: Path) -> str:
    """從 PNG 檔頭讀長寬。只是要兩個整數，不必為此裝 Pillow。"""
    with path.open("rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise PipelineError(ErrorCode.MISSING_INPUT, f"不是 PNG：{path.name}")
    w, h = struct.unpack(">II", head[16:24])
    return "1:1" if w == h else "4:5"


def collect_images(slug: str, post_index: int) -> list[dict[str, Any]]:
    """掃 `p<N>/images/`，把檔名解析成 post.json 的 images 清單。

    **檔名就是契約**（`paths.image_name()` 定的）。這裡不重新發明命名規則，只是讀它。
    """
    d = images_dir(slug, post_index)
    files = sorted(d.glob("*.png")) if d.exists() else []
    if not files:
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            f"第 {post_index} 則貼文還沒有圖卡：{d}",
            hint="先跑「出圖.bat」",
        )

    images: list[dict[str, Any]] = []
    for f in files:
        m = ROLE_RE.match(f.name)
        if not m:
            raise PipelineError(
                ErrorCode.MISSING_INPUT,
                f"圖檔名不符命名規則：{f.name}",
                hint="檔名一律由 paths.image_name() 產生。手動改名會讓下游對不上",
            )
        entry: dict[str, Any] = {
            "path": f"images/{f.name}",
            "role": m.group(2),
            "ratio": _png_ratio(f),
            "bytes": f.stat().st_size,
        }
        if m.group(3):
            entry["card_index"] = int(m.group(3))
        images.append(entry)
    return images


def attribution(source: dict[str, Any]) -> str:
    """出處標註。**程式組的，模型碰不到。**（紅線 3：不省出處）"""
    line = f"原文：{source['title']}｜{source['author']}"
    url = source.get("url")
    return f"{line}\n{url}" if url else line


def _cards_digest(post: dict[str, Any]) -> str:
    """把知識卡攤平成純文字餵給模型。**文案不得超出卡片講過的東西。**"""
    out = []
    for c in post["cards"]:
        t = c["type"]
        if t == "point":
            out.append(f"- [重點] {c['title']}：{c['body']}")
        elif t == "steps":
            steps = "；".join(f"{i}. {s['text']}" for i, s in enumerate(c["steps"], 1))
            out.append(f"- [步驟] {c['title']}：{steps}")
        elif t == "contrast":
            out.append(f"- [對照] {c['title']}：✗ {c['wrong']['text']} ／ ✓ {c['right']['text']}")
        elif t == "quote":
            out.append(f"- [金句] {c['text']}")
    return "\n".join(out)


def build_prompt(post: dict[str, Any], article: dict[str, Any]) -> str:
    """**IG 與 Threads 共用同一份文案**（Human 2026-07-14：「IG 太冗長了，可以跟 Threads 共用」）。

    原本兩個平台各一個 prompt、各叫一次 LLM——結果 IG 那版寫成五段小論文。
    圖卡才是主角，caption 是導讀。**兩邊都不需要一篇論文。**

    共用之後：一次呼叫產出 hook + body + hashtags，
    IG 用 hook + body + 出處 + hashtag；Threads 用 hook + body + 出處。
    （順帶把 API 呼叫減半。）
    """
    template = (PROMPT_DIR / "caption.md").read_text(encoding="utf-8")
    src = article["source"]
    kind = "這支影片" if article.get("origin") == "video_transcript" else "這篇文章"
    return (
        template.replace("{title}", src["title"])
        .replace("{author}", src["author"])
        .replace("{kind}", kind)
        .replace("{angle}", post["angle"])
        .replace("{hook}", post.get("hook", ""))
        .replace("{cards}", _cards_digest(post))
        .replace("{fold}", str(IG_FOLD_CHARS))
        .replace("{hook_max}", str(HOOK_MAX_CHARS))
    )


def _parse(text: str) -> dict[str, Any]:
    """從模型回應挖出 JSON。**對真實換行寬容**。

    文案本來就是多段落的東西，模型很自然會在 JSON 字串裡直接按 Enter：

        {"body": "第一段
        第二段"}

    嚴格說那是不合法的 JSON（控制字元未跳脫）。但**這是可以修的，不是要重跑的**——
    第一次實跑就有一則死在這裡。與其怪模型，不如把換行跳脫掉再解析。
    """
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b == -1:
        raise PipelineError(ErrorCode.SCHEMA_INVALID, "文案回應裡找不到 JSON", hint=text[:120])
    blob = text[a : b + 1]

    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass

    # 把「字串內部的真實換行」換成 \n。用 strict=False 就是允許這件事。
    try:
        return json.loads(blob, strict=False)
    except json.JSONDecodeError as e:
        raise PipelineError(
            ErrorCode.SCHEMA_INVALID,
            f"文案回應不是合法 JSON：{e}",
            hint="檢查 prompts/caption_*.md 的輸出格式說明",
        ) from e


def clean(text: str) -> str:
    """剝表情符號、轉台灣正體、收乾空白。**全是機械的事。**"""
    text = EMOJI.sub("", text)
    text = locale.to_taiwan(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def assemble(
    body: str, attrib: str, tags: list[str] | None = None, hook: str = ""
) -> str:
    parts = [p for p in (hook, body, attrib) if p]
    if tags:
        parts.append(" ".join(tags))
    return "\n\n".join(parts)


_PUNCT = re.compile(r"[\s，。、！？：；「」『』（）()《》…—\-,.!?:;'\"]+")


def restates_angle(hook: str, angle: str) -> bool:
    """hook 是不是只是把 angle 換句話說？

    **angle 和 hook 的讀者不同，任務不同：**

        angle  「這則在講什麼」——給我看的索引（也是封面標題）
        hook   「你為什麼該停下來」——給讀者看的鉤子

    第一版沒分開，模型就把 angle 複述一遍當開場：
    「用『槓鈴策略』駕馭 AI，兼顧防禦與進攻，讓 AI 成為你的思考夥伴。」
    **那是摘要，不是 hook。** 這條機器驗得出來：字元重疊率太高就退回重寫。
    """
    a, h = _PUNCT.sub("", angle), _PUNCT.sub("", hook)
    if not a or not h:
        return False
    if a in h or h in a:
        return True
    shared = len(set(a) & set(h))
    return shared / len(set(a)) >= 0.7  # angle 的字有七成以上出現在 hook 裡


SENTENCE_END = re.compile(r"(?<=[。！？])|\n+")


def fit_by_sentence(body: str, room: int) -> str:
    """把正文砍到 `room` 字以內——**從最後一句開始拿掉，切在句號上。**

    為什麼這一刀要由程式砍：

        **LLM 不會數中文字。** 它看到的是 token，不是字。
        叫它「壓到 400 字以內」，它只能憑感覺——實測 729 → 717 → 561，
        一路逼近但永遠差一點。我等於要求它做一件它做不到的事，然後怪它做不到。

    所以：**句子由模型寫（判斷），最後一刀由程式砍（機械）。**
    絕不切在句子中間——寧可少一句話，也不要發半句話。
    """
    if len(body) <= room:
        return body

    parts = [p for p in SENTENCE_END.split(body) if p and p.strip()]
    kept: list[str] = []
    for p in parts:
        if len("".join(kept)) + len(p) > room:
            break
        kept.append(p)

    # 連第一句都放不下 → 回空字串，讓呼叫端報錯。
    # **不硬切前 N 字**：那會發出半句話，正好違反這個函式存在的理由。
    return "".join(kept).strip()


def wasted_opening(body: str, source: dict[str, Any]) -> str:
    """開頭有沒有被出處吃掉？回傳被浪費掉的那段字（沒有就回空字串）。

    **IG 折疊前只看得到前 125 字。** 第一次實跑，12 則有 11 則這樣開頭：

        「我最近看了 Linking Your Thinking with Nick Milo 的這支影片《Give Me 15 Minutes…》」

    讀者在動態上滑過去，看到的是一長串書名和作者名——**論點一個字都沒露出來。**

    我在 prompt 裡寫了「前 125 字要能自己站著」，它照做了，**卻是用一句廢話站著**：
    我沒禁止它把出處寫進正文，而出處本來就是程式會接在後面的。

    「這句話有沒有意義」是判斷題，機器答不了。
    但「開頭有沒有出現標題／作者」是**機械可驗**的——那就別只靠叮嚀。
    """
    head = body[:IG_FOLD_CHARS]
    for field in ("title", "author"):
        val = str(source.get(field) or "").strip()
        # 長標題只比對前 16 字（模型常常只抄一半，或改了標點）
        probe = val[:16]
        if len(probe) >= 6 and probe in head:
            return probe
    return ""


def check_hook(hook: str, post: dict[str, Any], source: dict[str, Any]) -> str:
    """hook 的機器檢查。回傳「哪裡不合格」（合格就回空字串）。

    **全是機械可驗的東西**——長度、複述、出處。
    「這句話夠不夠有力」機器答不了，那是人的事。
    """
    if not hook:
        return "你沒有寫 hook"
    if len(hook) > HOOK_MAX_CHARS:
        return f"hook {len(hook)} 字，超過 {HOOK_MAX_CHARS} 字——**太長就不是 hook，是摘要**"
    if restates_angle(hook, post["angle"]):
        return (
            f"你的 hook 只是把 angle 換句話說：\n"
            f"      angle：{post['angle']}\n"
            f"      hook ：{hook}\n"
            f"    angle 是「這則在講什麼」，hook 是「**你為什麼該停下來**」"
            f"——沒有人會為一句摘要停下手指"
        )
    if wasted_opening(hook, source):
        return "hook 裡出現了標題或作者名——出處由程式接在文末，寫進 hook 只是浪費那一行"
    return ""


def draft(post: dict[str, Any], article: dict[str, Any], llm: LLMFn) -> tuple[str, str, list[str]]:
    """叫模型寫一份文案（hook + 正文 + hashtag）。**兩個平台共用這一份。**"""
    source = article["source"]
    prompt = build_prompt(post, article)

    for attempt in range(MAX_REWRITE_ROUNDS + 1):
        raw = _parse(llm(prompt))

        hook = clean(str(raw.get("hook", "")))
        body = clean(str(raw.get("body", "")))
        if not body:
            raise PipelineError(ErrorCode.SCHEMA_INVALID, "文案是空的")

        tags: list[str] = []
        for t in raw.get("hashtags") or []:
            t = EMOJI.sub("", locale.to_taiwan(str(t))).strip().replace(" ", "")
            if t:
                tags.append(t if t.startswith("#") else f"#{t}")
        tags = tags[:IG_HASHTAG_MAX]

        bad = check_hook(hook, post, source)
        if not bad:
            stolen = wasted_opening(body, source)
            if stolen:
                bad = f"正文開頭出現了「{stolen}…」——那是出處，程式會自動接在文末"

        if not bad:
            return hook, body, tags

        if attempt == MAX_REWRITE_ROUNDS:
            # 改不動就放行，但**把問題印出來**——這是編輯品質，不是平台規則，
            # 沒必要為它丟掉整篇。最後一關本來就是人。
            print(f"    ⚠ hook 仍不合格（{bad.splitlines()[0][:44]}）→ 照樣輸出，請你自己看")
            return hook, body, tags

        print(f"    hook 不合格 → 重寫（{bad.splitlines()[0][:44]}）")
        prompt = (
            f"{build_prompt(post, article)}\n\n---\n\n"
            f"# 你的開場白不合格\n\n{bad}\n\n"
            f"**重寫 hook：{HOOK_MAX_CHARS} 字內、一句話、挑一種手法（痛點／反直覺／結果／"
            f"共鳴提問／代價／破除誤解／門檻很低／作者原話），依據要在卡片裡找得到。**\n"
            f"**不准替讀者斷言他的狀態**（「你的筆記從來沒有真正屬於你」＝戴帽子）。\n"
            f"正文可以保留。再輸出一次完整的 JSON。"
        )

    raise AssertionError("unreachable")  # pragma: no cover


def write_one(
    platform: str,
    hook: str,
    body: str,
    tags: list[str],
    article: dict[str, Any],
    images: list[dict[str, Any]],
) -> dict[str, Any]:
    """把同一份文案裝進某個平台的殼裡。**不再呼叫 LLM。**"""
    attrib = attribution(article["source"])
    ig = platform == "instagram"
    limit = IG_MAX_CHARS if ig else THREADS_MAX_CHARS  # 平台的硬上限，不是我的偏好
    tags = tags if ig else []  # Threads 不放 hashtag

    caption = assemble(body, attrib, tags, hook=hook)

    if len(caption) > limit:
        # 幾乎只會發生在 Threads（硬上限 500）。**砍正文，不砍 hook**——
        # hook 是這則貼文唯一保證會被讀到的東西。切在句號上，絕不切在句子中間。
        room = limit - len(attrib) - len(hook) - 6 - sum(len(t) + 1 for t in tags)
        trimmed = fit_by_sentence(body, room)
        if not trimmed:
            raise PipelineError(
                ErrorCode.SCHEMA_INVALID,
                f"{platform}：第一句話就超過 {room} 字，砍不動（平台上限 {limit}）",
                hint="改 prompts/caption.md：要它寫短句，不要一句話寫成一整段",
            )
        print(f"    {platform} 砍掉最後 {len(body) - len(trimmed)} 字（切在句號上）")
        body = trimmed
        caption = assemble(body, attrib, tags, hook=hook)

    out: dict[str, Any] = {
        "platform": platform,
        "caption": caption,
        "image_paths": [i["path"] for i in images],
        "attribution": attrib,
    }
    if tags:
        out["hashtags"] = tags
    return out


def compose_post(slug: str, post_index: int, llm: LLMFn | None = None, force: bool = False) -> Path:
    """一則貼文 → 一份 post.json（Instagram + Threads 兩版）。"""
    path = post_path(slug, post_index)

    # **跳過的條件是「產物比所有輸入都新」，不是「檔案存在」。**
    # prompt 也是輸入——改了 prompt 卻沒重跑，等於拿舊文案當新的用。
    inputs = (highlights_path(slug), images_dir(slug, post_index), PROMPT_DIR)
    if not force and not is_stale(path, *inputs):
        return path

    llm = llm or get_llm()
    article = read_json("article", article_path(slug))
    h = read_json("highlights", highlights_path(slug))

    if post_index > len(h["posts"]):
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            f"這篇只有 {len(h['posts'])} 則貼文，沒有第 {post_index} 則",
        )

    post = h["posts"][post_index - 1]
    images = collect_images(slug, post_index)

    # **一次呼叫，兩個平台共用**（Human：「IG 太冗長了，可以跟 Threads 共用」）。
    hook, body, tags = draft(post, article, llm)

    data = {
        "schema_version": "3.0",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "model": current_model(),
        "source": h["source"],
        "post_index": post_index,
        "angle": post["angle"],
        "images": images,
        "posts": [
            write_one("instagram", hook, body, tags, article, images),
            write_one("threads", hook, body, tags, article, images),
        ],
    }
    validate("post", data)
    return write_json("post", path, data)


def compose(slug: str, force: bool = False, llm: LLMFn | None = None) -> list[Path]:
    """把這篇文章的每一則貼文都寫成文案。"""
    h = read_json("highlights", highlights_path(slug))
    return [compose_post(slug, i, llm=llm, force=force) for i in range(1, len(h["posts"]) + 1)]


__all__ = [
    "compose",
    "compose_post",
    "collect_images",
    "attribution",
    "assemble",
    "clean",
    "draft",
    "write_one",
    "check_hook",
    "restates_angle",
    "fit_by_sentence",
    "wasted_opening",
    "build_prompt",
    "HOOK_MAX_CHARS",
    "THREADS_MAX_CHARS",
    "IG_MAX_CHARS",
    "IG_FOLD_CHARS",
]

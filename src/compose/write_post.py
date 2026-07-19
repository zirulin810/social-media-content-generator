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

import difflib
import json
import os
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import settings
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

IG_HASHTAG_MAX = 10  # 出廠預設；實際值走設定（generation.hashtags_max）


def _hashtags_max() -> int:
    return int(settings.gen("hashtags_max"))

# hook 不合格時請它重寫幾次。改不動就放行並印警告——
# **那是編輯品質，不是平台規則**，沒必要為它丟掉整篇。最後一關本來就是人。
MAX_REWRITE_ROUNDS = 2  # 出廠預設；實際值走設定（caption.rewrite_rounds）


def _rewrite_rounds() -> int:
    return int(settings.cap("rewrite_rounds"))

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
# 所以現在 hook 是**獨立欄位**，而且受機器檢查。
#
# **但檢查的是「結構」，不是「字數」**（Human 2026-07-14：「hook 幹嘛定字數，
# 就一句話不就得了」——他是對的）：
#
#   一句話  → 不換行、句末標點最多一個  ← **機器驗得出來，而且不用猜任何數字**
#   看得完  → 不超過 IG 的折疊線 125 字  ← **Instagram 定的，不是我猜的**
#
# 我原本把「一句話」翻譯成「30 字」，然後那個數字開始咬人：
# 模型寫 31–35 字，重寫三次都跨不過去，我照樣出貨——**三次 LLM 呼叫，零改善。**
# **「一句話」是結構，「30 字」是我對結構的猜測。驗結構就好，不要猜。**
HOOK_TARGET_CHARS = 25  # 出廠預設；實際值由後台設定管（見 _hook_target()），程式不拿它擋人


def _hook_target() -> int:
    """hook 的目標字數——人的偏好，住在後台設定（[[編輯台後台設定]]）。"""
    return int(settings.gen("hook_target"))
HOOK_MAX_CHARS = IG_FOLD_CHARS  # 硬上限＝折疊線：hook 被折疊就等於沒寫

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


def collect_images(slug: str, post_index: int, original: bool = False) -> list[dict[str, Any]]:
    """掃 `p<N>/images/`，把檔名解析成 post.json 的 images 清單。

    **檔名就是契約**（`paths.image_name()` 定的）。這裡不重新發明命名規則，只是讀它。

    `original=True`＝人已在編輯台明確宣告「這則是我的原創」（highlights 的 `post.original`，
    v3.4）——只有這種貼文允許沒有結尾卡。預設一律要求出處。
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

    # **紅線：不省出處。**
    # caption 不再帶出處了（Human 2026-07-14），所以整則貼文的出處**只剩結尾卡在扛**。
    # 那張卡不見的話，這則貼文就變成沒有標註來源的轉貼——**那是版權問題，不是風格問題。**
    # 唯一的例外：人已明確宣告原創（original）——原創沒有「別人的出處」可標。
    if not original and not any(i["role"] == "outro" for i in images):
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            f"第 {post_index} 則沒有結尾卡（outro），出處會消失",
            hint="出處只剩結尾卡在扛（caption 不再帶）。重跑「出圖.bat」",
        )
    return images


def attribution(source: dict[str, Any]) -> str:
    """出處標註。留在 `post.json` 裡當紀錄，**但不再貼進 caption**。

    **2026-07-14 Human：「文案當中不用放來源跟作者，圖片最後一張其實就有了。」**

    他是對的：出處已經印在**結尾卡**上（`templates/card.js` 的 outro），
    caption 再放一次是重複的。而且那個網址在 IG 上根本不能點，
    貼在文案裡只是佔字數——142 字，吃掉正文預算的三分之一。

    **紅線「不省出處」沒有鬆，是執行點搬家了**：
    從「caption 必須帶出處」改成「**結尾卡必須存在**」（見 `collect_images()`）。
    """
    # **作者選填**：Google 課程、官方文件、白皮書常常沒有個人作者。
    # 沒有就不印那一段——不要留一個孤零零的「｜」，那看起來像出錯。
    author = source.get("author")
    line = f"原文：{source['title']}｜{author}" if author else f"原文：{source['title']}"
    url = source.get("url")
    return f"{line}\n{url}" if url else line


# 卡片文字裡的 `**重點**` 是**版型的語言**（card.js 渲染成螢光筆），不是文案的。
# 餵給文案模型前要剝掉——不剝的話模型會有樣學樣，把星號原封寫進 caption，
# 而 IG／Threads 不渲染 markdown，讀者看到的就是兩顆星號。
_MARK = re.compile(r"\*\*(.+?)\*\*")


def _plain(s: str) -> str:
    return _MARK.sub(r"\1", s)


def _cards_digest(post: dict[str, Any]) -> str:
    """把知識卡攤平成純文字餵給模型。**文案不得超出卡片講過的東西。**"""
    out = []
    for c in post["cards"]:
        t = c["type"]
        if t == "point":
            out.append(f"- [重點] {_plain(c['title'])}：{_plain(c['body'])}")
        elif t == "steps":
            steps = "；".join(f"{i}. {_plain(s['text'])}" for i, s in enumerate(c["steps"], 1))
            out.append(f"- [步驟] {_plain(c['title'])}：{steps}")
        elif t == "contrast":
            out.append(f"- [對照] {_plain(c['title'])}：✗ {_plain(c['wrong']['text'])} ／ ✓ {_plain(c['right']['text'])}")
        elif t == "quote":
            out.append(f"- [金句] {_plain(c['text'])}")
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
    budget = body_budget("字" * HOOK_MAX_CHARS)  # 最壞情況（hook 寫滿）下的預算
    return (
        template.replace("{title}", src["title"])
        .replace("{author}", src.get("author") or "（沒有標明作者）")
        .replace("{kind}", kind)
        .replace("{angle}", post["angle"])
        .replace("{hook}", post.get("hook", ""))
        .replace("{cards}", _cards_digest(post))
        .replace("{fold}", str(IG_FOLD_CHARS))
        .replace("{hook_max}", str(_hook_target()))  # 給模型看「目標」，不是硬上限
        .replace("{para_max}", str(_para_target()))
        .replace("{body_max}", str(budget))
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


def clean(text: str, keep_marks: bool = False) -> str:
    """剝表情符號、剝 `**` 標記、轉台灣正體、收乾空白。**全是機械的事。**

    `**` 是圖卡版型的螢光筆語法（card.js 渲染成 `<mark>`）——它只屬於圖卡：
    - caption 是純文字，IG／Threads 不渲染 markdown，星號留著就會被讀者看到 → 預設剝掉
    - **圖卡文字的建議**（編輯台）裡它是合法的重點標記 → `keep_marks=True` 保留
    """
    text = EMOJI.sub("", text)
    if not keep_marks:
        text = _MARK.sub(r"\1", text)
    text = locale.to_taiwan(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def assemble(body: str, tags: list[str] | None = None, hook: str = "") -> str:
    """caption ＝ hook ＋ 正文 ＋（IG 才有的）hashtag。**沒有出處。**

    出處在結尾卡上（見 `attribution()` 的說明）。
    """
    parts = [p for p in (hook, body) if p]
    if tags:
        parts.append(" ".join(tags))
    return "\n\n".join(parts)


_PUNCT = re.compile(r"[\s，。、！？：；「」『』（）()《》…—\-,.!?:;'\"]+")

# 句末標點出現在**句子中間**＝這不只一句話。（結尾的那個不算，所以先 rstrip 掉。）
SENTENCE_SPLIT = re.compile(r"[。！？](?!$)")


def restates_angle(hook: str, angle: str) -> bool:
    """hook 是不是只是把 angle 換句話說？

    **angle 和 hook 的讀者不同，任務不同：**

        angle  「這則在講什麼」——給我看的索引（也是封面標題）
        hook   「你為什麼該停下來」——給讀者看的鉤子

    要抓的是這種：
        angle「用槓鈴策略駕馭 AI」→ hook「用『槓鈴策略』駕馭 AI，兼顧防禦與進攻…」
        **那是摘要，不是 hook。**

    **不能抓的是這種**（第一版誤殺了它們）：
        angle「讓 AI 讀懂你的筆記庫」→ hook「AI 每次都要你重講一遍你是誰？」
        它跟 angle 共用關鍵字是**必然的**——講的是同一件事。但它是痛點，不是摘要。

    第一版用「字元集合重疊率 ≥ 0.7」，於是**任何講同一個主題的 hook 都被判複述**。
    實跑時它連殺兩個好 hook，模型重寫三次都過不了關。
    **判斷器太嚴，模型就只能亂猜——而它猜不到我心裡想的那條線。**

    改成只抓「幾乎一模一樣」：angle 整句被塞進 hook，或兩者相似度 ≥ 0.75。
    """
    a, h = _PUNCT.sub("", angle), _PUNCT.sub("", hook)
    if not a or not h:
        return False
    if a in h or h in a:
        return True
    return difflib.SequenceMatcher(None, a, h).ratio() >= 0.75


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


# **讀者看不到那支影片。** 他滑到的是輪播圖。
#
# 這幾種句子把文案變成「一個他看不到的東西的導覽」：
#   「這支影片展示了…」「影片建議…」「就像作者所說…」「文章中提到…」
#
# 我會犯這個錯，是因為 prompt 從頭到尾都在叫模型「幫我整理**這支影片**」——
# **它就真的變成影片解說員了。**
#
# 誠實由文末的出處標註負責（「原文：《標題》｜作者 + 連結」），
# 不需要在每一句話裡再提醒讀者「這是別人講的」。
NARRATION = re.compile(
    r"這支影片|這篇文章|本影片|本文章|片中|"
    r"影片(中|裡|提到|展示|建議|教|點出|分享|介紹|說)|"
    r"文章(中|裡|提到|展示|建議|教|點出|分享|介紹|說)|"
    r"作者(所?說|提到|建議|示範|強調|認為|分享|指出)|"
    r"原文提到|(正如|就像|如同).{0,12}?所說"  # 「正如 Nick Milo 所說」——人名可能很長
)


def narrates_the_source(body: str) -> str:
    """文案在幫讀者看不到的東西做導覽嗎？回傳第一句犯規的話（沒有就回空字串）。"""
    m = NARRATION.search(body)
    return m.group(0) if m else ""


# 正文的形狀。**「2–3 段」是可以驗的，別只靠叮嚀。**
#
# 段落長度也是兩個數字：
#   PARA_TARGET  100 字——寫進 prompt 的建議
#   PARA_MAX     150 字——硬上限，超過就真的是一坨了
#
# 第一版只有「110」當硬上限，結果模型寫 111、115、118——**全部差一點點**，
# 重寫三次都跨不過我那條隨手畫的線。
BODY_MIN_PARAS = 2      # 以下四個都是出廠預設；實際值走設定的 caption 區
BODY_MAX_PARAS = 4
PARA_TARGET_CHARS = 100
PARA_MAX_CHARS = 150


def _paras_min() -> int:
    return int(settings.cap("body_paras_min"))


def _paras_max() -> int:
    return int(settings.cap("body_paras_max"))


def _para_target() -> int:
    return int(settings.cap("para_target"))


def _para_max() -> int:
    return int(settings.cap("para_max"))

# hook 已經在鉤讀者了，正文不必再鉤一次。
# 而且實跑時它在正文又問了一句「你是不是也覺得知識很難累積？」——**卡片沒講過這件事。**
SECOND_HOOK = re.compile(r"你是不是也|你有沒有(發現|遇過)|你是否也|想像一下")


def body_budget(hook: str) -> int:
    """正文最多能寫幾個字——**由 Threads 的硬上限倒推出來。**

    兩個平台共用一份文案，所以**它必須塞得進比較小的那個框**（Threads 500 字）。

        500 − hook（約 30 字）− 空行 ≈ 465 字

    但**這是天花板，不是目標**：正文的編輯規格是「2–3 段、每段 ≤110 字」＝ 約 330 字。
    天花板只負責擋住「塞不進 Threads」這種硬性失敗。

    （出處拿掉之前，這個預算只剩 320 字——而我同時叫模型寫「每段最多 160 字」＝ 480 字。
    **規格從一開始就自相矛盾**，於是程式在下游把 Threads 版砍短，
    「兩個平台共用一份」就這樣被我自己的規格拆散了。）
    """
    return THREADS_MAX_CHARS - len(hook) - 4


def check_body(body: str, budget: int = 0) -> str:
    """正文的形狀對不對？回傳哪裡不對（沒問題就回空字串）。

    「這段話好不好」機器答不了。但「有沒有分段」「有沒有一坨 300 字」
    「塞不塞得進 Threads」「是不是又在正文裡鉤一次讀者」——**這些都是機械可驗的。**
    """
    if budget and len(body) > budget:
        return (
            f"正文 {len(body)} 字，超過 {budget} 字。\n"
            f"    （Threads 的硬上限是 500 字，扣掉出處和 hook 之後，正文只剩這麼多。"
            f"**兩個平台共用一份文案，所以它必須塞得進比較小的那個框。**）\n"
            f"    **砍掉一整段或一整句，不要縮寫成流水帳。**"
        )

    paras = [p.strip() for p in body.split("\n\n") if p.strip()]

    if len(paras) < _paras_min():
        return (
            f"正文只有 {len(paras)} 段，全部黏成一坨（{len(body)} 字）。"
            f"**要 {_paras_min()}–{_paras_max() - 1} 段，段落之間空一行。**"
        )
    if len(paras) > _paras_max():
        return f"正文有 {len(paras)} 段，太碎了。**{_paras_min()}–{_paras_max() - 1} 段就好。**"

    for i, p in enumerate(paras, 1):
        if len(p) > _para_max():
            return (
                f"第 {i} 段有 {len(p)} 字，超過硬上限 {_para_max()}——**那不是段落，是一坨。**\n"
                f"    **不要試著數字數**（你數不準）。改用結構：**那一段拆成兩段，"
                f"或者砍掉一整句。**"
            )

    m = SECOND_HOOK.search(body)
    if m:
        return (
            f"正文裡又鉤了一次讀者（「{m.group(0)}…」）。**hook 已經在做這件事了。**\n"
            f"    正文的任務是**兌現 hook 的承諾**，不是再鉤一次——"
            f"而且那句話的處境，卡片裡不見得有。"
        )
    return ""


def check_hook(hook: str, post: dict[str, Any], source: dict[str, Any]) -> str:
    """hook 的機器檢查。回傳「哪裡不合格」（合格就回空字串）。

    **全是機械可驗的東西**——長度、複述、出處。
    「這句話夠不夠有力」機器答不了，那是人的事。
    """
    if not hook:
        return "你沒有寫 hook"
    # **驗結構，不驗字數。**「一句話」是機器看得出來的；「30 字」是我對它的猜測。
    if "\n" in hook:
        return "hook 要是一句話，不能換行"

    if len(SENTENCE_SPLIT.findall(hook.rstrip("。！？"))) > 0:
        return (
            f"hook 有不只一句話：{hook}\n"
            f"    **一句話，只講一個意思。** 講兩件事的那叫摘要，不叫鉤子。"
        )

    if len(hook) > HOOK_MAX_CHARS:
        # 這個上限**不是我猜的**：IG 折疊線在 125 字，hook 被折疊就等於沒寫。
        return (
            f"hook {len(hook)} 字，超過 IG 的折疊線（{HOOK_MAX_CHARS} 字）——**讀者根本看不到它**。\n"
            f"    砍成一句話。"
        )
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

    rounds = _rewrite_rounds()
    for attempt in range(rounds + 1):
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
        tags = tags[:_hashtags_max()]

        bad = check_hook(hook, post, source)
        if not bad:
            stolen = wasted_opening(body, source)
            if stolen:
                bad = f"正文開頭出現了「{stolen}…」——那是出處，程式會自動接在文末"

        if not bad:
            narration = narrates_the_source(hook + "\n" + body)
            if narration:
                bad = (
                    f"你在幫一個讀者看不到的東西做導覽：文案裡出現「{narration}」。\n"
                    f"    **讀者滑到的是輪播圖，不是{'影片' if '影片' in narration else '原文'}。**"
                    f"他不會為了看懂這段文字去點開它。\n"
                    f"    直接跟他講那個想法本身——出處由程式接在文末，誠實已經由那一行負責了。"
                )

        if not bad:
            bad = check_body(body, budget=body_budget(hook))

        if not bad:
            return hook, body, tags

        if attempt == rounds:
            # 改不動就放行，但**把問題印出來**——這是編輯品質，不是平台規則，
            # 沒必要為它丟掉整篇。最後一關本來就是人。
            print(f"    ⚠ 仍不合格（{bad.splitlines()[0][:44]}）→ 照樣輸出，請你自己看")
            return hook, body, tags

        print(f"    文案不合格 → 重寫（{bad.splitlines()[0][:44]}）")
        prompt = (
            f"{build_prompt(post, article)}\n\n---\n\n"
            f"# 你上一次的稿子不合格\n\n{bad}\n\n"
            f"這是你上一次寫的：\n\n"
            f"    hook：{hook}\n"
            f"    正文：{body[:300]}\n\n"
            f"**請修正上面指出的問題，重新輸出完整的 JSON。**\n"
            f"（hook：{HOOK_MAX_CHARS} 字內、一句話、痛點／反直覺／結果／共鳴提問／代價／"
            f"破除誤解／門檻很低／作者原話，依據要在卡片裡找得到；"
            f"**不准替讀者斷言他的狀態**，「你的筆記從來沒有真正屬於你」＝戴帽子。）"
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
    """把同一份文案裝進某個平台的殼裡。**不再呼叫 LLM。**

    兩個平台的差別**只剩 hashtag**（Threads 不放）。
    正文由 `draft()` 保證塞得進 Threads，所以這裡不該再砍任何東西——
    真的砍到了，就是上游的預算算錯了。
    """
    attrib = attribution(article["source"])  # 留在 post.json 當紀錄，不進 caption
    ig = platform == "instagram"
    limit = IG_MAX_CHARS if ig else THREADS_MAX_CHARS  # 平台的硬上限，不是我的偏好
    tags = tags if ig else []  # Threads 不放 hashtag

    caption = assemble(body, tags, hook=hook)

    if len(caption) > limit:
        # **走到這裡就是 bug**：draft() 應該已經保證正文塞得進 Threads 了。
        # 但寧可砍在句號上出貨，也不要讓整批掛掉——並且把它印出來，不要靜靜地發生。
        room = limit - len(hook) - 4 - sum(len(t) + 1 for t in tags)
        trimmed = fit_by_sentence(body, room)
        if not trimmed:
            raise PipelineError(
                ErrorCode.SCHEMA_INVALID,
                f"{platform}：第一句話就超過 {room} 字，砍不動（平台上限 {limit}）",
                hint="改 prompts/caption.md：要它寫短句，不要一句話寫成一整段",
            )
        print(
            f"    ⚠ {platform} 還是太長，砍掉最後 {len(body) - len(trimmed)} 字"
            f"——**這是 bug：draft() 的預算算錯了**"
        )
        body = trimmed
        caption = assemble(body, tags, hook=hook)

    out: dict[str, Any] = {
        "platform": platform,
        "caption": caption,
        "image_paths": [i["path"] for i in images],
        "attribution": attrib,  # 紀錄用；出處印在結尾卡上，不在 caption 裡
    }
    if tags:
        out["hashtags"] = tags
    return out


def compose_post(slug: str, post_index: int, llm: LLMFn | None = None, force: bool = False) -> Path:
    """一則貼文 → 一份 post.json（Instagram + Threads 兩版）。"""
    path = post_path(slug, post_index)

    # **LLM 永遠不覆蓋人的編輯**（[[發布前預覽介面]] 的紅線，在資料層執行）。
    # 人在編輯台改過的文案標著 `human_edited`——那份 caption 已經是「人的最終版」。
    # 重出圖之後 post.json 會過期，但過期的只有**圖片清單**，不是人寫的字：
    # 所以這裡只更新 images / image_paths，caption 一個字都不碰，也不叫 LLM。
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = {}
        if old.get("human_edited"):
            h = read_json("highlights", highlights_path(slug))
            hpost = h["posts"][post_index - 1] if post_index <= len(h["posts"]) else {}
            images = collect_images(slug, post_index, original=bool(hpost.get("original")))
            old["images"] = images
            for pp in old.get("posts", []):
                pp["image_paths"] = [i["path"] for i in images]
            validate("post", old)
            return write_json("post", path, old)

    # **跳過的條件是「產物比所有輸入都新」，不是「檔案存在」。**
    #
    # 輸入有四個，一個都不能漏：
    #   highlights.json  內容
    #   images/          圖卡（post.json 要列出它們）
    #   prompts/         改了 prompt 就該重寫
    #   **這個模組本身**  改了 hook 的檢查邏輯、砍字規則……產物一樣過期
    #
    # 最後一項是 Human 追問「新舊偵測是不是壞了」才補上的。
    # **程式碼也是輸入。** 漏掉它，你會拿到一份「用舊邏輯生成、看起來很新」的東西——
    # 而那正是最難發現的一種壞掉。
    inputs = (
        highlights_path(slug),
        images_dir(slug, post_index),
        PROMPT_DIR,
        settings.path(),        # 參數（hook 目標等）也是輸入
        Path(__file__).parent,  # src/compose/
    )
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
    images = collect_images(slug, post_index, original=bool(post.get("original")))

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
    # 人宣告原創的貼文沒有「別人的出處」可記——attribution 欄位改記宣告本身，
    # 讓紀錄誠實反映狀態，而不是留一行不再成立的出處。
    if post.get("original"):
        for pp in data["posts"]:
            pp["attribution"] = "（人已於編輯台宣告：此貼文為原創內容，無外部出處）"
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

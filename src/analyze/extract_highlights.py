"""階段 2：article.json → highlights.json

實作任務：[[重點分析與金句抽取]]

**v3：產出的不是金句，是可運用的知識。**
一篇文章依資訊密度切成 1–3 則貼文，每則由知識卡組成（point / steps / contrast / quote）。

契約（見 docs/spec.md）：
- 每一條主張各自帶 `evidence`（原文段落 + 逐字原句）
- **機器不攔截**：對照結果標成 ✓／✗ 給人看，過不過由人決定（見 grounding.py）
- prompt 放 prompts/highlights.md，不寫死在程式裡
- 一律輸出繁體中文；英文／簡體原文 → 卡片是譯文、evidence 保留原文
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import settings
from ..errors import ErrorCode, PipelineError
from ..llm import LLMFn, current_model, get_llm
from ..paths import PROMPT_DIR, article_path, highlights_path, is_stale
from ..schema import read_json, validate, write_json
from . import locale
from .grounding import Finding, check, iter_claims, review

# 這幾個執行參數住在後台設定的「進階」區（[[編輯台後台設定]]）；
# STRICT_GROUNDING 環境變數仍可蓋過設定（除錯的手動排檔）。
def _chunk_threshold() -> int:
    """超過這個長度才需要分段。實測最長素材 2 萬字，一次塞得下。"""
    return int(settings.adv("chunk_threshold"))


def _strict() -> bool:
    """預設不擋（只標給人看）。要機器擋：設定頁開嚴格模式，或 STRICT_GROUNDING=1。"""
    env = os.environ.get("STRICT_GROUNDING", "")
    if env not in ("", "0", "false"):
        return True
    return bool(settings.adv("strict_grounding"))


def _json_retries() -> int:
    """模型吐出爛 JSON 是隨機的（漏跳脫一個引號就整份爛掉）。重試通常就好了。"""
    return int(settings.adv("json_retries"))


def _repair_rounds() -> int:
    """schema 不合不必整批重想——把錯誤餵回去請它修那幾個地方。"""
    return int(settings.adv("repair_rounds"))


def build_prompt(
    article: dict[str, Any],
    brief: str | None = None,
    briefs: list[str] | None = None,
) -> str:
    """把 prompt 範本與文章組起來。段落帶著 [index]——那是 evidence 的定錨點。

    `briefs`＝**每則一格的題目與走向**（[[編輯台改版第二輪]]：則數由介面明定，
    不再讓模型自己決定切幾則）。清單長度＝要產出幾則；某格留白＝那一則的論點由模型判斷。
    沒給 briefs（批次腳本）→ 則數用設定的 posts_max。
    `brief`（整段自由文字）保留相容：附加為全域指示。
    """
    template = (PROMPT_DIR / "highlights.md").read_text(encoding="utf-8")
    body = "\n\n".join(f"[{p['index']}] {p['text']}" for p in article["paragraphs"])
    prompt = (
        template.replace("{title}", article["source"]["title"])
        .replace("{author}", article["source"].get("author") or "（沒有標明作者）")
        .replace("{language}", article["language"])
        .replace("{paragraphs}", body)
    )
    # 生成參數來自後台設定（[[編輯台後台設定]]）：prompt 檔裡只有 {變數}，
    # 數字的唯一事實來源是 settings.json——schema 的物理上限也讀同一份，兩邊不會走散。
    gen = settings.load()["generation"]
    # 則數是**明定的**，不是模型判斷的（[[編輯台改版第二輪]]）：
    # 拖素材時每則一格 → len(briefs)；批次腳本沒給 → 設定的 posts_max。
    n = max(1, len(briefs)) if briefs is not None else int(gen["posts_max"])
    if n <= 1:
        posts_rule = (
            "**一篇素材固定產出 1 則貼文——不多不少。** 先讀完全文，挑出「讀者最能帶走」的單一論點來做；\n"
            "其餘內容捨棄——寧可少而完整，不要多而零碎。"
        )
        overflow_rule = "內容多到放不下 → **忍痛割愛**：留最重要的，砍掉其餘。一篇只出一則。"
    else:
        posts_rule = (
            f"**一篇素材固定產出 {n} 則貼文——不多不少。** 每則各自成立：\n"
            "一則講一個論點，講到讀者能照做；各則之間不重複。"
        )
        overflow_rule = f"內容多到放不下 → **忍痛割愛**：這 {n} 則裝不下的就捨棄，不要加開。"
    if briefs and any(b.strip() for b in briefs):
        specs = []
        for i, b in enumerate(briefs, 1):
            spec = b.strip() or "（未指定——由你判斷這一則的最佳論點）"
            specs.append(f"- 第 {i} 則：{spec}")
        posts_rule += "\n\n人指定的各則題目與走向（**優先於你自己的判斷**）：\n" + "\n".join(specs)
    prompt = prompt.replace("{posts_rule}", posts_rule).replace("{overflow_rule}", overflow_rule)
    for key, val in gen.items():
        prompt = prompt.replace("{" + key + "}", str(val))
    if brief and brief.strip():
        prompt += (
            "\n\n---\n\n# 人指定的題目與走向（優先於你自己的判斷）\n\n"
            f"{brief.strip()}\n\n"
            "切幾則、每則講什麼，**照上面的指定來**；指定沒講到的部分你才自己判斷。\n"
            "「不能超出原文說過的」這條紅線不因此放鬆。"
        )
    return prompt


def parse_response(text: str) -> dict[str, Any]:
    """從模型回應裡挖出 JSON。模型偶爾會加圍欄或前言，容忍一下，但不猜。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise PipelineError(
            ErrorCode.SCHEMA_INVALID,
            "模型回應裡找不到 JSON",
            hint=f"回應開頭：{text[:120]}",
        )
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise PipelineError(
            ErrorCode.SCHEMA_INVALID,
            f"模型回應不是合法 JSON：{e}",
            hint="檢查 prompts/highlights.md 的輸出格式說明",
        ) from e


def _dump_raw(slug: str, text: str) -> None:
    """把模型的原始回應存起來。JSON 爛掉時，這是唯一看得到真相的地方。"""
    try:
        path = article_path(slug).parent / "_raw_response.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:  # pragma: no cover — 除錯用的東西不該讓主流程死掉
        pass


def _ask_for_json(llm: LLMFn, prompt: str, slug: str) -> dict[str, Any]:
    """問模型、拿 JSON。爛 JSON 就重跑（漏跳脫一個引號是隨機事件）。"""
    retries = _json_retries()
    for attempt in range(retries + 1):
        response = llm(prompt)
        _dump_raw(slug, response)  # 出事時看得到模型到底吐了什麼
        try:
            return parse_response(response)
        except PipelineError as e:
            if attempt == retries:
                raise
            print(f"    模型吐出爛 JSON（{e.message[:48]}）→ 重跑第 {attempt + 2} 次")
    raise AssertionError("unreachable")  # pragma: no cover


def _repair_prompt(original: str, bad: dict[str, Any], errors: str) -> str:
    """把 schema 的錯誤原封不動餵回去，叫模型改那幾個地方。

    比整批重想便宜，也比整批重想準——它已經讀完文章了，只是某個欄位超了字數。
    """
    return (
        f"{original}\n\n"
        "---\n\n"
        "# 你上一次的輸出不符規格\n\n"
        "這是你剛才給的 JSON：\n\n"
        f"```json\n{json.dumps(bad, ensure_ascii=False, indent=2)[:12000]}\n```\n\n"
        "程式驗出這些問題：\n\n"
        f"```\n{errors}\n```\n\n"
        "**請只修正這些問題，其他內容保持不變。** 超字數的就精簡，缺欄位的就補上。\n"
        "再輸出一次完整的 JSON。"
    )


def analyze(
    article: dict[str, Any],
    llm: LLMFn | None = None,
    brief: str | None = None,
    briefs: list[str] | None = None,
) -> dict[str, Any]:
    """跑分析，回傳 highlights 內容（不落地）。"""
    llm = llm or get_llm()

    total = sum(len(p["text"]) for p in article["paragraphs"])
    if total > _chunk_threshold():
        raise PipelineError(
            ErrorCode.ARTICLE_TOO_SHORT,  # 借用；長度問題
            f"文章 {total} 字，超過單次上限 {_chunk_threshold()}",
            hint="目前沒有分段實作——實測最長素材 20,749 字，一次塞得下。真的遇到再說",
        )

    prompt = build_prompt(article, brief, briefs)
    slug = article["source"]["slug"]
    expected_n = max(1, len(briefs)) if briefs is not None else None

    rounds = _repair_rounds()
    for repair in range(rounds + 1):
        raw = _ask_for_json(llm, prompt, slug)
        data = {
            "schema_version": "3.1",
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "model": current_model(),
            "source": article["source"],
            "summary": raw.get("summary", [])[:7],
            "posts": raw.get("posts", [])[:3],
        }
        # 簡體 → 台灣正體。**程式做，不叫模型做**：轉換是確定性的字串對應，
        # 為它多跑一輪 LLM 又慢又不保證改乾淨。（evidence 不動——那是原文。）
        # 放在 validate 之前：轉換可能改變字數，要驗就驗轉換後的結果。
        n = locale.localize(data)
        if n:
            print(f"    簡體 → 台灣正體：改了 {n} 個欄位")

        try:
            validate("highlights", data)
            # 則數是明定的：介面每則一格 → 模型必須剛好給 N 則，多的少的都退回去修
            if expected_n is not None and len(data["posts"]) != expected_n:
                raise PipelineError(
                    ErrorCode.SCHEMA_INVALID,
                    f"posts: 要求固定 {expected_n} 則，你給了 {len(data['posts'])} 則"
                    f" → 產出剛好 {expected_n} 則",
                )
        except PipelineError as e:
            if repair == rounds:
                raise
            print(f"    產出不符 schema → 把錯誤餵回去請它修（第 {repair + 1} 輪）")
            prompt = _repair_prompt(build_prompt(article, brief, briefs), raw, e.message)
            continue

        # 圖卡上的中文必須是台灣的中文。**prompt 叮嚀不夠**——素材是簡體時，
        # 模型會把原文的字和用語順手帶上卡片（2026-07-14 實跑：整整三則貼文都是簡體）。
        #
        # 但這裡有兩種東西，確定性差很多：
        #   簡體字  → 機器說了算，改不掉就擋（`blocking()`）
        #   用語    → **要看語意，機器判不準**（「程序正義」vs「這個程序有 bug」）
        #             所以連同原句餵回去，讓模型自己判斷；它說不用改，就不改。
        # 轉完還有簡體字 → 那是 OpenCC 的表沒蓋到，或我漏轉了某個欄位。
        # 這是 bug，不是模型的錯，所以直接炸掉，不要靜靜地把簡體字印上圖卡。
        left = locale.blocking(locale.scan(data))
        if left:
            raise PipelineError(
                ErrorCode.NOT_TAIWANESE,
                f"轉換後仍有 {len(left)} 處簡體字：\n" + locale.describe(left[:8]),
                hint="locale.localize() 漏了某個欄位，或 OpenCC 的表沒蓋到這個字",
            )

        # 用語（質量／程序／用戶…）**不處理**：少數情況，而且要看語意才判得準
        # （「程序正義」vs「這個程序有 bug」）。標在審稿表上給人看就好——
        # 機器不替語意做決定。見 scripts/analyze_all.py。

        check(data, article, strict=_strict())  # 預設只對照、不攔截
        return data

    raise AssertionError("unreachable")  # pragma: no cover


def extract(
    slug: str,
    force: bool = False,
    llm: LLMFn | None = None,
    brief: str | None = None,
    briefs: list[str] | None = None,
) -> Path:
    path = highlights_path(slug)

    # **LLM 永遠不覆蓋人的編輯**（[[發布前預覽介面]] 的紅線，在資料層執行）。
    # 人在編輯台改過的 highlights 標著 `human_edited`——那份檔案已經是「人的最終版」，
    # 重跑分析不准動它，**連 --force 也不准**：force 的意思是「產物過期了重做」，
    # 不是「把人改的字丟掉」。真要整份重來，人自己刪檔——刪除是只有人做得出的明確動作。
    if path.exists():
        try:
            edited = json.loads(path.read_text(encoding="utf-8")).get("human_edited", False)
        except (OSError, json.JSONDecodeError):
            edited = False
        if edited:
            print(f"  ⚠ {path.name} 有人的編輯，分析階段不覆蓋（要整份重來請先刪掉它）")
            return path

    # **跳過的條件是「產物比所有輸入都新」，不是「檔案存在」。**
    # 輸入 = article.json + prompts/ + 設定檔 + 這個模組本身
    # （prompt 改了、參數改了、抽取邏輯改了——知識卡都算過期）。
    inputs = (article_path(slug), PROMPT_DIR, settings.path(), Path(__file__).parent)
    if not force and not is_stale(path, *inputs):
        return path

    article = read_json("article", article_path(slug))
    return write_json("highlights", path, analyze(article, llm, brief=brief, briefs=briefs))


def review_slug(slug: str) -> tuple[list[Finding], dict[str, Any]]:
    """讀已產出的 highlights，跟原文對照一遍，回傳結果供人審。"""
    article = read_json("article", article_path(slug))
    highlights = read_json("highlights", highlights_path(slug))
    return review(highlights, article), highlights


__all__ = ["extract", "analyze", "build_prompt", "parse_response", "review_slug", "iter_claims"]

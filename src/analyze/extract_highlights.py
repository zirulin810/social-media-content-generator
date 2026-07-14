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

from ..errors import ErrorCode, PipelineError
from ..llm import LLMFn, current_model, get_llm
from ..paths import PROMPT_DIR, article_path, highlights_path
from ..schema import read_json, validate, write_json
from . import locale
from .grounding import Finding, check, iter_claims, review

# 超過這個長度才需要分段。20k 字的逐字稿一次塞得下，別為了不存在的問題蓋一座 map-reduce。
CHUNK_THRESHOLD = 60_000

# 預設不擋。人要機器擋的話設 STRICT_GROUNDING=1
STRICT = os.environ.get("STRICT_GROUNDING", "") not in ("", "0", "false")

# 模型吐出爛 JSON 是隨機的（漏跳脫一個引號就整份爛掉）。重試通常就好了。
MAX_JSON_RETRIES = 2

# schema 不合（某個欄位超字數、漏了 evidence）不必整批重想——
# 把錯誤原封不動餵回去，叫它改那幾個地方就好。這比重跑便宜也比重跑準。
MAX_REPAIR_ROUNDS = 2


def build_prompt(article: dict[str, Any]) -> str:
    """把 prompt 範本與文章組起來。段落帶著 [index]——那是 evidence 的定錨點。"""
    template = (PROMPT_DIR / "highlights.md").read_text(encoding="utf-8")
    body = "\n\n".join(f"[{p['index']}] {p['text']}" for p in article["paragraphs"])
    return (
        template.replace("{title}", article["source"]["title"])
        .replace("{author}", article["source"]["author"])
        .replace("{language}", article["language"])
        .replace("{paragraphs}", body)
    )


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
    for attempt in range(MAX_JSON_RETRIES + 1):
        response = llm(prompt)
        _dump_raw(slug, response)  # 出事時看得到模型到底吐了什麼
        try:
            return parse_response(response)
        except PipelineError as e:
            if attempt == MAX_JSON_RETRIES:
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


def analyze(article: dict[str, Any], llm: LLMFn | None = None) -> dict[str, Any]:
    """跑分析，回傳 highlights 內容（不落地）。"""
    llm = llm or get_llm()

    total = sum(len(p["text"]) for p in article["paragraphs"])
    if total > CHUNK_THRESHOLD:
        raise PipelineError(
            ErrorCode.ARTICLE_TOO_SHORT,  # 借用；長度問題
            f"文章 {total} 字，超過單次上限 {CHUNK_THRESHOLD}",
            hint="目前沒有分段實作——實測最長素材 20,749 字，一次塞得下。真的遇到再說",
        )

    prompt = build_prompt(article)
    slug = article["source"]["slug"]

    for repair in range(MAX_REPAIR_ROUNDS + 1):
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
        except PipelineError as e:
            if repair == MAX_REPAIR_ROUNDS:
                raise
            print(f"    產出不符 schema → 把錯誤餵回去請它修（第 {repair + 1} 輪）")
            prompt = _repair_prompt(build_prompt(article), raw, e.message)
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

        check(data, article, strict=STRICT)  # 預設只對照、不攔截
        return data

    raise AssertionError("unreachable")  # pragma: no cover


def extract(slug: str, force: bool = False, llm: LLMFn | None = None) -> Path:
    path = highlights_path(slug)
    if path.exists() and not force:
        return path
    article = read_json("article", article_path(slug))
    return write_json("highlights", path, analyze(article, llm))


def review_slug(slug: str) -> tuple[list[Finding], dict[str, Any]]:
    """讀已產出的 highlights，跟原文對照一遍，回傳結果供人審。"""
    article = read_json("article", article_path(slug))
    highlights = read_json("highlights", highlights_path(slug))
    return review(highlights, article), highlights


__all__ = ["extract", "analyze", "build_prompt", "parse_response", "review_slug", "iter_claims"]

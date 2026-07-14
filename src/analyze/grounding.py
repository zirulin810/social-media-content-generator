"""原文對照 v3：逐主張列出依據，供人審。

**2026-07-13 改：機器不再攔截，判斷交給人。**

原本 grounding 失敗會擋下整條 pipeline。現在不擋了——它只做機械的部分
（「這句原文我在第 N 段裡對得到嗎？」），把結果標成 ✓／✗ 給人看，過不過由人決定。

為什麼還留著 evidence：**因為驗證交給人，人才更需要它。**
沒有 evidence，要查一句話忠不忠於原文，得回頭重讀兩萬字逐字稿；
有 evidence，只要看「主張 ↔ 原文」並排的一張表。機器不判斷，但機器可以把表排好。

要恢復「不過就擋下來」的行為：`check(..., strict=True)`。

---

原本的設計說明（仍然成立）：

v2 只驗一種東西：一句金句對回原文的一個句子。
v3 要處理「可運用的知識」——步驟、對照、做法。這些**通常是跨段落綜合出來的**：
原文不會有一句話說「先建 me.md，再建 vault map」。

所以防線的抓手改了：

    不是「整張卡指一個出處」，而是「**每一條主張各自指出處**」。

一張步驟卡有三步 → 三步各自要有 evidence。
一張對照卡有錯法與正確 → 兩邊各自要有 evidence（錯法那邊最容易被腦補）。

程式能驗的：`source_text` 是否逐字出現在它宣稱的那一段。
程式驗不了的：你的中文重述有沒有超出那句原文的意思。**那只有人能看。**
所以 `iter_claims()` 把「主張 ↔ 依據」成對吐出來，讓人審的成本降到最低。
"""

from __future__ import annotations

import re
from typing import Any, Iterator, NamedTuple

from ..errors import ErrorCode, PipelineError


class Claim(NamedTuple):
    """一條主張，與它宣稱的原文依據。"""

    where: str  # 「第 1 則 / 第 2 張卡（steps）/ 第 3 步」——出錯時人要找得到它
    text: str  # 會印在圖卡上的中文
    evidence: list[dict[str, Any]]


# 引號是 JSON 的地雷。逐字稿裡本來就有雙引號（`then await instruction."`），
# 模型照抄進 source_text 時常常忘了跳脫，整份 JSON 就爛掉。
# 解法：允許模型省略或替換引號，比對時兩邊都把引號抹掉。
# 這不會放寬「內容必須真實」——只是不再讓一個標點符號決定成敗。
QUOTES = re.compile(r"[\"\'\u2018\u2019\u201c\u201d\u300c\u300d\u300e\u300f\u2032\u2033`]")


def _normalize(s: str) -> str:
    """比對用：吃掉空白與引號的差異，但不動文字本身。"""
    return QUOTES.sub("", re.sub(r"\s+", "", s))


def iter_claims(highlights: dict[str, Any]) -> Iterator[Claim]:
    """走訪每一則貼文的每一張卡的每一條主張。

    新增卡型時**一定要在這裡加一條**，否則那張卡就繞過了防線——
    這是唯一一個「忘記改就會靜默失守」的地方，所以下面的 else 直接拋錯。
    """
    for pi, post in enumerate(highlights["posts"], 1):
        for ci, card in enumerate(post["cards"], 1):
            t = card["type"]
            at = f"第 {pi} 則／第 {ci} 張卡（{t}）"

            if t in ("quote", "point"):
                text = card["text"] if t == "quote" else f"{card['title']}｜{card['body']}"
                yield Claim(at, text, card["evidence"])

            elif t == "steps":
                for si, s in enumerate(card["steps"], 1):
                    yield Claim(f"{at}／第 {si} 步", s["text"], s["evidence"])

            elif t == "contrast":
                yield Claim(f"{at}／錯法", card["wrong"]["text"], card["wrong"]["evidence"])
                yield Claim(f"{at}／正確", card["right"]["text"], card["right"]["evidence"])

            else:  # pragma: no cover
                raise PipelineError(
                    ErrorCode.SCHEMA_INVALID,
                    f"未知的卡型：{t}",
                    hint="新增卡型時必須同步更新 grounding.iter_claims()，否則它會繞過幻覺防線",
                )


class Finding(NamedTuple):
    """一條主張的對照結果。

    severity 是重點：**「段落標錯」跟「憑空編造」是完全不同的嚴重程度**，
    不該用同一個 ✗ 打發。前者無害（句子是真的，只是索引偏了），
    後者是這條 pipeline 最危險的失效模式。
    """

    claim: Claim
    ok: bool
    problem: str  # ok 時為空字串
    severity: str = "ok"  # ok / misindexed（段落標錯）/ fabricated（原文中完全找不到）


def review(highlights: dict[str, Any], article: dict[str, Any]) -> list[Finding]:
    """逐條對照，回報結果。**不拋錯、不擋下任何東西。**

    機器只回答一個機械的問題：「這句 source_text，我在第 N 段裡找得到嗎？」
    它不回答「這個中文重述有沒有超出原文的意思」——那是人的工作。
    """
    paragraphs = {p["index"]: _normalize(p["text"]) for p in article["paragraphs"]}
    body = _normalize(article["body"])
    n = len(paragraphs)
    out: list[Finding] = []

    for claim in iter_claims(highlights):
        if not claim.evidence:
            out.append(Finding(claim, False, "沒有附任何原文依據", "fabricated"))
            continue

        problems: list[str] = []
        severity = "ok"

        for ev in claim.evidence:
            idx = ev["para_index"]
            src = _normalize(ev["source_text"])
            para = paragraphs.get(idx)

            if para is not None and src in para:
                continue  # 對得上

            # 對不上，但這句話在別的段落找得到嗎？
            # 找得到 → 只是索引標錯，句子是真的，無害。
            # 找不到 → 原文裡根本沒這句話。這才是要警戒的。
            if src in body:
                actual = next((i for i, t in paragraphs.items() if src in t), None)
                problems.append(f"段落標錯：說在第 {idx} 段，其實在第 {actual} 段")
                severity = "misindexed" if severity == "ok" else severity
            elif para is None:
                problems.append(f"指向不存在的段落 {idx}（全文只有 {n} 段）")
                severity = "fabricated"
            else:
                problems.append(f"原文中完全找不到這句話（第 {idx} 段沒有，全文也沒有）")
                severity = "fabricated"

        out.append(Finding(claim, not problems, "；".join(problems), severity))

    return out


def check(highlights: dict[str, Any], article: dict[str, Any], strict: bool = False) -> list[Finding]:
    """回報對照結果。strict=True 時，**只有憑空編造才擋**（段落標錯是無害的）。"""
    findings = review(highlights, article)
    bad = [f for f in findings if f.severity == "fabricated"]
    if strict and bad:
        lines = [f"  {f.claim.where}：{f.problem}\n    主張：{f.claim.text}" for f in bad[:5]]
        raise PipelineError(
            ErrorCode.QUOTE_NOT_GROUNDED,
            f"{len(bad)} 條主張對不回原文：\n" + "\n".join(lines),
            hint="重跑；反覆發生就修 prompts/highlights.md。或關掉 strict 讓人自己判斷",
        )
    return findings


__all__ = ["check", "review", "iter_claims", "Claim", "Finding"]

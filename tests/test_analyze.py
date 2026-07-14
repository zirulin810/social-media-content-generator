"""分析階段（v3：知識卡）的測試。

契約變了：產出的不是金句陣列，是 1–3 則貼文，每則由知識卡組成。
**每一條主張各自帶 evidence**——步驟卡的每一步、對照卡的兩邊，都要指得出原文。

機器不再攔截（判斷交給人），所以測的是：對照表算不算得對。
"""

from __future__ import annotations

import copy
import json

import pytest

from src.analyze.extract_highlights import analyze, parse_response
from src.analyze.grounding import check, iter_claims, review
from src.errors import ErrorCode, PipelineError
from src.schema import validate

ARTICLE = {
    "schema_version": "2.0",
    "generated_at": "2026-07-13T01:00:00+08:00",
    "source": {"slug": "t", "title": "測試", "author": "某作者", "url": "https://youtu.be/x"},
    "origin": "video_transcript",
    "language": "en",
    "paragraphs": [
        {"index": 0, "text": "Obsidian is just a folder of notes on your computer."},
        {"index": 1, "text": "Give the AI a map and it can figure out which files are relevant."},
        {"index": 2, "text": "Keep your skills in your own notes, not inside the AI tool."},
    ],
    "body": (
        "Obsidian is just a folder of notes on your computer.\n\n"
        "Give the AI a map and it can figure out which files are relevant.\n\n"
        "Keep your skills in your own notes, not inside the AI tool."
    ),
}

GOOD = {
    "summary": ["筆記是純文字檔", "AI 需要地圖", "技能存自己的筆記"],
    "posts": [
        {
            "angle": "讓 AI 讀懂你的筆記庫",
            "hook": "不給地圖，它只能瞎猜",
            "cards": [
                {
                    "type": "point",
                    "title": "給 AI 一張地圖",
                    "body": "在 vault 根目錄放一份地圖，寫清楚每個資料夾放什麼，AI 就不必掃全庫",
                    "evidence": [
                        {"para_index": 1, "source_text": "Give the AI a map and it can figure out which files are relevant."}
                    ],
                },
                {
                    "type": "steps",
                    "title": "兩步就位",
                    "steps": [
                        {
                            "text": "認清 vault 就是一個資料夾",
                            "evidence": [{"para_index": 0, "source_text": "Obsidian is just a folder of notes"}],
                        },
                        {
                            "text": "把技能寫進自己的筆記",
                            "evidence": [{"para_index": 2, "source_text": "Keep your skills in your own notes"}],
                        },
                    ],
                },
                {
                    "type": "contrast",
                    "title": "技能存哪裡",
                    "wrong": {
                        "text": "存在 AI 工具裡",
                        "evidence": [{"para_index": 2, "source_text": "not inside the AI tool"}],
                    },
                    "right": {
                        "text": "存在自己的筆記裡",
                        "evidence": [{"para_index": 2, "source_text": "Keep your skills in your own notes"}],
                    },
                },
                {
                    "type": "quote",
                    "text": "技能存自己的筆記，不要存在 AI 工具裡",
                    "verbatim": False,
                    "evidence": [
                        {"para_index": 2, "source_text": "Keep your skills in your own notes, not inside the AI tool."}
                    ],
                },
            ],
            "topics": ["Obsidian", "AI"],
            "hashtags": ["#Obsidian", "#AI", "#第二大腦"],
        }
    ],
}


def _llm(payload: dict):
    return lambda _p: json.dumps(payload, ensure_ascii=False)


# --- 契約 ---

def test_analyze_produces_valid_v3_highlights() -> None:
    data = analyze(ARTICLE, llm=_llm(GOOD))
    validate("highlights", data)
    assert data["schema_version"] == "3.1"
    assert len(data["posts"]) == 1
    assert {c["type"] for c in data["posts"][0]["cards"]} == {"point", "steps", "contrast", "quote"}


def test_evidence_keeps_original_language() -> None:
    """卡片是繁中，evidence 保留英文原文——人才能並排比對。"""
    data = analyze(ARTICLE, llm=_llm(GOOD))
    card = data["posts"][0]["cards"][0]
    assert "地圖" in card["body"]
    assert "Give the AI a map" in card["evidence"][0]["source_text"]


# --- 逐主張走訪：新增卡型忘了改這裡，就會靜默失守 ---

def test_iter_claims_visits_every_claim() -> None:
    """point 1 條 + steps 2 步 + contrast 2 邊 + quote 1 條 = 6 條主張。"""
    claims = list(iter_claims(GOOD))
    assert len(claims) == 6
    wheres = " ".join(c.where for c in claims)
    assert "第 1 步" in wheres and "第 2 步" in wheres
    assert "錯法" in wheres and "正確" in wheres


def test_unknown_card_type_raises_rather_than_slipping_through() -> None:
    """新增卡型時忘了更新 iter_claims → 那張卡會繞過所有對照。寧可炸掉。"""
    bad = copy.deepcopy(GOOD)
    bad["posts"][0]["cards"][0] = {"type": "mystery", "text": "x"}
    with pytest.raises(PipelineError) as e:
        list(iter_claims(bad))
    assert "繞過" in e.value.hint


# --- 對照表 ---

def test_review_marks_everything_ok_when_grounded() -> None:
    findings = review(GOOD, ARTICLE)
    assert len(findings) == 6
    assert all(f.ok for f in findings)


def test_fabrication_is_marked_as_fabricated() -> None:
    """原文裡根本沒這句話——這是最危險的失效模式。"""
    bad = copy.deepcopy(GOOD)
    bad["posts"][0]["cards"][0]["evidence"][0]["source_text"] = "The author never said this."
    failed = [f for f in review(bad, ARTICLE) if not f.ok]
    assert len(failed) == 1
    assert failed[0].severity == "fabricated"
    assert "完全找不到" in failed[0].problem


def test_misindexed_evidence_is_not_treated_as_fabrication() -> None:
    """句子是真的、只是段落標錯——無害，不該跟憑空編造用同一個 ✗ 打發。

    這是實跑 Gemini 才學到的：42 條主張裡 3 條對不上，**全都是索引偏一格**，
    句子本身都在原文裡。把它們標成「幻覺」會讓人失去對警報的信任。
    """
    bad = copy.deepcopy(GOOD)
    bad["posts"][0]["cards"][1]["steps"][0]["evidence"][0]["para_index"] = 2  # 其實在第 0 段
    failed = [f for f in review(bad, ARTICLE) if not f.ok]
    assert len(failed) == 1
    assert failed[0].severity == "misindexed"
    assert "其實在第 0 段" in failed[0].problem
    assert "第 1 步" in failed[0].claim.where


def test_strict_mode_blocks_fabrication_but_tolerates_misindex() -> None:
    """要擋就擋真正危險的。段落標錯不該擋下整條 pipeline。"""
    misindexed = copy.deepcopy(GOOD)
    misindexed["posts"][0]["cards"][1]["steps"][0]["evidence"][0]["para_index"] = 2
    check(misindexed, ARTICLE, strict=True)  # 不該拋錯

    fabricated = copy.deepcopy(GOOD)
    fabricated["posts"][0]["cards"][0]["evidence"][0]["source_text"] = "Never said this at all."
    with pytest.raises(PipelineError) as e:
        check(fabricated, ARTICLE, strict=True)
    assert e.value.code == ErrorCode.QUOTE_NOT_GROUNDED


def test_review_flags_nonexistent_paragraph() -> None:
    bad = copy.deepcopy(GOOD)
    ev = bad["posts"][0]["cards"][2]["wrong"]["evidence"][0]
    ev["para_index"] = 99
    ev["source_text"] = "a sentence that appears nowhere in the article"
    failed = [f for f in review(bad, ARTICLE) if not f.ok]
    assert failed[0].severity == "fabricated"
    assert "不存在的段落" in failed[0].problem


# --- 機器不攔截（判斷交給人） ---

def test_grounding_does_not_block_by_default() -> None:
    """對不上也照樣產出——判斷是人的事。這是 2026-07-13 的決定。"""
    bad = copy.deepcopy(GOOD)
    bad["posts"][0]["cards"][0]["evidence"][0]["source_text"] = "Never said this."
    data = analyze(ARTICLE, llm=_llm(bad))  # 不該拋錯
    validate("highlights", data)





# --- schema 邊界 ---

def test_step_without_evidence_is_rejected_by_schema() -> None:
    """步驟是跨段綜合出來的，最容易被腦補——每一步都必須指得出原文。"""
    bad = copy.deepcopy(GOOD)
    del bad["posts"][0]["cards"][1]["steps"][0]["evidence"]
    data = {
        "schema_version": "3.1",
        "generated_at": "2026-07-13T01:00:00+08:00",
        "source": ARTICLE["source"],
        **bad,
    }
    with pytest.raises(PipelineError):
        validate("highlights", data)


def test_more_than_three_posts_is_rejected() -> None:
    bad = copy.deepcopy(GOOD)
    bad["posts"] = bad["posts"] * 4
    data = {
        "schema_version": "3.1",
        "generated_at": "2026-07-13T01:00:00+08:00",
        "source": ARTICLE["source"],
        **bad,
    }
    with pytest.raises(PipelineError):
        validate("highlights", data)


def test_parse_response_tolerates_code_fences() -> None:
    assert parse_response('```json\n{"posts": []}\n```') == {"posts": []}


# --- 2026-07-13 第一次真跑 v3，四篇全掛。這兩條測試就是那兩個 bug ---

QUOTED_ARTICLE = {
    **ARTICLE,
    "paragraphs": [
        {"index": 0, "text": 'He says: Confirm you have read, then await instruction." That is it.'},
        {"index": 1, "text": "Give the AI a map and it can figure out which files are relevant."},
        {"index": 2, "text": "Keep your skills in your own notes, not inside the AI tool."},
    ],
    "body": (
        'He says: Confirm you have read, then await instruction." That is it.\n\n'
        "Give the AI a map and it can figure out which files are relevant.\n\n"
        "Keep your skills in your own notes, not inside the AI tool."
    ),
}


def test_quotes_in_transcript_do_not_break_grounding() -> None:
    """逐字稿裡本來就有雙引號。模型照抄會炸掉 JSON，所以我們允許它省略引號——
    比對時兩邊都把引號抹掉，內容還是要真。這是四篇全掛的第一個原因。"""
    from src.analyze.grounding import review

    h = copy.deepcopy(GOOD)
    h["posts"][0]["cards"][0]["evidence"] = [
        # 模型省略了原文結尾的 " ——這應該要能對得上
        {"para_index": 0, "source_text": "Confirm you have read, then await instruction."}
    ]
    findings = review(h, QUOTED_ARTICLE)
    assert findings[0].ok, findings[0].problem


def test_quote_normalization_does_not_let_fabrication_through() -> None:
    """放寬引號不等於放寬內容——編造的句子還是要被標出來。"""
    from src.analyze.grounding import review

    h = copy.deepcopy(GOOD)
    h["posts"][0]["cards"][0]["evidence"] = [
        {"para_index": 0, "source_text": "He says you should buy my course."}
    ]
    assert not review(h, QUOTED_ARTICLE)[0].ok


def test_broken_json_is_retried() -> None:
    """漏跳脫一個引號整份 JSON 就爛掉，而且是隨機的。重試通常就好。"""
    calls = {"n": 0}

    def flaky(_prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"posts": [{"angle": "壞掉的 "引號" 在這裡"}]}'
        return json.dumps(GOOD, ensure_ascii=False)

    data = analyze(ARTICLE, llm=flaky)
    assert calls["n"] == 2
    assert len(data["posts"]) == 1


def _quote_claim(source_text: str, para_index: int = 0) -> dict:
    """一則只有一張金句卡的 highlights，拿來單獨測 grounding 的比對。"""
    return {
        "posts": [
            {
                "angle": "a",
                "cards": [
                    {
                        "type": "quote",
                        "text": "中文重述",
                        "verbatim": False,
                        "evidence": [{"para_index": para_index, "source_text": source_text}],
                    }
                ],
            }
        ]
    }


def test_capitalising_the_first_letter_is_not_a_hallucination() -> None:
    """從句中開始引用，首字母大寫——**那是正確的引用方式，不是編造。**

    2026-07-14 實跑抓到：
        原文「The first gotcha, don't import everything from your old notes app.」
        模型「Don't import everything from your old notes app.」
    一字不差，只有 d→D。舊的比對是大小寫敏感的，於是判成 `fabricated`＝最高警戒。
    **防線要抓的是編造，不是大寫。**
    """
    f = review(_quote_claim("OBSIDIAN is just A FOLDER of notes on your computer."), ARTICLE)[0]
    assert f.ok, f.problem
    assert f.severity == "ok"


def test_quote_spanning_two_paragraphs_is_harmless_and_says_where() -> None:
    """引文橫跨兩段（原文的分段不見得切在句號上）→ 無害，但要講得出跨了哪幾段。

    舊版找得到句子卻答不出段號，印出「其實在第 **None** 段」——
    使用者看到 None，只能猜那是什麼意思。**看不懂的訊息等於沒有訊息。**
    """
    spanning = "on your computer. Give the AI a map"  # 橫跨第 0、1 段
    f = review(_quote_claim(spanning, para_index=0), ARTICLE)[0]
    assert not f.ok
    assert f.severity == "misindexed"       # 句子是真的，只是跨了段界
    assert "None" not in f.problem
    assert "0–1" in f.problem


def test_a_genuinely_invented_quote_is_still_caught() -> None:
    """放寬大小寫之後，真的編造仍然要被抓出來——否則防線就白拆了。"""
    f = review(_quote_claim("Obsidian automatically writes your notes for you."), ARTICLE)[0]
    assert not f.ok
    assert f.severity == "fabricated"


def _too_long_step() -> str:
    """一個「保證超過 schema 上限」的步驟字串。

    **上限去問 schema，不要抄進測試。** 原本這裡寫死 `"字" * 61`（因為當時上限是 60）——
    2026-07-14 上限改成 100 之後，61 字變成合法值，這兩條測試就靜靜地測不到東西了：
    一條沒觸發修復迴圈，一條沒觸發該有的錯誤。**抄一份上限，就多一個會跟契約走散的地方。**
    """
    import json as _json

    from src.paths import SCHEMA_DIR

    s = _json.loads((SCHEMA_DIR / "highlights.schema.json").read_text(encoding="utf-8"))
    cap = s["$defs"]["stepsCard"]["properties"]["steps"]["items"]["properties"]["text"]["maxLength"]
    return "字" * (cap + 1)


def test_schema_failure_is_repaired_not_restarted() -> None:
    """欄位超字數不必整批重想——把錯誤餵回去，叫它改那幾個地方就好。
    它已經讀完文章了，重跑一次全文分析是浪費。"""
    calls = {"n": 0, "prompts": []}

    def flaky(prompt: str) -> str:
        calls["n"] += 1
        calls["prompts"].append(prompt)
        if calls["n"] == 1:
            bad = copy.deepcopy(GOOD)
            bad["posts"][0]["cards"][1]["steps"][0]["text"] = _too_long_step()
            return json.dumps(bad, ensure_ascii=False)
        return json.dumps(GOOD, ensure_ascii=False)

    data = analyze(ARTICLE, llm=flaky)
    assert calls["n"] == 2
    # 第二次的 prompt 必須帶著錯誤訊息與上一次的輸出
    assert "不符規格" in calls["prompts"][1]
    assert "steps/0/text" in calls["prompts"][1]
    assert len(data["posts"][0]["cards"]) == 4


def test_repair_gives_up_eventually() -> None:
    """模型改不好就得認輸，不能無限迴圈。"""
    def always_bad(_prompt: str) -> str:
        bad = copy.deepcopy(GOOD)
        bad["posts"][0]["cards"][1]["steps"][0]["text"] = _too_long_step()
        return json.dumps(bad, ensure_ascii=False)

    with pytest.raises(PipelineError) as e:
        analyze(ARTICLE, llm=always_bad)
    assert e.value.code == ErrorCode.SCHEMA_INVALID

"""文案階段：機械的事程式做，判斷的事模型做。

這裡驗的全是「程式該保證的事」——出處、字數、無 emoji、台灣正體、hook 的形式。
**「這段話好不好」不在這裡驗，那是人的工作。**
"""

from __future__ import annotations

import json

import pytest

from src.compose.write_post import (
    HOOK_MAX_CHARS,
    IG_MAX_CHARS,
    THREADS_MAX_CHARS,
    assemble,
    attribution,
    build_prompt,
    check_hook,
    clean,
    draft,
    fit_by_sentence,
    restates_angle,
    wasted_opening,
    write_one,
)
from src.errors import PipelineError
from src.paths import PROMPT_DIR

SOURCE = {
    "slug": "t",
    "title": "How I Use Obsidian",
    "author": "Nick Milo",
    "url": "https://youtu.be/x",
}
ARTICLE = {"source": SOURCE, "origin": "video_transcript"}
POST = {
    "angle": "讓 AI 讀懂你的筆記庫",
    "hook": "先給它一張地圖",
    "cards": [
        {"type": "point", "title": "先給地圖", "body": "AI 不是不夠聰明，是不知道你的脈絡"},
        {"type": "steps", "title": "怎麼做", "steps": [{"text": "建立 me.md"}, {"text": "建立地圖"}]},
        {
            "type": "contrast",
            "title": "技能放哪",
            "wrong": {"text": "放工具裡"},
            "right": {"text": "放筆記裡"},
        },
    ],
}
IMAGES = [{"path": "images/01_cover.png"}, {"path": "images/02_point_1.png"}]

GOOD_HOOK = "AI 每次都要你重講一遍你是誰嗎？"  # 短、痛點、不是 angle 的複述


def fake_llm(body: str, tags: list[str] | None = None, hook: str = GOOD_HOOK):
    payload: dict = {"hook": hook, "body": body}
    if tags is not None:
        payload["hashtags"] = tags
    return lambda _prompt: json.dumps(payload, ensure_ascii=False)


def one(platform: str, body: str, tags: list[str] | None = None, hook: str = GOOD_HOOK) -> dict:
    """跑完整條路（模型 → 檢查 → 裝進平台的殼）。"""
    h, b, t = draft(POST, ARTICLE, fake_llm(body, tags, hook))
    return write_one(platform, h, b, t, ARTICLE, IMAGES)


# --- 兩個平台共用一份文案 ---------------------------------------------------


def test_both_platforms_share_one_draft() -> None:
    """**IG 與 Threads 共用同一份文案**（Human：「IG 太冗長了，可以跟 Threads 共用」）。

    原本兩個平台各一個 prompt、各叫一次 LLM——結果 IG 那版寫成五段小論文。
    圖卡才是主角，caption 是導讀。**兩邊都不需要一篇論文。**（順帶把 API 呼叫減半。）
    """
    calls = {"n": 0}

    def counting(_p: str) -> str:
        calls["n"] += 1
        return json.dumps({"hook": GOOD_HOOK, "body": "正文。", "hashtags": ["#a"]}, ensure_ascii=False)

    hook, body, tags = draft(POST, ARTICLE, counting)
    ig = write_one("instagram", hook, body, tags, ARTICLE, IMAGES)
    th = write_one("threads", hook, body, tags, ARTICLE, IMAGES)

    assert calls["n"] == 1, "兩個平台應該共用一次呼叫的結果"
    assert ig["caption"].startswith(hook) and th["caption"].startswith(hook)
    assert body in ig["caption"] and body in th["caption"]
    assert "hashtags" in ig and "hashtags" not in th  # 差別只在 hashtag


# --- hook：機器驗得了的部分 --------------------------------------------------


def test_a_hook_that_only_restates_the_angle_is_rejected() -> None:
    """**angle 是摘要，hook 是鉤子——兩者的讀者不同，任務不同。**

        angle  「這則在講什麼」——給我看的索引（也是封面標題）
        hook   「你為什麼該停下來」——給讀者看的第一句

    第一版沒把 hook 當成一個東西（它只是正文的第一行），於是模型寫出來的都是這種：
        「用『槓鈴策略』駕馭 AI，兼顧防禦與進攻，讓 AI 成為你的思考夥伴。」
    **那是摘要，不是 hook。** 沒有人會為一句摘要停下手指。
    """
    angle = "用槓鈴策略駕馭 AI"
    assert restates_angle("用「槓鈴策略」駕馭 AI，兼顧防禦與進攻，讓 AI 成為你的思考夥伴。", angle)
    assert not restates_angle("AI 寫出來的東西，正在淹掉你自己的想法。", angle)


def test_hook_checks_are_mechanical() -> None:
    assert check_hook("", POST, SOURCE)
    assert check_hook("字" * (HOOK_MAX_CHARS + 1), POST, SOURCE)
    assert check_hook(POST["angle"], POST, SOURCE)                      # 複述 angle
    assert check_hook("How I Use Obsidian 講得很好", POST, SOURCE)      # 出處吃掉開場
    assert not check_hook(GOOD_HOOK, POST, SOURCE)


def test_a_bad_hook_gets_rewritten() -> None:
    calls = {"n": 0}

    def flaky(_p: str) -> str:
        calls["n"] += 1
        hook = POST["angle"] if calls["n"] == 1 else GOOD_HOOK  # 第一次直接複述 angle
        return json.dumps({"hook": hook, "body": "正文", "hashtags": ["#a"]}, ensure_ascii=False)

    hook, _, _ = draft(POST, ARTICLE, flaky)
    assert calls["n"] == 2, "hook 只是複述 angle 時，應該請它重寫"
    assert hook == GOOD_HOOK


def test_a_hook_that_is_too_long_is_not_a_hook() -> None:
    assert HOOK_MAX_CHARS <= 40, "超過這個長度就不是鉤子，是摘要"


def test_the_hook_leads_the_caption() -> None:
    ig = one("instagram", "正文內容", ["#a"])
    assert ig["caption"].startswith(GOOD_HOOK), "hook 必須是第一句——它是唯一一定會被讀到的東西"


# --- 程式該保證的事 ---------------------------------------------------------


def test_attribution_is_added_by_the_program_not_the_model() -> None:
    """**紅線：不省出處。** 這條不能靠模型記得——它總有一天會忘。"""
    ig = one("instagram", "這支影片的整理", ["#筆記"])
    assert SOURCE["url"] in ig["caption"]
    assert SOURCE["title"] in ig["caption"] and SOURCE["author"] in ig["caption"]
    assert ig["attribution"]


def test_emoji_are_stripped_by_the_program() -> None:
    """**紅線：不用表情符號。**

    「有沒有 emoji」是機械可驗的，程式剝掉就好——
    不必花一輪 LLM 去請它「不要用 emoji」。那是把確定的事交給不確定的東西。
    """
    ig = one("instagram", "超讚的做法 🔥🚀", ["#筆記 ✨"])
    assert "🔥" not in ig["caption"] and "🚀" not in ig["caption"]
    assert all("✨" not in t for t in ig["hashtags"])


def test_captions_are_taiwanese() -> None:
    ig = one("instagram", "这个软件的用户界面不错", ["#笔记"])
    assert "這個軟體的使用者介面不錯" in ig["caption"]
    assert "#筆記" in ig["hashtags"]


def test_only_the_platform_limit_is_enforced_not_my_taste() -> None:
    """**平台的硬上限才報錯；我的偏好不報錯。**

    第一版我把「500 字」（我自己猜的編輯偏好）寫成硬上限，還在上面疊了精算
    （500 − 出處 − hashtag = 正文只剩 264 字），把模型逼進一個它做不到的框。
    實跑 12 則全滅——**沒有一則是因為內容爛，全部是因為我的數字。**

    IG 的真實上限是 2200，不是 500。600 字的 IG 文案完全合法。
    """
    ig = one("instagram", "字" * 600, ["#a"])
    assert len(ig["caption"]) <= IG_MAX_CHARS
    assert len(ig["caption"]) > 500, "600 字的 IG 文案是合法的，不該被我的偏好擋下來"


def test_threads_too_long_gets_trimmed_at_a_sentence_boundary() -> None:
    """Threads 的 500 字是**平台定的**——太長就由程式砍在句號上。**砍正文，不砍 hook。**"""
    th = one("threads", "這是一個完整的句子而且它講的是一件事。" * 40)
    assert len(th["caption"]) <= THREADS_MAX_CHARS

    parts = th["caption"].split("\n\n")
    assert parts[0] == GOOD_HOOK, "hook 必須完整保留——它是唯一保證會被讀到的東西"
    assert parts[1].endswith("。"), "切在句子中間了——寧可少一句話，也不要發半句話"


def test_one_giant_sentence_is_rejected_not_chopped_in_half() -> None:
    """砍不動就報錯。**不硬切前 N 字**——那會發出半句話，正好違反這個機制存在的理由。"""
    with pytest.raises(PipelineError) as e:
        one("threads", "字" * 600)  # 一個句號都沒有
    assert "砍不動" in e.value.message


def test_the_last_cut_is_made_by_the_program_not_the_model() -> None:
    """**LLM 不會數中文字。**

    實測：叫它「壓到 400 字以內」，它給 729 → 717 → 561——一路逼近但永遠差一點。
    它看到的是 token，不是字。我等於要求它做一件它做不到的事，然後怪它做不到。

    所以：**句子由模型寫（判斷），最後一刀由程式砍（機械）。**
    """
    body = "第一句話很重要。第二句話也不錯。第三句話可以砍。第四句話一定要砍。"
    out = fit_by_sentence(body, 20)
    assert len(out) <= 20
    assert out.endswith("。")
    assert out.startswith("第一句話很重要"), "要從最後一句開始砍，不是從開頭砍"


def test_a_short_body_is_never_touched() -> None:
    assert fit_by_sentence("只有一句話。", 500) == "只有一句話。"


def test_a_real_newline_inside_the_json_string_is_tolerated() -> None:
    """文案本來就是多段落的，模型很自然會在 JSON 字串裡直接按 Enter。

    嚴格說那不是合法的 JSON——但**這是可以修的，不是要重跑的**。
    """
    llm = lambda _p: '{"hook": "' + GOOD_HOOK + '", "body": "第一段\n第二段"}'  # noqa: E731
    hook, body, _ = draft(POST, ARTICLE, llm)
    assert hook == GOOD_HOOK
    assert "第一段" in body and "第二段" in body


def test_threads_gets_no_hashtags() -> None:
    th = one("threads", "整理", ["#不該出現"])
    assert "hashtags" not in th
    assert "#" not in th["caption"]


def test_image_paths_follow_the_render_output() -> None:
    ig = one("instagram", "正文", ["#a"])
    assert ig["image_paths"] == ["images/01_cover.png", "images/02_point_1.png"]


def test_the_program_owns_the_skeleton() -> None:
    assert assemble("正文", "原文：X｜Y", ["#a"], hook="鉤子") == "鉤子\n\n正文\n\n原文：X｜Y\n\n#a"
    assert attribution(SOURCE).startswith("原文：How I Use Obsidian｜Nick Milo")
    assert clean("这个软件 🔥") == "這個軟體"


def test_an_opening_eaten_by_the_source_is_detected() -> None:
    """**IG 折疊前只看得到前 125 字**——拿它來寫書名和作者，等於什麼都沒說。"""
    assert wasted_opening("我最近看了 How I Use Obsidian 這支影片，收穫很多。", SOURCE)
    assert wasted_opening("Nick Milo 說，AI 讀不懂你的筆記庫。", SOURCE)
    assert not wasted_opening("AI 讀不懂你的筆記庫，是因為你沒給它地圖。", SOURCE)


# --- prompt 該告訴模型的事 ---------------------------------------------------


def test_prompt_carries_the_cards_so_the_model_cannot_invent() -> None:
    """**文案不得超出圖卡講過的東西。** 卡片內容要餵進去，模型才有東西可寫。"""
    p = build_prompt(POST, ARTICLE)
    assert "AI 不是不夠聰明" in p and "建立 me.md" in p and "放工具裡" in p
    assert POST["angle"] in p


def test_prompt_says_video_not_article_for_transcripts() -> None:
    """語氣要對：影片轉出的文章要說「這支影片」，不是「這篇文章」。"""
    assert "這支影片" in build_prompt(POST, ARTICLE)
    assert "這篇文章" in build_prompt(POST, {"source": SOURCE, "origin": "article"})


def test_the_prompt_forbids_putting_a_hat_on_the_reader() -> None:
    """**今天最重要的一條紅線，是 Human 指出來的。**

        ✗「你的筆記散落在各處，從來沒有真正屬於你。」
        ✗「你的筆記之所以難用，是因為你沒有真正擁有它。」

        「這個才是真正的雷點，因為他們並不一定屬實，而且有點像是在給人戴帽子。」

    這兩句的問題**不是「用了斷言句」**，是**替讀者的人生下判斷**——
    它假裝了解讀者的缺陷，然後叫他來看解方，而讀者沒有否認的餘地。

    對比：「你是不是也把舊筆記全部匯進來了？」是**問句，他可以說沒有**，
    而且原文真的講過那是常見錯誤。

    所以紅線是「**不准替讀者斷言他的狀態**」，不是「不准用問句」。
    """
    text = (PROMPT_DIR / "caption.md").read_text(encoding="utf-8")
    assert "不准替讀者斷言" in text
    assert "戴帽子" in text
    assert "從來沒有真正屬於你" in text, "把真實的反例寫進去——抽象的規則模型記不住"


def test_the_prompt_offers_several_hook_moves() -> None:
    """hook 放行，但要**給它幾種手法**，不然它只會複述 angle。"""
    text = (PROMPT_DIR / "caption.md").read_text(encoding="utf-8")
    for move in ("痛點", "反直覺", "具體結果", "共鳴提問", "破除誤解"):
        assert move in text, f"prompt 沒給「{move}」這種手法"


def test_prompts_forbid_inventing_anyones_past() -> None:
    """**模型不知道任何人以前怎麼做筆記，不准替他們編。**

    我在 prompt 裡放了一句示範：「我一直以為筆記要分類分得很細，看完才發現方向反了」。
    模型把它當模板，套進**四則不同主題**的貼文——包括跟「分類」毫無關係的那幾則。
    **我示範了一句聽起來很像人話的假話，它就學會了。**
    """
    text = (PROMPT_DIR / "caption.md").read_text(encoding="utf-8")
    assert "不捏造任何人的經驗" in text
    for line in text.splitlines():
        if "我一直以為筆記要分類" in line:
            assert line.lstrip().startswith(("- ✗", "✗")), (
                f"還把這句話當正面示範——模型會照抄：{line.strip()}"
            )


def test_prompts_live_in_files_not_in_code() -> None:
    text = (PROMPT_DIR / "caption.md").read_text(encoding="utf-8")
    assert "標題黨" in text and "表情符號" in text
    assert "hook" in text and "作者名" in text

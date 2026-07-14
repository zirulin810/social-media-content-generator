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

# 合格的假正文：**2 段**（`check_body` 會擋掉黏成一坨的東西）
GOOD_BODY = "先給 AI 一張地圖，它才知道去哪裡找。\n\n三個檔案就夠：me.md、vault map、skill map。"


def fake_llm(body: str = GOOD_BODY, tags: list[str] | None = None, hook: str = GOOD_HOOK):
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
        return json.dumps(
            {"hook": GOOD_HOOK, "body": GOOD_BODY, "hashtags": ["#a"]}, ensure_ascii=False
        )

    hook, body, tags = draft(POST, ARTICLE, counting)
    ig = write_one("instagram", hook, body, tags, ARTICLE, IMAGES)
    th = write_one("threads", hook, body, tags, ARTICLE, IMAGES)

    assert calls["n"] == 1, "兩個平台應該共用一次呼叫的結果"
    assert ig["caption"].startswith(hook) and th["caption"].startswith(hook)
    assert body in ig["caption"] and body in th["caption"]
    assert "hashtags" in ig and "hashtags" not in th  # 差別只在 hashtag


# --- hook：機器驗得了的部分 --------------------------------------------------


def test_a_good_hook_is_not_killed_just_for_sharing_keywords() -> None:
    """**判斷器太嚴，模型就只能亂猜——而它猜不到我心裡想的那條線。**

    第一版用「字元集合重疊率 ≥ 0.7」判斷「hook 是不是複述 angle」。
    但 hook 跟 angle 講的**本來就是同一件事**，共用關鍵字是必然的。
    於是它連殺兩個好 hook，模型重寫三次都過不了關：

        angle「讓 AI 讀懂你的筆記庫」
        hook 「AI 每次都要你重講一遍你是誰？」  ← 痛點，不是摘要，卻被判複述

    改成只抓「幾乎一模一樣」（angle 整句被塞進 hook，或相似度 ≥ 0.75）。
    """
    assert not restates_angle(
        "AI 每次都要你重講一遍你是誰？試試這樣讓它讀懂你的筆記。", "讓 AI 讀懂你的筆記庫"
    )
    assert not restates_angle("AI 內容像洪水？用「槓鈴策略」讓它成為你的超能力。", "用槓鈴策略駕馭 AI")
    assert not restates_angle("你是不是也把舊筆記全部匯進 Obsidian 了？", "避免 Obsidian 新手常見錯誤")


def test_the_limits_are_a_ceiling_not_a_target() -> None:
    """**兩個數字，性質不同。** 這是今天學了五次的教訓。

    第一版：hook 硬上限 30 字、段落硬上限 110 字。
    實跑時模型寫 31、33、35、111、115、118——**全部差一點點**，
    重寫三次都跨不過我那條隨手畫的線，然後我照樣出貨。
    **三次 LLM 呼叫，換來零改善。**

    因為我又在叫它「數中文字」——那是它做不到的事。
    """
    from src.compose.write_post import (
        HOOK_MAX_CHARS,
        HOOK_TARGET_CHARS,
        PARA_MAX_CHARS,
        PARA_TARGET_CHARS,
        check_body,
    )

    assert HOOK_TARGET_CHARS < HOOK_MAX_CHARS, "目標必須比硬上限鬆——不然目標就是上限"
    assert PARA_TARGET_CHARS < PARA_MAX_CHARS

    # **驗行為，不驗數字。** 模型寫「稍微超過目標」是常態（它不會數中文字），
    # 那不該觸發重寫——**不然就會發生實跑那種事：重寫三次、零改善、照樣出貨。**
    over_hook = "字" * (HOOK_TARGET_CHARS + 8)
    assert not check_hook(over_hook, POST, SOURCE), "稍微超過目標的 hook 不該被擋"

    over_para = "字" * (PARA_TARGET_CHARS + 15)
    assert not check_body(f"{over_para}\n\n第二段。"), "稍微超過目標的段落不該被擋"

    text = (PROMPT_DIR / "caption.md").read_text(encoding="utf-8")
    assert "判準是句數，不是字數" in text, "要用它做得到的方式下指令（句數），不是字數"


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
        return json.dumps(
            {"hook": hook, "body": GOOD_BODY, "hashtags": ["#a"]}, ensure_ascii=False
        )

    hook, _, _ = draft(POST, ARTICLE, flaky)
    assert calls["n"] == 2, "hook 只是複述 angle 時，應該請它重寫"
    assert hook == GOOD_HOOK


def test_a_hook_is_one_sentence_not_a_character_count() -> None:
    """**Human 2026-07-14：「hook 幹嘛定字數，就一句話不就得了。」他是對的。**

    「一句話」是**結構**——機器看得出來（不換行、句末標點只有一個）。
    「30 字」是**我對那個結構的猜測**，而那個猜測開始咬人：
    模型寫 31–35 字，重寫三次跨不過去，我照樣出貨。**三次 LLM 呼叫，零改善。**

    **驗結構，不要猜數字。**
    而真的需要一個上限時，那個數字也不該是我猜的——
    **hook 必須在 IG 折疊前看得完，而折疊線是 Instagram 定的（125 字）。**
    """
    from src.compose.write_post import IG_FOLD_CHARS

    assert HOOK_MAX_CHARS == IG_FOLD_CHARS, "硬上限要有來歷：折疊線是 IG 定的，不是我猜的"

    # 這兩句實跑時被舊規則（30 字）擋下來過——它們是好 hook
    assert not check_hook("你是不是也把舊筆記全部匯進 Obsidian，結果更難找？", POST, SOURCE)
    assert not check_hook("讓 AI 直接讀取你的 Obsidian 筆記，保有資料隱私與所有權。", POST, SOURCE)

    # 該擋的是「不只一句話」——那才是「摘要」的真正特徵
    assert check_hook("Obsidian 不會內建 AI。這是刻意的，而且跟你的隱私有關。", POST, SOURCE)
    assert check_hook("AI 讀不懂你的筆記庫。先給它一張地圖。", POST, SOURCE)
    assert check_hook("第一行\n第二行", POST, SOURCE)


def test_the_hook_leads_the_caption() -> None:
    ig = one("instagram", "正文內容", ["#a"])
    assert ig["caption"].startswith(GOOD_HOOK), "hook 必須是第一句——它是唯一一定會被讀到的東西"


# --- 程式該保證的事 ---------------------------------------------------------


def test_a_source_without_an_author_still_works() -> None:
    """**作者選填，來源必填。**（Human 2026-07-14）

    Google 的課程、官方文件、白皮書——很多素材本來就沒有個人作者。
    硬要一個，只會逼人瞎填。

    但**出處紅線沒鬆**：標題與連結還在，結尾卡照印。
    沒有作者就**不印那一段**，不要留一個孤零零的「｜」——那看起來像出錯。
    """
    no_author = {k: v for k, v in SOURCE.items() if k != "author"}
    attrib = attribution(no_author)
    assert attrib.startswith("原文：How I Use Obsidian")
    assert "｜" not in attrib, "沒有作者就不要留分隔線"
    assert no_author["url"] in attrib, "但出處還在"

    p = build_prompt(POST, {"source": no_author, "origin": "article"})
    assert "作者 （沒有標明作者）" in p, "prompt 要明講「沒有作者」，不要留一個空白讓模型去腦補"


def test_the_caption_does_not_repeat_the_attribution() -> None:
    """**出處在結尾卡上，caption 不用再放一次。**

    Human 2026-07-14：「文案當中不用放來源跟作者，圖片最後一張其實就有了。」

    而且那個網址在 IG 上根本不能點——貼在文案裡只是佔掉 142 字，
    **正文預算的三分之一**。

    紅線沒鬆：`post.json` 仍留著 `attribution` 欄位，
    而「出處不能消失」由 `collect_images()` 保證（沒有 outro 卡就報錯）。
    """
    ig = one("instagram", "第一段。\n\n第二段。", ["#筆記"])
    assert SOURCE["url"] not in ig["caption"], "網址不該再出現在 caption 裡"
    assert SOURCE["title"] not in ig["caption"]
    assert ig["attribution"], "但 post.json 仍要留著出處（紀錄用）"


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
    assert assemble("正文", ["#a"], hook="鉤子") == "鉤子\n\n正文\n\n#a"
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


def test_the_caption_does_not_narrate_a_video_the_reader_cannot_see() -> None:
    """**讀者看不到那支影片。他滑到的是輪播圖。**

    實跑出來的文案長這樣：
        「這支影片展示了如何用 AI 技能系統自動化工作流。」
        「影片建議，一開始保持簡單，專注於連結筆記。」
        「就像作者所說，Obsidian 只是看著你電腦上的一個資料夾。」

    **每一句都在幫一個讀者看不到的東西做導覽。** 他不會為了看懂 caption
    去點開一支 40 分鐘的英文影片。

    我會犯這個錯，是因為 prompt 從頭到尾都在叫模型「幫我整理**這支影片**」——
    **它就真的變成影片解說員了。**

    誠實由文末的出處標註負責，不必在每句話裡再提醒讀者「這是別人講的」。
    """
    from src.compose.write_post import narrates_the_source

    for bad in (
        "這支影片展示了如何自動化工作流",
        "影片建議一開始保持簡單",
        "就像作者所說，你擁有你的資料",         # 「作者所說」——第一版的 regex 只寫了「作者說」
        "就像影片中說的，Obsidian 只是一個資料夾",
        "正如 Nick Milo 所說，擁有你的想法",   # 人名夾在中間
        "這篇文章點出四個常見錯誤",
    ):
        assert narrates_the_source(bad), f"沒抓到：{bad}"

    # **誤殺比漏抓危險**：這些是「直接講內容」的正常句子，不准被擋
    for ok in (
        "一開始把外掛裝好裝滿，反而會分散你學核心功能的注意力。",
        "Obsidian 只是看著你電腦上的一個資料夾。你的筆記從頭到尾都在你手上。",
        "把技能存在自己的筆記裡，換工具就不必重來。",
    ):
        assert not narrates_the_source(ok), f"誤殺：{ok}"

    text = (PROMPT_DIR / "caption.md").read_text(encoding="utf-8")
    assert "不要當解說員" in text
    assert "讀者看不到" in text


def test_the_shared_body_must_fit_the_smaller_platform() -> None:
    """**兩個平台共用一份文案，所以它必須塞得進比較小的那個框**（Threads 500 字）。

    我原本沒算這筆帳：出處 145 字 + hook 30 字 → 正文只剩 320 字，
    但我同時叫模型寫「2–3 段、每段最多 160 字」＝ 最多 480 字。
    **規格從一開始就自相矛盾**，於是程式在下游把 Threads 版砍短，
    「共用一份文案」就這樣被我自己的規格拆散了。

    **與其讓下游收拾，不如讓上游寫得下。**
    （出處拿掉之後預算寬鬆多了，但這條測試守的是「規格必須自洽」。）
    """
    from src.compose.write_post import PARA_MAX_CHARS, body_budget, check_body

    budget = body_budget(GOOD_HOOK)
    assert budget > 0

    # 規格必須自洽：段落上限 × 最多段數，不能超過 Threads 給的預算
    assert PARA_MAX_CHARS * 3 <= budget, "段落規格跟 Threads 的預算打架"

    assert check_body("第一段。\n\n第二段。", budget=5), "超過預算就要退回重寫"
    assert not check_body(GOOD_BODY, budget=budget)


def test_the_body_must_be_paragraphs_not_one_lump() -> None:
    """**「2–3 段」是可以驗的，別只靠叮嚀。**

    實跑時它把整篇擠成一段 300 字的東西，Threads 還因此被砍掉尾巴。
    「這段話好不好」機器答不了；但「有沒有分段」「有沒有一坨 300 字」——**機器驗得出來。**
    """
    from src.compose.write_post import PARA_MAX_CHARS, check_body

    assert check_body("字" * 300), "整篇一坨，應該被抓"
    assert check_body("第一段。\n\n" + "字" * (PARA_MAX_CHARS + 1)), "單一段落過長，應該被抓"
    assert not check_body("第一段講清楚 hook 的承諾。\n\n第二段展開細節，講得更具體。")


def test_the_body_does_not_hook_the_reader_a_second_time() -> None:
    """hook 已經在鉤讀者了，正文不必再鉤一次。

    實跑時正文又問了一句「你是不是也覺得知識很難累積？」——
    **而卡片根本沒講過這件事**，那是憑空編讀者的經驗。
    """
    from src.compose.write_post import check_body

    assert check_body("你是不是也覺得知識很難累積？\n\n所以你需要自動化。")
    assert check_body("想像一下，AI 每天早上幫你寫好簡報。\n\n這就是每日簡報技能。")
    assert not check_body("把外掛裝好裝滿會分散注意力。\n\n先專注在連結筆記上。")


def test_the_body_reads_like_prose_not_a_bullet_list() -> None:
    """「一句一行」把文案變成**沒有項目符號的條列**——每句講一件不相干的事，東落西落。

    那條規則本來是為了 Threads 的可讀性訂的，結果毀了整段的連貫性。
    """
    text = (PROMPT_DIR / "caption.md").read_text(encoding="utf-8")
    assert "不要一句一行" in text
    assert "沒有項目符號的條列" in text


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

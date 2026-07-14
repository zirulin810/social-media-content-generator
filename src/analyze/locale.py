"""圖卡上的中文必須是台灣的中文。

**為什麼要有這個檔案：** prompt 早就寫著「一律輸出繁體中文」，模型還是把簡體原文抄上卡片
（2026-07-14 實跑：tonywang71 那篇的三則貼文整組是簡體）。

理由不難懂——素材本身是簡體，模型在「照抄依據」和「改寫成繁體」之間，
很容易把原文的措辭順手帶進卡片。**光靠叮嚀不會贏。**

所以這裡做機械檢查，兩種問題分開處理：

    簡體字     100% 驗得出來（字就是字）
    中國用語   靠對照表（視頻／軟件／文件夾…）——繁體字寫的中國話，看起來很正常，但一讀就出戲

**只檢查會印在卡片上的字。** `evidence.source_text` 是原文，簡體就該是簡體——
那是給人核對用的證據，改了它就等於偽造證據。
"""

from __future__ import annotations

import re
from typing import Any, Iterator, NamedTuple

from ..errors import ErrorCode, PipelineError

# ---------------------------------------------------------------------------
# 簡體字（只列「簡繁不同形」的常用字；同形字如「的」「是」不在此列）
# ---------------------------------------------------------------------------
#
# ⚠️ 這張表只能放「**簡體才有、繁體不用**」的字。
#
# 第一版我圖快，直接 union 了一串**詞**（`视频软件硬盘…`），Python 把它拆成單字，
# 於是「化、程、存、面、法、件」這些**兩邊寫法完全一樣**的字全被當成簡體。
# 那樣的表一上線，每一張正常的繁體卡都會被判定不合格。
#
# 所以這張表**必須用語料驗**：tests/test_locale.py 拿真實的繁體文件掃一遍，
# 只要有任何一個字被誤判，測試就失敗。**寧可漏抓，不可誤殺。**
SIMPLIFIED = set(
    "们个这说经过时来对国还没关软网络题笔记为么问动开发实现认识样点儿两内层"
    "书买卖东车专业务办员单双变复观见规视觉计论议讲设访证译语调谈请课"
    "读谁谢费资质贵赛边达远进连运选辽迁"
    "机权杀条极构标枢检楼树档欢欧歼汉汤沟泪测济浊浓"
    "炼烦热爱牵独狱猎现环电画础疗痴皱盘监确种积称稳穷窗竞"
    "筑简类粮纠红纪约级纯纲纳纵纷纸纽线练组细织终绍结绕给络绝统继续"
    "维绿罗职联肃肠脑脏节芦苏茎荐荣药获营萧蓝虑众衔补袭装"
    "让讯许农冲决况净凉减刘则创删刚剑劝胜劳势勋勤"
    "驱驶驾验骂骄骤鱼鲁鲜鸟鸡鸣鸿鹅鹰麦黄齐齿龄龙龟坏块坚坛坟垄垒"
    "壮声壳处备够头夸夺奋奖妆妇妈娱婴孙宁宝宠审宪宫宽宾寝寻导"
    "尔尘尝层属岁岂岗峡币师帅带帮帐帜应库庆废开异弃张弹强归当"
    "录彻忆忧怀态怜总恋恳恶闷惊惧惨愤愿懒执扩扫扬扰抛护报担拟拥择"
    "挤挥损换据挣掷揽摆摄摇摊撑敌数断无旧旷显"
    "术杂杨枫枪柜栏栈栋桥桨梦椭榄横"
    "款毁毙氢汇汹泼泽洁洒浅浑涂涛涝润涨"  # 「沈」不在此列——那是台灣常見的姓
    "渊渐渔渗湾湿溃溅滚满滤滨滩滞潜澜灭灯灵灾灿炉烂"
    "烧焕烛烟爷犹狈猪献琼瓯畴疟疮疯疾痒"
    "盏盐睁瞒矫码砖硕碍碱祷禄稣窃窍"
    "窜窝竖笋笼筛筝签箩粪紧绍"
    "价优传体党击划压厅厂历县参发叶号吗团园图圆场广战户丧临举乐习乡"
    "亚产亲仅从仑仓仪们优伟传伤伦伪佣侠侣俭债倾偿储儿"
)

CJK = re.compile(r"[一-鿿]")

# ---------------------------------------------------------------------------
# 中國用語 → 台灣用語
#
# **這一類比簡體字陰險**：它是繁體字寫的中國話。字都對，讀起來就是不對。
# 只收「在台灣幾乎不會這樣講」的詞，避免誤傷（例如「開啟」「筆記」兩邊都用，不收）。
# ---------------------------------------------------------------------------
TERMS: dict[str, str] = {
    "視頻": "影片",
    "音頻": "音訊",
    "網絡": "網路",
    "軟件": "軟體",
    "硬件": "硬體",
    "文件夾": "資料夾",
    "文檔": "文件",
    "質量": "品質",
    "信息": "資訊",
    "默認": "預設",
    "屏幕": "螢幕",
    "截屏": "截圖",
    "內存": "記憶體",
    "硬盤": "硬碟",
    "程序": "程式",
    "界面": "介面",
    "激活": "啟用",
    "緩存": "快取",
    "數據": "資料",
    "用戶": "使用者",
    "服務器": "伺服器",
    "命令行": "命令列",
    "終端機": "終端機",  # 兩邊都用，保留（放在這裡是為了提醒自己不要誤加）
    "博客": "部落格",
    "帖子": "貼文",
    "點贊": "按讚",
    "視頻號": "頻道",
    "公眾號": "粉絲專頁",
    "搜索": "搜尋",
    "打印": "列印",
    "安裝包": "安裝檔",
    "插件": "外掛",
    "郵箱": "信箱",
    "調研": "調查",
    "牛逼": "厲害",
    "靠譜": "可靠",
}
# 「終端機」是兩邊共用的，不該被當成問題
TERMS.pop("終端機", None)

# ⚠️ 曾經被我加進來、又拿掉的詞——**誤殺比漏抓危險**：
#   「碎碎念」「二次創作」：台灣本來就這樣講
#   「水平」：在「水平線」「水平方向」裡是正常詞，不能一律當中國用語
#   「打開」「筆記」「終端機」：兩邊共用
#   「腳本」：台灣正常用（電影腳本；技術圈也講）—— Human 2026-07-14 決定不擋
# 加詞之前先問：**台灣人真的不會這樣講嗎？** 不確定就不要加。
#
# 這張表是拿 Human 自己的 115 篇筆記掃出來校準的，不是我憑感覺列的。
# 掃出 6 個命中詞，逐一問過：
#   擋（Human 決定）：程序→程式、數據→資料、用戶→使用者、文檔→文件、界面→介面
#   不擋：腳本
# **邊界效應要認**：「程序正義」「大數據」也會被一起改。這是 Human 知情後選的。

TERM_RE = re.compile("|".join(sorted(TERMS, key=len, reverse=True)))


class Issue(NamedTuple):
    where: str
    kind: str  # simplified（機器說了算）/ term（要看語意，機器只標記）
    found: str
    suggest: str
    context: str = ""  # 用語疑慮要帶著整句——**語意只能在句子裡判斷**


def iter_texts(highlights: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """走訪**所有會被人看到的中文**。

    刻意**不含** `evidence.source_text`——那是原文，簡體就該是簡體。
    改掉證據就等於偽造證據。
    """
    for i, s in enumerate(highlights.get("summary", []), 1):
        yield f"summary/{i}", s

    for pi, post in enumerate(highlights["posts"], 1):
        at = f"第 {pi} 則"
        yield f"{at}／angle", post["angle"]
        if post.get("hook"):
            yield f"{at}／hook", post["hook"]
        for t in post.get("topics", []) or []:
            yield f"{at}／topic", t
        for t in post.get("hashtags", []) or []:
            yield f"{at}／hashtag", t

        for ci, card in enumerate(post["cards"], 1):
            c = f"{at}／第 {ci} 張卡（{card['type']}）"
            if card.get("title"):
                yield f"{c}／title", card["title"]
            if card["type"] == "point":
                yield f"{c}／body", card["body"]
            elif card["type"] == "steps":
                for si, s in enumerate(card["steps"], 1):
                    yield f"{c}／第 {si} 步", s["text"]
            elif card["type"] == "contrast":
                yield f"{c}／錯法", card["wrong"]["text"]
                yield f"{c}／正確", card["right"]["text"]
            elif card["type"] == "quote":
                yield f"{c}／text", card["text"]


_cc = None


def to_taiwan(text: str) -> str:
    """簡體 → 台灣正體。**這是程式的工作，不是模型的工作。**

    為什麼不叫模型改：轉換是確定性的字串對應，跑一輪 LLM 又慢又不保證改乾淨。

    為什麼不自己手刻對照表：**一簡對多繁**會害死人——
        发 → 發（發生）還是 髮（頭髮）？
        干 → 乾（乾淨）、幹（幹活）還是 干（干擾）？
        里 → 裡（裡面）還是 里（公里）？
    這要看詞、看語境。OpenCC 的 `s2twp` 有完整的詞彙表（而且順便把
    「视频→影片」「文件夹→資料夾」「默认→預設」這些用語一起轉成台灣說法）。

    我今天已經憑感覺猜錯五次數字了。這種有標準答案的東西，不要再自己發明。
    """
    # **沒有簡體字就不要碰它。**
    #
    # 2026-07-14 實跑抓到：模型寫的文案本來就是繁體，我卻無條件跑一次簡→繁，
    # 結果 OpenCC 的「一簡對多繁」規則在不該開火的地方開火了：
    #
    #     「他分享了如何…」 → 「他分享**瞭**如何…」
    #     「用連結**連接**不同的想法」 → 「用連結**連線**不同的想法」
    #
    # 轉換器是為「輸入是簡體」設計的。餵它繁體，它會把對的字改成錯的。
    # **工具用在它不該用的地方，比不用還糟。**
    if not any(c in SIMPLIFIED for c in text):
        return text

    global _cc
    if _cc is None:
        try:
            from opencc import OpenCC
        except ImportError as e:  # pragma: no cover
            raise PipelineError(
                ErrorCode.MISSING_INPUT,
                "少了簡繁轉換套件 opencc",
                hint="跑一次「安裝.bat」，或 pip install opencc-python-reimplemented",
            ) from e
        _cc = OpenCC("s2twp")  # s2twp = 簡體 → 正體（台灣，含詞彙轉換）
    return _cc.convert(text)


def localize(highlights: dict[str, Any]) -> int:
    """把所有會印在卡片上的字轉成台灣正體。回傳改了幾個欄位。

    **`evidence.source_text` 不動**——那是原文，簡體就該是簡體。
    改掉證據等於偽造證據：它存在的意義，就是讓人核對卡片有沒有超譯原文。
    """
    changed = 0

    def fix(container: Any, key: Any) -> None:
        nonlocal changed
        old = container[key]
        if isinstance(old, str) and old:
            new = to_taiwan(old)
            if new != old:
                container[key] = new
                changed += 1

    for i in range(len(highlights.get("summary", []))):
        fix(highlights["summary"], i)

    for post in highlights["posts"]:
        for k in ("angle", "hook"):
            if post.get(k):
                fix(post, k)
        for k in ("topics", "hashtags"):
            for i in range(len(post.get(k, []) or [])):
                fix(post[k], i)

        for card in post["cards"]:
            for k in ("title", "body", "text"):
                if card.get(k):
                    fix(card, k)
            if card["type"] == "steps":
                for s in card["steps"]:
                    fix(s, "text")
            elif card["type"] == "contrast":
                for side in ("wrong", "right"):
                    fix(card[side], "text")

    return changed


def scan(highlights: dict[str, Any]) -> list[Issue]:
    """找出簡體字（確定的）與可疑用語（不確定的）。

    **兩者的確定性天差地遠，所以 Issue 帶著 kind，呼叫端要分開處理：**

        simplified  機器說了算——字就是字，沒有語意問題
        term        **機器說不準**。同一個詞，語意不同就是兩件事：
                    「程序正義」「大數據」「電影腳本」都是台灣的正常說法，
                    但「這個程序有 bug」「把數據存起來」就是中國用語。
                    黑名單看到的是字串，看不到語意——**所以它只負責標記，不負責判決**。
    """
    issues: list[Issue] = []
    for where, text in iter_texts(highlights):
        if not text:
            continue
        bad = sorted({c for c in CJK.findall(text) if c in SIMPLIFIED})
        if bad:
            issues.append(Issue(where, "simplified", "".join(bad), "改寫成台灣繁體"))
        for m in dict.fromkeys(TERM_RE.findall(text)):
            issues.append(Issue(where, "term", m, TERMS[m], text))
    return issues


def blocking(issues: list[Issue]) -> list[Issue]:
    """只有簡體字是硬性的。用語疑慮不擋——那要看語意，機器判不準。"""
    return [i for i in issues if i.kind == "simplified"]


def describe(issues: list[Issue]) -> str:
    """餵回給模型的清單。

    簡體字：直接要求改。
    可疑用語：**連同整句一起給，讓模型自己判斷語意**——不是叫它無腦替換。
    這是刻意的：我這張表分不出「程序正義」和「這個程序有 bug」，但模型分得出。
    """
    lines = []
    for i in issues:
        if i.kind == "simplified":
            lines.append(f"- {i.where}：出現簡體字「{i.found}」→ 整句改寫成台灣繁體")
        else:
            lines.append(
                f"- {i.where}：「{i.found}」在這句話裡是中國用語嗎？"
                f"是的話改成「{i.suggest}」；如果是台灣的正常用法（例：程序正義、大數據、電影腳本）就**保持原樣**。\n"
                f"    原句：{i.context}"
            )
    return "\n".join(lines)


__all__ = ["scan", "blocking", "describe", "iter_texts", "Issue", "TERMS", "SIMPLIFIED"]

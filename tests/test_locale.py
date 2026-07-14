"""圖卡上的中文必須是台灣的中文。

分工（2026-07-14 與 Human 定的）：

    簡體字   **程式**用 OpenCC 轉（確定性的字串對應，不必也不該叫模型做）
    中國用語 **人**判斷（要看語意：「程序正義」是台灣話，「這個程序有 bug」不是）
    evidence **不准動**（那是原文；改掉證據等於偽造證據）

這裡最重要的兩條：
- `test_no_false_positives_on_real_traditional_text`：**寧可漏抓，不可誤殺**
- `test_evidence_is_never_converted`：證據的完整性比什麼都重要
"""

from __future__ import annotations

import copy
import re

from src.analyze.locale import SIMPLIFIED, TERMS, localize, scan, to_taiwan
from src.paths import PROJECT_ROOT

CJK = re.compile(r"[一-鿿]")


def _highlights(body: str = "乾淨的繁體", step: str = "第一步", quote: str = "一句話") -> dict:
    return {
        "summary": ["內部摘要"],
        "posts": [
            {
                "angle": "角度",
                "hook": "副標",
                "cards": [
                    {"type": "point", "title": "標題", "body": body,
                     "evidence": [{"para_index": 0, "source_text": "视频里说的原话"}]},
                    {"type": "steps", "title": "步驟", "steps": [
                        {"text": step, "evidence": [{"para_index": 0, "source_text": "软件"}]}]},
                    {"type": "quote", "text": quote, "verbatim": False,
                     "evidence": [{"para_index": 0, "source_text": "网络"}]},
                ],
            }
        ],
    }


# --- 程式做的部分：轉換 ------------------------------------------------------


def test_simplified_cards_are_converted_by_the_program() -> None:
    h = _highlights(body="这个软件的用户界面不错", step="打开设置", quote="第二大脑")
    assert localize(h) == 3
    post = h["posts"][0]
    assert post["cards"][0]["body"] == "這個軟體的使用者介面不錯"
    assert post["cards"][1]["steps"][0]["text"] == "開啟設定"
    assert post["cards"][2]["text"] == "第二大腦"


def test_one_to_many_characters_use_context_not_a_naive_table() -> None:
    """**手刻對照表必死的地方**：一個簡體字對到好幾個繁體字。

    发 → 發（發生）還是 髮（頭髮）？干 → 乾／幹／干？里 → 裡／里？
    這要看詞。所以用 OpenCC，不要自己發明。
    """
    assert to_taiwan("头发很长") == "頭髮很長"
    assert to_taiwan("这里干活很累") == "這裡幹活很累"   # 干 → 幹（看詞判斷）
    assert to_taiwan("这里面有问题") == "這裡面有問題"   # 里 → 裡


def test_an_ambiguous_char_alone_is_left_alone_and_that_is_correct() -> None:
    """**這條是「沒有簡體字就不要碰」的代價，而且我甘願付。**

    「干」繁體也在用（干擾、干預），所以它不能進簡體字表——進去就會誤判「干擾」。
    於是「干活很累」這串偵測不到任何簡體字 → 守門判定「這是繁體」→ 不轉。
    結果：`干活` 沒有變成 `幹活`。**這是漏抓。**

    但代價的另一邊是：把已經正確的繁體丟給 OpenCC，它會**把對的字改成錯的**——

        「不要干擾我」 → 「不要幹擾我」   ← 真的會這樣

    **漏抓只是少改一個字；誤殺是把使用者寫對的東西改壞。** 所以守門留著。

    （真實的簡體句子一定帶著其他明確的簡體字——「这里干活很累」就轉得對。
    孤立的歧義字只會出現在測試裡，不會出現在真實素材裡。）
    """
    assert to_taiwan("干活很累") == "干活很累"      # 漏抓，接受
    assert to_taiwan("不要干擾我") == "不要干擾我"   # 但沒有誤殺，這才是重點
    assert to_taiwan("干預他人的決定") == "干預他人的決定"


def test_traditional_text_is_never_touched() -> None:
    """**沒有簡體字就不要碰它。**

    2026-07-14 實跑抓到：文案本來就是繁體，我卻無條件跑一次簡→繁，
    OpenCC 的「一簡對多繁」規則就在不該開火的地方開火了：

        「他分享了如何…」   → 「他分享**瞭**如何…」
        「用連結連接想法」   → 「用連結**連線**想法」

    轉換器是為「輸入是簡體」設計的。餵它繁體，它會把對的字改成錯的。
    **工具用在它不該用的地方，比不用還糟。**
    """
    clean_zh_tw = [
        "他分享了如何用 AI 技能系統來自動化工作流",
        "使用連結和反向連結來連接不同的想法",
        "介紹了如何結合 Obsidian 和 Claude Code",
        "點選連結並連接事物",
    ]
    for s in clean_zh_tw:
        assert to_taiwan(s) == s, f"繁體句子被轉壞了：{s} → {to_taiwan(s)}"


def test_evidence_is_never_converted() -> None:
    """`source_text` 是原文——簡體就該是簡體。**改掉證據等於偽造證據。**"""
    h = _highlights(body="这个软件很好")
    localize(h)
    ev = h["posts"][0]["cards"][0]["evidence"][0]
    assert ev["source_text"] == "视频里说的原话", "證據被改了——它是給人核對用的，不能動"
    assert h["posts"][0]["cards"][1]["steps"][0]["evidence"][0]["source_text"] == "软件"


def test_converting_twice_changes_nothing() -> None:
    h = _highlights(body="这个软件很好")
    localize(h)
    frozen = copy.deepcopy(h)
    assert localize(h) == 0
    assert h == frozen


def test_no_simplified_survives_conversion() -> None:
    h = _highlights(body="这个软件的质量很好，默认会用缓存", step="打开设置，选择文件夹")
    localize(h)
    assert [i for i in scan(h) if i.kind == "simplified"] == []


# --- 人判斷的部分：用語只標記，不判決 ----------------------------------------


def test_mainland_terms_are_flagged_with_their_sentence() -> None:
    """用語要**連同整句**標出來——語意只能在句子裡判斷，機器判不準。"""
    h = _highlights(body="這個工具的質量很好")
    issues = [i for i in scan(h) if i.kind == "term"]
    assert issues[0].found == "質量" and issues[0].suggest == "品質"
    assert "這個工具的質量很好" in issues[0].context, "沒有上下文，人就沒辦法判斷語意"


def test_no_false_positives_on_real_traditional_text() -> None:
    """拿真的繁體文件掃——**一個字都不准被誤判成簡體**。

    誤殺比漏抓危險得多：漏抓只是一個簡體字上了卡片；
    誤殺會讓正常的文章跑不完，而錯誤訊息還指著無辜的字。

    （第一版字表我 union 了一串「詞」，Python 拆成單字，
    「化、程、存、面、法、件」這些兩邊同形的字全被當成簡體。）
    """
    corpus = list((PROJECT_ROOT / "docs").glob("*.md")) + [PROJECT_ROOT / "README.md"]
    bad: dict[str, str] = {}
    for f in corpus:
        for c in CJK.findall(f.read_text(encoding="utf-8")):
            if c in SIMPLIFIED:
                bad.setdefault(c, f.name)
    assert not bad, f"這些字被誤判成簡體（繁體本來就這樣寫）：{bad}"


def test_term_table_maps_to_something_different() -> None:
    assert not [k for k, v in TERMS.items() if k == v]

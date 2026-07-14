"""端到端 CLI：**它不做任何決定，只負責接線。**

所有判斷都在各階段裡（要不要重跑、拆不拆卡、退不退回重寫）。
**CLI 是接線員，不是主管**——這樣 `.bat` 跑得到的東西，CLI 一定跑得到，
不會出現「兩條路徑行為不一致」這種最難查的 bug。
"""

from __future__ import annotations

import pytest

from src.cli import STAGES, build_parser, main
from src.paths import PROJECT_ROOT


def test_the_four_stages_are_in_order() -> None:
    assert STAGES == ("ingest", "analyze", "render", "compose")


def test_usage_is_printed_without_args(capsys) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
    assert "usage" in capsys.readouterr().err.lower()


def test_a_missing_file_fails_clearly(capsys) -> None:
    code = main(["這個檔案不存在.md"])
    err = capsys.readouterr().err
    assert code == 1
    assert "找不到檔案" in err
    assert "萬用字元" in err, "錯誤訊息要告訴人下一步能做什麼"


def test_ratio_is_validated_not_silently_ignored() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["a.md", "--ratio", "16:9"])


def test_the_cli_does_not_reimplement_skip_logic() -> None:
    """**跳過與否只有一份事實來源**（`paths.is_stale`）。

    CLI 印「↩ 沿用 / ✎ 重跑」只是為了給人看；真正決定的是各階段自己。
    如果 CLI 另外實作一份跳過邏輯，兩份就會走散——
    而「產物過期而不自知」正是今天踩了三次的坑。
    """
    src = (PROJECT_ROOT / "src" / "cli.py").read_text(encoding="utf-8")
    assert "is_stale" in src
    assert "os.path.exists" not in src and ".exists() and not" not in src


def test_every_stage_checks_staleness_against_its_own_code() -> None:
    """**程式碼也是輸入。** 四個階段一個都不能漏。"""
    for mod in (
        "src/ingest/read_article.py",
        "src/analyze/extract_highlights.py",
        "src/render/render_cards.py",
        "src/compose/write_post.py",
    ):
        text = (PROJECT_ROOT / mod).read_text(encoding="utf-8")
        assert "is_stale" in text, f"{mod} 還在用「檔案存在就跳過」"
        assert "Path(__file__).parent" in text, f"{mod} 沒把自己算進輸入"

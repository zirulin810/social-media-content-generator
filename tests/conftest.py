"""測試的共用前提。

**測試不吃使用者本機的 settings.json。** 設定是人可調的；測試斷言的是「預設行為」，
所以整套測試一律指向一個不存在的設定檔（＝內建預設）。
要測「設定改了會怎樣」的，自己再覆寫 SETTINGS_FILE（見 test_settings.py）。
"""

import pytest


@pytest.fixture(autouse=True)
def _default_settings_for_all_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("SETTINGS_FILE", str(tmp_path / "_no_settings.json"))
    yield

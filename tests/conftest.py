import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, state  # noqa: E402


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """一个全新的素材库（路径含中文和空格）。"""
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.toml")
    monkeypatch.setattr(config, "_cache", None)
    state.reset_for_tests()
    c = state.init_library(str(tmp_path / "酷玩 素材库"))
    yield c
    state.reset_for_tests()

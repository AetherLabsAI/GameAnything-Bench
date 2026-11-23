import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _memory_sandbox(tmp_path_factory, monkeypatch):
    """the refine rounds' experience memory (gab/refine/memory.py, on by default) writes under a temp dir in every test and never
    calls a model"""
    monkeypatch.setenv("GAB_MEMORY_DIR", str(tmp_path_factory.mktemp("memory")))
    from gab.refine import memory
    def no_live_call(prompt, model): raise RuntimeError("no live model call in tests")
    monkeypatch.setattr(memory, "_chat", no_live_call)

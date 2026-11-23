"""gab get-game: one command per game (image, files, config, start check)"""
import json
import pytest
from gab.setup import getgame as G


@pytest.fixture
def fake(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(G.config, "REPO", tmp_path)
    monkeypatch.setenv("GAB_COMMERCIAL_DIR", str(tmp_path / "commercial")); monkeypatch.setenv("GAB_HOST", "pool")
    monkeypatch.setattr(G, "image", lambda g: calls.append(("image", g)))
    monkeypatch.setattr(G, "check", lambda g: calls.append(("check", g)))
    monkeypatch.setitem(G.FILES, "minecraft", lambda: calls.append(("files", "minecraft")))
    return calls, tmp_path


def test_open_pulls_and_checks(fake):
    calls, tmp = fake
    G.get_game("open")
    assert [c for c in calls if c[0] == "image"] == [("image", g) for g in G.OPEN]
    assert ("check", "flare") in calls and json.loads((tmp / "gab.local.json").read_text())["host"] == "docker"


def test_commercial_steam_goes_through_steamsetup(fake, capsys, monkeypatch):
    calls, _ = fake
    from gab.setup import steamsetup
    monkeypatch.setattr(steamsetup, "setup_game", lambda g, do_check=True: calls.append(("steam", g)))
    G.get_game("minecraft,terraria", do_check=False)
    assert calls == [("image", "minecraft"), ("files", "minecraft"), ("steam", "terraria")]
    assert "ready: minecraft, terraria" in capsys.readouterr().out


def test_unknown_game(fake):
    with pytest.raises(SystemExit): G.get_game("doom")


def test_failure_is_reported(fake, monkeypatch, capsys):
    calls, _ = fake
    def boom(g): raise RuntimeError("did not start")
    monkeypatch.setattr(G, "check", boom)
    with pytest.raises(SystemExit): G.get_game("flare")
    assert "FAILED: flare: did not start" in capsys.readouterr().out

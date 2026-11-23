"""Colobot adapter, offline: deliverable layout / build check, level names, pool ids vs probes, compare on the recorded pool
verification (tests/data/colobot: A/A aa9 / aa10 and the three negative mods of 2026-10-30; save_load from their save re-runs),
the state composition (STATE_SPEC) from a recorded shim snapshot."""
import json, shutil
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.colobot import probes as P
from gab.game.colobot.adapter import Adapter

D = Path(__file__).parent / "data" / "colobot"
REPO = Path(__file__).resolve().parents[1]
G = gbase.gdir("colobot")


def rec(n): return json.loads((D / f"{n}.json").read_text())


def items():
    p = yaml.safe_load((REPO / "benchmark" / "pools" / "colobot" / "pool.yaml").read_text())
    return p, [i for i in p["always"] + p["pool"] if i["status"] == "ready"]


def fails(base, got): return sorted(i["id"] for i in items()[1] if P.item_result(i["probe"], base, got)[0] is False)


def test_registered():
    assert "colobot" in gbase.GAMES and gbase.title("colobot") == "Colobot: Gold Edition"


def test_pool_matches_probes():
    p, its = items()
    assert {i["probe"].split(".")[0] for i in its} - {"log_errors"} <= set(P.ALL)
    ids = {i["id"] for i in its}
    assert set(p["default_picks"]) <= ids and p["game"] == "colobot" and len(its) == 15
    neg = yaml.safe_load((G / "negmods" / "negmods.yaml").read_text())
    for m, spec in neg.items():
        assert (G / "negmods" / m).is_dir() and set(spec["expect_fail"]) <= ids


def test_aa_and_negatives_on_recorded_runs():
    a, b = rec("aa9"), rec("aa10")
    assert fails(a, b) == [] and fails(b, a) == []
    neg = yaml.safe_load((G / "negmods" / "negmods.yaml").read_text())
    for m, spec in neg.items(): assert fails(a, rec(m)) == sorted(spec["expect_fail"]), m


def test_layout_and_build(tmp_path):
    ad = Adapter.__new__(Adapter)
    tpl = G / "devkit" / "template" / "mod"
    ok, log = ad.build("x", tpl); assert ok, log
    mods, st = Adapter._layout(tpl); assert mods == [tpl] and st == {"scene": "custom101"}
    assert Adapter.mod_levels(mods) == {"custom101": "levels/custom/mymod/level001/scene.txt"}
    # selfcheck layout: negative mods one level down
    m = tmp_path / "mod"; shutil.copytree(G / "negmods" / "neg_ants", m / "neg_ants"); shutil.copytree(G / "negmods" / "neg_level", m / "neg_level")
    mods, _ = Adapter._layout(m); assert [x.name for x in mods] == ["neg_ants", "neg_level"]
    assert Adapter.mod_levels(mods) == {"missions201": "levels/missions/chapter002/level001/scene.txt"}
    # problems are reported
    bad = tmp_path / "bad"; (bad / "levels/custom/a/level001").mkdir(parents=True); (bad / "stuff").mkdir()
    (bad / "levels/custom/a/level001/scene.txt").write_text("Title.E text=\"x\"\n"); (bad / "start.yaml").write_text("scene: nowhere\n")
    ok, log = ad.build("x", bad)
    assert not ok and "no astronaut" in log and "chaptertitle" in log and "nowhere" in log and "stuff" in log
    ok, log = ad.build("x", tmp_path / "empty"); assert not ok


def test_statecheck_mod_builds():
    sc = yaml.safe_load((G / "statecheck" / "statecheck.yaml").read_text())
    ad = Adapter.__new__(Adapter)
    ok, log = ad.build("x", G / "statecheck" / sc["mod"]["dir"]); assert ok, log


def test_compose_state():
    snap = {"t": 30.0, "frames": 900, "_age": 0,
            "objects": [{"id": 0, "type": "Me", "pos": [-53, 39, 16.6], "orientation": 11.5, "shield": 1, "energy": 1, "altitude": 0, "lifetime": 12.3, "load": None},
                        {"id": 1, "type": "WheeledGrabber", "pos": [-45, 30, 15.7], "energy": 0.99, "shield": 1, "cell": "PowerCell", "load": None, "team": 0},
                        {"id": 5, "type": "PowerCell", "pos": [-39, 30, 15.7], "energy": 0.42, "shield": 1, "team": 0}],
            "programs": [{"owner": 1, "function": "GabState", "vars": {"gab_quest_stage": 2, "gab_marker": "stage_two"}}],
            "messages": [{"t": 28.0, "text": "GAB TEST HUD: the beacon is near", "type": 0, "from": 1}]}
    log = "[INFO]: Loading level: levels/custom/gabstate/level001/scene.txt\n"
    inst = {"start": {"scene": "custom101"}, "levels": {"custom101": "levels/custom/gabstate/level001/scene.txt"}}
    st = Adapter.compose(snap, log, True, inst, list(inst["levels"].values()))
    e = st["engine"]
    assert e["screen"] == "level" and e["level_from_mod"] and e["time_s"] == 12.3 and e["object_counts"]["PowerCell"] == 1
    assert [o["type"] for o in st["mod"]["objects"]] == ["WheeledGrabber", "PowerCell"]
    assert st["mod"]["programs"][0]["vars"]["gab_marker"] == "stage_two"
    assert st["screen_text"][0].startswith("GAB TEST HUD") and "2 s ago" in st["screen_text"][0]
    menu = Adapter.compose({"t": 3, "frames": 80, "objects": []}, "", True, {}, [])
    assert menu["engine"]["screen"] == "menu" and menu["mod"] == {"objects": [], "programs": [], "messages_total": 0} and menu["screen_text"] == []
    stock = Adapter.compose(snap, "Loading level: levels/missions/chapter001/level001/scene.txt\nTeam 1 won\n", False, {}, [])
    assert stock["engine"]["screen"] == "exited" and stock["mod"]["objects"] == []
    d = gbase.state_diff(menu, st); assert d.get("changed") or d.get("added")


def test_click_accepts_a_button_name(monkeypatch):
    a = Adapter(object(), root="/x")
    cmds = []
    monkeypatch.setattr(a, "_x", lambda inst, cmd, timeout=180: cmds.append(cmd))
    a.input("i", {"do": "click", "x": 1, "y": 2, "button": "right"})
    assert "mousedown 3" in cmds[0] and "mouseup 3" in cmds[0]

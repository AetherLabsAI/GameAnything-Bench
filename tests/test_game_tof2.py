"""Tanks of Freedom II adapter, offline: registration, deliverable build check, pool ids vs probes, compare on the recorded pool
verification (tests/data/tof2: A/A aa3 / aa4 and the three negative mods of 2026-10-30, negatives trimmed to their probes),
the state bridge script."""
import json
from pathlib import Path

import yaml

from gab.game import base as gbase, godot_common as GC
from gab.game.tof2 import probes as P
from gab.game.tof2.adapter import Adapter

D = Path(__file__).parent / "data" / "tof2"
REPO = Path(__file__).resolve().parents[1]


def rec(n): return json.loads((D / f"{n}.json").read_text())


def items():
    p = yaml.safe_load((REPO / "benchmark" / "pools" / "tof2" / "pool.yaml").read_text())
    return p, [i for i in p["always"] + p["pool"] if i["status"] == "ready"]


def probe_of(spec): return P.SUB.get(spec, (spec.split(".")[0], None))[0]


def fails(base, got, only=None):
    return sorted(i["id"] for i in items()[1] if i["probe"] != "log_errors" and (only is None or probe_of(i["probe"]) in only)
                  and P.item_result(i["probe"], base, got)[0] is False)


def test_registered():
    assert "tof2" in gbase.GAMES and gbase.title("tof2") == "Tanks of Freedom II"


def test_pool_matches_probes():
    p, its = items()
    assert {probe_of(i["probe"]) for i in its} - {"log_errors"} <= set(P.ORDER)
    ids = {i["id"] for i in its}
    assert set(p["default_picks"]) <= ids
    neg = yaml.safe_load((gbase.gdir("tof2") / "negmods" / "negmods.yaml").read_text())
    for m, spec in neg.items():
        assert set(spec["expect_fail"]) <= ids and set(spec["quick_probes"]) <= set(P.ORDER)


def test_aa_and_negatives_on_recorded_runs():
    a = rec("aa3")
    assert fails(a, rec("aa4")) == [] and P.item_result("log_errors", a, rec("aa4"))[0] is True
    assert fails(a, rec("neg_map_4p_cross"), {"maps"}) == ["TF-MAPS"]
    assert fails(a, rec("neg_campaign_prologue"), {"campaign"}) == ["TF-CAMPAIGN"]
    assert fails(a, rec("neg_tank_attack"), {"data", "combat"}) == ["TF-COMBAT", "TF-UNIT-DATA"]
    g = json.loads(json.dumps(a)); g["maps"]["_crashed"] = "rc=134 done=False"
    assert P.item_result("log_errors", a, g)[0] is False


def test_build_check(tmp_path):
    class S: sandbox, home, display = "x", "/home/player", ":101"
    ad = Adapter(S())
    ok, log = ad.build("i", gbase.gdir("tof2") / "devkit" / "template" / "mod"); assert ok, log
    ok, log = ad.build("i", gbase.gdir("tof2") / "statecheck" / "gab_statecheck"); assert ok, log
    m = tmp_path / "mod" / "assets" / "maps" / "skirmish"; m.mkdir(parents=True)
    (m / "bad.tofmap.json").write_text("{\"metadata\": {}}"); (m / "broken.tofmap.json").write_text("{")
    c = tmp_path / "mod" / "campaign" / "x"; c.mkdir(parents=True); (c / "campaign.json").write_text("{\"name\": \"x\"}")
    ok, log = ad.build("i", tmp_path / "mod")
    assert not ok and "tiles" in log and "JSON error" in log and "missions" in log


def test_state_bridge_script():
    s = GC.bridge(gbase.gdir("tof2") / "state_engine.gd")
    for f in ("func _engine()", "func _ref_pos()", "func _watch()", "func _mod_extra()"):
        assert s.count("\n" + f) == 1, f
    assert "import" in P.APPLY_SH and "--remove-destination \"$R/src/$f.import\"" in P.APPLY_SH


def test_run_probes_maps_views_to_probes():
    class S: sandbox, home, display = "x", "/home/player", ":101"
    ad = Adapter(S()); got = {}
    class FakeProbes:
        def __init__(self, *a, **k): pass
        def session(self, names, mods): got["names"] = names; return {}
        def cleanup(self): pass
    import gab.game.tof2.adapter as A
    orig, A.P.TOFProbes = A.P.TOFProbes, FakeProbes
    ad.install = lambda: True
    try: ad.run_probes(["produce", "move", "capture", "win", "maps", "log_errors"])
    finally: A.P.TOFProbes = orig
    assert got["names"] == ["capture_win", "maps", "produce_move"]

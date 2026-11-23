"""Celeste 64 adapter: offline checks (no sandbox). Recorded probe sessions: tests/data/celeste64/ = the pool verification
2026-10-30 (aa1, aa2 vanilla; neg_map / neg_text / neg_dash = the three negative mods, neg_text = its repeat run alone)."""
import json
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.celeste64 import probes as P
from gab.game.celeste64.adapter import Adapter

D = Path(__file__).parent / "data" / "celeste64"
REPO = Path(__file__).resolve().parents[1]
POOL = yaml.safe_load((REPO / "benchmark" / "pools" / "celeste64" / "pool.yaml").read_text())
READY = [i for i in POOL["always"] + POOL["pool"] if i["status"] == "ready"]
rd = lambda n: json.loads((D / f"{n}.json").read_text())


def fails(base, got): return sorted(i["id"] for i in READY if P.item_result(i["probe"], base, got)[0] is not True)


def test_registered():
    assert "celeste64" in gbase.GAMES and gbase.title("celeste64").startswith("Celeste 64")


def test_pool_probes_exist():
    assert set(POOL["default_picks"]) <= {i["id"] for i in READY}
    for i in READY:
        assert i["probe"] == "log_errors" or i["probe"].split(".")[0] in P.ALL, i


def test_aa_and_negatives_on_recordings():
    a = rd("aa1")
    assert fails(a, rd("aa2")) == []
    neg = yaml.safe_load((REPO / "gab/game/celeste64/negmods/negmods.yaml").read_text())
    for m, spec in neg.items(): assert fails(a, rd(m)) == sorted(spec["expect_fail"]), m


def test_compare_rules():
    a = rd("aa1")
    assert P.item_result("log_errors", a, dict(a, alive_at_end=False))[0] is False
    g = json.loads(json.dumps(a)); g["boot"]["maps"].append("my_new_map"); g["boot"]["n_textures"] += 3
    assert P.compare("boot", a["boot"], g["boot"])[0] is True                 # additions are allowed
    g = json.loads(json.dumps(a)); g["move"]["run_top_speed"] += 0.4
    assert P.compare("move", a["move"], g["move"])[0] is True                 # inside the A/A tolerance
    g["move"]["run_top_speed"] += 5
    assert P.compare("move", a["move"], g["move"])[0] is False
    assert P.compare("dash", a["dash"], {"error": "no result"})[0] is False


def test_layout_and_build(tmp_path):
    m = tmp_path / "mod"; (m / "a" / "Content" / "Maps").mkdir(parents=True); (m / "b" / "Source").mkdir(parents=True)
    (m / "a" / "Content" / "Maps" / "x.map").write_text('{\n"classname" "worldspawn"\n}\n')
    (m / "b" / "Source" / "X.cs").write_text("namespace Celeste64; class X {}")
    assert [u.name for u in Adapter._layout(m)] == ["a", "b"]
    assert Adapter._layout(m / "a") == [m / "a"]
    h = Adapter._src_hash(Adapter._layout(m)); assert h and Adapter._src_hash([m / "a"]) is None
    ad = Adapter.__new__(Adapter)
    ok, log = ad.build("i", m); assert ok, log
    (m / "a" / "Content" / "Levels.json").write_text('[{"ID": "X"}]')
    ok, log = ad.build("i", m); assert not ok and "Levels.json" in log
    ok, log = ad.build("i", tmp_path / "empty"); assert not ok


def test_statecheck_spec():
    sc = REPO / "gab/game/celeste64/statecheck"
    s = yaml.safe_load((sc / "statecheck.yaml").read_text())
    assert (sc / s["mod"]["dir"] / "Source").is_dir() and s["vanilla"]["engine"] and s["mod"]["expect"]
    assert gbase.check_trace({"steps": s["vanilla"]["steps"]}) == []
    lv = json.loads((sc / s["mod"]["dir"] / "Content/Levels.json").read_text())
    assert lv[0]["Map"] == "gab_statecheck" and (sc / s["mod"]["dir"] / "Content/Maps/gab_statecheck.map").exists()


def test_arena_map():
    m = P.arena_map(); assert '"classname" "PlayerSpawn"' in m and m.count("{") == m.count("}")

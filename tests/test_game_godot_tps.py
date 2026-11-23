"""Godot TPS adapter, offline: registration, deliverable build check, pool ids vs probes, compare on the recorded pool
verification (tests/data/godot-tps: A/A aa1 / aa2 and the three negative mods of 2026-10-30, negatives trimmed to their probes),
the state bridge script."""
import json
from pathlib import Path

import yaml

from gab.game import base as gbase, godot_common as GC
from gab.game.godot_tps import probes as P
from gab.game.godot_tps.adapter import Adapter

D = Path(__file__).parent / "data" / "godot-tps"
REPO = Path(__file__).resolve().parents[1]


def rec(n): return json.loads((D / f"{n}.json").read_text())


def items():
    p = yaml.safe_load((REPO / "benchmark" / "pools" / "godot-tps" / "pool.yaml").read_text())
    return p, [i for i in p["always"] + p["pool"] if i["status"] == "ready"]


def fails(base, got, only=None):
    return sorted(i["id"] for i in items()[1] if (only is None or i["probe"].split(".")[0] in only) and P.item_result(i["probe"], base, got)[0] is False)


def test_registered():
    assert "godot-tps" in gbase.GAMES and gbase.gdir("godot-tps").name == "godot_tps" and gbase.title("godot-tps").startswith("Godot TPS")


def test_pool_matches_probes():
    p, its = items()
    names = {i["probe"].split(".")[0] for i in its}
    assert names - {"log_errors", "render"} <= set(P.ORDER)
    assert {i["probe"].split(".")[1] for i in its if i["probe"].startswith("render.")} <= set(P.RENDER_READY)
    ids = {i["id"] for i in its}
    assert set(p["default_picks"]) <= ids
    neg = yaml.safe_load((gbase.gdir("godot-tps") / "negmods" / "negmods.yaml").read_text())
    for m, spec in neg.items():
        assert set(spec["expect_fail"]) <= ids and any((gbase.gdir("godot-tps") / "negmods" / m).rglob("*.*"))


def test_aa_and_negatives_on_recorded_runs():
    a = rec("aa1")
    assert fails(a, rec("aa2")) == []
    assert fails(a, rec("neg_jump7"), {"settings", "jump"}) == ["GT-JUMP", "GT-SETTINGS"]
    assert fails(a, rec("neg_robothp2"), {"settings", "shoot"}) == ["GT-ROBOT-DAMAGE", "GT-SETTINGS"]
    assert fails(a, rec("neg_menu_nosettings"), {"render"}) == ["GT-MENU"]
    assert P.item_result("settings", a, {"settings": {"error": "x"}})[0] is False
    assert P.item_result("render.menu", {}, a)[0] is None                 # no baseline -> could not test


def test_build_check(tmp_path):
    class S: sandbox, home, display = "x", "/home/player", ":101"
    ad = Adapter(S())
    ok, log = ad.build("i", gbase.gdir("godot-tps") / "devkit" / "template" / "mod"); assert ok, log
    ok, log = ad.build("i", gbase.gdir("godot-tps") / "statecheck" / "gab_statecheck"); assert ok, log
    m = tmp_path / "mod"; (m / ".godot").mkdir(parents=True); (m / ".godot" / "x").write_text("1")
    (m / "a.tscn").write_text("garbage"); (m / "b.png.import").write_text("x")
    ok, log = ad.build("i", m)
    assert not ok and ".godot" in log and "a.tscn" in log and ".import" in log
    assert ad.requirements()["gpu"] is True


def test_state_bridge_script():
    s = GC.bridge(gbase.gdir("godot-tps") / "state_engine.gd")
    for f in ("func _engine()", "func _ref_pos()", "func _watch()", "func _mod_extra()", "func _screen_text()", "func _mod()"):
        assert s.count("\n" + f) == 1, f
    sh = GC.meta_sh("/v", "/m", "/s")
    assert "modfiles.txt" in sh and "vanilla_autoloads.txt" in sh and "vanilla_actions.txt" in sh
    assert GC.state_of(False, None) == {"running": False, "engine": {"screen": "not running"}, "mod": {}, "screen_text": []}
    sc = yaml.safe_load((gbase.gdir("godot-tps") / "statecheck" / "statecheck.yaml").read_text())
    assert (gbase.gdir("godot-tps") / "statecheck" / sc["mod"]["dir"]).is_dir() and sc["vanilla"]["engine"]


def test_mod_layout(tmp_path):
    from gab.game.godot_tps.adapter import TOPS
    m = tmp_path / "mod"; (m / "player").mkdir(parents=True); (m / "player" / "player.gd").write_text("x")
    assert GC.layout(m, TOPS) == [m]                                       # a plain overlay
    w = tmp_path / "wrap"; (w / "jump7" / "player").mkdir(parents=True); (w / "jump7" / "player" / "player.gd").write_text("x")
    (w / "b_mod" / "my").mkdir(parents=True); (w / "b_mod" / "override.cfg").write_text("[autoload]\n")
    assert GC.layout(w, TOPS) == [w / "b_mod", w / "jump7"]                 # side by side / selfcheck negmod copies
    out = GC.merged(w, TOPS, tmp_path / "t")
    assert (out / "player" / "player.gd").exists() and (out / "override.cfg").exists()
    assert GC.merged(tmp_path / "none", TOPS, tmp_path / "t2") is None


def test_host_removed_spreads_and_fails():
    """a total-conversion mod frees /root/main: the headless top-level error reaches every asked probe (fail with that
    evidence, not "not probed"), the render menu error too, and both are marked deterministic (no re-run)"""
    a = rec("aa1")
    msg = "host main scene removed by the mod: /root/main was freed at 1.3 s (root: [SkateCompetition, ParkCompetition]; main: none)"
    h = P.spread_error({"error": msg, "_host_removed": True, "_level_loaded_s": 1.3, "_exit": 0}, ["level", "move", "shoot"])
    assert h["level"] == h["move"] == {"error": msg, "host_removed": True}
    res = P.mark_deterministic({**h, "render": {"menu": {"error": msg, "host_removed": True}}})
    assert res["_deterministic"] == ["level", "move", "render", "shoot"]
    assert P.item_result("level", a, res) == (False, msg)
    assert P.item_result("shoot.bullets+fire_rate+bullet_speed", a, res) == (False, msg)
    assert P.item_result("render.menu", a, res) == (False, msg)
    assert P.item_result("enemy", a, res) == (None, "not probed")       # not asked -> unchanged


def test_spread_error_keeps_infra_and_plain_timeouts_apart():
    a = rec("aa1")
    nout = {"error": "no probe output (game crashed or hung)", "_no_output": True}
    assert "level" not in P.spread_error(dict(nout), ["level"])          # crash / hang: still could-not-test
    msg = "level did not load in 90 s (root: [x]; main: [y])"             # seen on vanilla too: could-not-test, with the reason
    t = P.mark_deterministic(P.spread_error({"error": msg, "_exit": 132}, ["level", "shoot"]))
    assert t["level"] == {"not_tested": msg} and "_deterministic" not in t
    assert P.item_result("level", a, t) == (None, msg)
    assert P.item_result("shoot.bullets+fire_rate+bullet_speed", a, t) == (None, msg)
    ok = P.spread_error({"level": {"grid": {}}}, ["level"])
    assert ok == {"level": {"grid": {}}} and "_deterministic" not in P.mark_deterministic(ok)


def test_probe_script_guards():
    s = (gbase.gdir("godot-tps") / "gt_probe.gd").read_text()
    assert "func _host_gone()" in s and "func _alive(" in s and "func _scene_summary()" in s
    assert s.count("if await _reload_level() == null:") == s.count("await _reload_level()") == 8
    menu = s.split("func p_menu():", 1)[1].split("\nfunc ", 1)[0]
    assert menu.count("if not _alive([menu]):") >= 5                     # every await that holds the menu node
    assert "if r == null:" in s                                           # a script-aborted probe reports an error

"""The gate (2026-11-06): build + load, runs stably, the host game is still there. Fakes and saved-session shapes only
(no game, no model): gab/eval/regression.py checks, re-run rule, infra on a broken vanilla baseline, pool.yaml `gate:` keys,
regate of a saved evaluation."""
import importlib, json
from pathlib import Path

import pytest
import yaml

from gab import config
from gab.eval import regression as G
from gab.eval import run as R
from gab.game import base as gbase


def real(game):
    """the game's adapter without a seat: compare() only"""
    A = importlib.import_module(gbase.GAMES[game]).Adapter; a = A.__new__(A); a.seat = None; return a


class FakeTask:
    game = "voxelibre"; id = "T-x"
    hidden = {"instructions_meta": {"explicit_requirements": []}}


VAN = {"player_start": {"game_mode": "survival", "health": 20, "hp_max": 20, "food": 20, "saturation": 5, "breath": 10},
       "place_break": {"place": True, "break": True, "drops": True}, "log_errors": {"errors": 0, "_lines": []}}


class FakeAd:
    """voxelibre-shaped sessions; `mods` = the modded sessions returned in order (the last one repeats)"""
    name = "voxelibre"
    def __init__(self, mods, base=None): self.mods, self.base, self.calls = list(mods), base or VAN, []
    def version(self): return {"build": "test"}
    def run_probes(self, names, mod_dir, label):
        self.calls.append((label, sorted(names)))
        if label == "vanilla": return json.loads(json.dumps(self.base))
        return self.mods.pop(0) if len(self.mods) > 1 else self.mods[0]
    def compare(self, spec, base, got): return real("voxelibre").compare(spec, base, got)


def mod(**kw): return {**json.loads(json.dumps(VAN)), **kw}


@pytest.fixture
def games(tmp_path, monkeypatch): monkeypatch.setenv("GAB_GAMES_DIR", str(tmp_path / "games"))


def test_every_game_has_a_gate():
    """all 14 games: one STABLE (log_errors) item + >= 1 host-present item, all ready (Cities: Skylines too since 2026-11-15)"""
    games = sorted(p.parent.name for p in (config.REPO / "benchmark" / "pools").glob("*/pool.yaml"))
    assert len(games) == 14
    for g in games:
        st, host, ok = G.gate_items(g)
        assert st["probe"] == "log_errors" and 1 <= len(host) <= 3, g
        assert ok, g
        assert "default_picks" not in json.dumps(yaml.safe_load((config.REPO / "benchmark" / "pools" / g / "pool.yaml").read_text())["gate"])


def test_partial_change_passes(games, tmp_path):
    """other numbers than vanilla (health 40, no saturation change ...) still pass: only the stock content must be reached"""
    ad = FakeAd([mod(player_start={**VAN["player_start"], "health": 40, "food": 10}, place_break={"place": True, "break": False, "drops": False})])
    g, _ = G.evaluate(FakeTask(), ad, None, tmp_path / "a")
    assert g["pass"] is True and g["fail"] is None and [c["ok"] for c in g["checks"]] == [True, True, True]
    assert [c[0] for c in ad.calls] == ["vanilla", "mod"]                 # nothing to re-run
    assert json.loads((tmp_path / "a" / "gate_probes.json").read_text())["pass"] is True


def test_host_check_must_fail_twice(games, tmp_path):
    gone = mod(player_start={"error": "no player joined the world"})
    ad = FakeAd([gone, mod()])                                             # fails once, passes on the re-run -> pass
    g, _ = G.evaluate(FakeTask(), ad, None, tmp_path / "a")
    assert g["pass"] is True and [c[0] for c in ad.calls] == ["vanilla", "mod", "mod-rerun"]
    c = {c["id"]: c for c in g["checks"]}["VL-JOIN-STATE"]
    assert c["ok"] is True and c["first_run"]["ok"] is False
    ad = FakeAd([gone, gone])                                              # fails twice, another host check still passes -> pass
    g, _ = G.evaluate(FakeTask(), ad, None, tmp_path / "b0")
    assert g["pass"] is True                                               # host present = ANY host check (2026-11-06)
    gone = mod(player_start={"error": "no player joined the world"}, place_break={"error": "no world"})
    ad = FakeAd([gone, gone])                                              # every host check fails twice -> host_removed
    g, _ = G.evaluate(FakeTask(), ad, None, tmp_path / "b")
    assert g["pass"] is False and g["fail"] == "host_removed" and "no player joined" in g["reason"]
    assert [c[0] for c in ad.calls] == ["mod", "mod-rerun"]                # vanilla baseline came from the cache


def test_broken_vanilla_baseline_is_infra(games, tmp_path):
    """the harness could not run a gate check (vanilla fails too): undecided, not a fail"""
    ad = FakeAd([mod()], base={**VAN, "place_break": {"error": "probe timed out"}})
    g, _ = G.evaluate(FakeTask(), ad, None, tmp_path / "a")
    assert g["pass"] is None and g["fail"] == "infra" and "vanilla baseline" in g["reason"]


def test_stability():
    """a real crash / hang or a new fatal error kind fails; more non-fatal error lines than vanilla do not; a session without
    known crash fields keeps the STABLE item's own verdict"""
    vl = real("voxelibre")
    assert G.stable(vl, "log_errors", VAN, mod(log_errors={"errors": 3, "_lines": ["ERROR: x"]}))[0] is False   # no crash fields: item verdict
    gt = real("godot-tps")
    b = {"_finished": True, "_exit": 0, "log_errors": 0}
    ok, det = G.stable(gt, "log_errors", b, {"_finished": True, "_exit": 0, "log_errors": 4, "_error_lines": ["SCRIPT ERROR: Invalid access @ x.gd:3"]})
    assert ok is True and "tolerated" in det
    ok, det = G.stable(gt, "log_errors", b, {"_finished": False, "_exit": 139, "log_errors": 0})
    assert ok is False and "did not finish" in str(det)
    ok, det = G.stable(gt, "log_errors", b, {"_finished": True, "_exit": 0, "log_errors": 1, "_error_lines": ["FATAL: out of memory in render"]})
    assert ok is False and "new_fatal_errors" in det
    tf = real("tof2")                                                      # new non-fatal script error kind, no crash -> stable
    tb = {"maps": {"_log_errors": 0, "_error_kinds": []}, "log_errors": 0}
    assert G.stable(tf, "log_errors", tb, {"maps": {"_log_errors": 2, "_error_kinds": ["SCRIPT ERROR: Nil @ f (x.gd:1)"]}, "log_errors": 2})[0] is True
    assert G.stable(tf, "log_errors", tb, {"maps": {"_log_errors": 0, "_error_kinds": [], "_crashed": "rc=134 done=False"}, "log_errors": 0})[0] is False
    tx = real("tuxemon")                                                   # a run that did not finish counts as a crash (until TX-STABLE says otherwise)
    xb = {"log_errors": {"errors": 0, "signatures": [], "runs": [], "not_finished": []}}
    assert G.stable(tx, "log_errors", xb, {"log_errors": {"errors": 0, "signatures": [], "runs": [], "not_finished": ["walk"]}})[0] is False
    assert G.stable(gt, "log_errors", {"log_errors": {"error": "no vanilla run"}}, {"_finished": True, "log_errors": 0})[0] is None   # harness
    assert G.stable(gt, "log_errors", {"_finished": False, "log_errors": 0}, {"_finished": False, "log_errors": 0})[0] is None    # vanilla hangs too


def test_host_present_shapes():
    # godot-tps T-VO-vid_4c7cbea650bf.t1: the mod frees /root/main, the headless run never reaches the level
    b = {"level": {"counts": {"MeshInstance3D": 1215}}, "move": {"travel": 9.74}, "_finished": True, "log_errors": 0}
    ok, det = G.present("level", b, {"error": "level did not load in 90 s", "_finished": True, "log_errors": 0})
    assert ok is False and "level did not load" in det
    assert G.present("move", b, {"level": {"counts": {}}, "move": {"travel": 0.5}})[0] is True        # other movement: fine
    # per-map load flags: some stock maps gone passes, none left fails
    xb = {"maps_load": {"afterslime": {"loaded": True}, "solarium": {"loaded": True}}}
    assert G.present("maps_load", xb, {"maps_load": {"afterslime": {"loaded": True}, "solarium": {"loaded": False}}})[0] is True
    assert G.present("maps_load", xb, {"maps_load": {"afterslime": {"loaded": False}, "solarium": {"loaded": False}}})[0] is False
    sb = {"tracks": {"tracks": {"abyss": "ok", "mines": "ok"}}}
    assert G.present("tracks", sb, {"tracks": {"tracks": {"abyss": "failed", "mines": "failed"}}})[0] is False
    assert G.present("render.menu", {"render": {"menu": {"buttons": 4}}}, {"render": {"error": "GL hang"}})[0] is False
    assert G.present("move,swim", {"move": {"a": 1}, "swim": {"b": 1}}, {"move": {"a": 2}, "swim": {"error": "no water"}})[0] is False
    assert G.present("player_start.health+food", {"player_start": {"health": 20, "food": 20}}, {"player_start": {"health": 20}})[0] is False
    assert G.present("level", {"level": {"error": "x"}}, {"level": {"a": 1}})[0] is None
    assert G.decide([{"kind": "stable", "ok": False}, {"kind": "host", "ok": False}]) == (False, "unstable")
    assert G.decide([{"kind": "stable", "ok": True}, {"kind": "host", "ok": None}]) == (None, "infra")
    # 2026-11-06: one host check could not be tested on the MODDED game (vanilla fine) while another reaches stock content -> pass
    assert G.decide([{"kind": "stable", "ok": True}, {"kind": "host", "ok": None, "baseline_broken": False},
                     {"kind": "host", "ok": True}]) == (True, None)
    # no host check reaches stock content on the modded game, vanilla fine -> host_removed, not infra
    assert G.decide([{"kind": "stable", "ok": True}, {"kind": "host", "ok": None, "baseline_broken": False},
                     {"kind": "host", "ok": None, "baseline_broken": False}]) == (False, "host_removed")
    assert G.decide([{"kind": "stable", "ok": True}, {"kind": "host", "ok": False}, {"kind": "host", "ok": True}]) == (True, None)


def test_host_gone_is_host_removed_and_not_rerun(games, tmp_path):
    """the godot-tps probe in the gate: godot-tps `_host_gone` (honest "host main scene removed by the mod") -> host_removed even when
    the run also looks unstable; probes in `_deterministic` are not re-run"""
    class T(FakeTask): game = "godot-tps"
    msg = "host main scene removed by the mod: /root/main was freed at 1.3 s"
    van = {"level": {"grid": {"n": 1763}}, "move": {"d": 3.1}, "log_errors": {"errors": 0}, "_finished": True, "_exit": 0}
    gone = {"level": {"error": msg, "host_removed": True}, "move": {"error": msg, "host_removed": True},
            "log_errors": {"errors": 0}, "_deterministic": ["level", "move"]}
    class Ad(FakeAd):
        name = "godot-tps"; stable_ok = True
        def compare(self, spec, base, got): return (self.stable_ok, "x")
    a = Ad([{**gone, "_finished": True, "_exit": 0}], base=van)
    g, _ = G.evaluate(T(), a, None, tmp_path / "a")
    assert (g["pass"], g["fail"]) == (False, "host_removed")
    assert [c for c, _ in a.calls] == ["vanilla", "mod"]                       # no re-run of a deterministic failure
    assert all(c.get("host_gone") for c in g["checks"] if c["kind"] == "host")
    a = Ad([{**gone, "_finished": False, "_exit": 0}], base=van); a.stable_ok = False   # also unstable: host_removed wins
    g, _ = G.evaluate(T(), a, None, tmp_path / "b")
    assert (g["pass"], g["fail"]) == (False, "host_removed")
    assert a.calls[-1] == ("mod-rerun", ["log_errors"])                        # the stable check is re-run, not level / move
    class Plain(Ad): pass
    a = Plain([{**van, "_finished": False}], base=van); a.stable_ok = False       # a real crash without a host error: unstable
    g, _ = G.evaluate(T(), a, None, tmp_path / "c")
    assert (g["pass"], g["fail"]) == (False, "unstable")


def test_real_adapter_keeps_probe_sessions(games, tmp_path):
    """an adapter with a trace replay() method (every game adapter) is not a regate replay: probes_mod(_rerun).json are written
    and a regate can read the baseline path from gate_probes.json"""
    class Ad(FakeAd):
        def replay(self, inst, tr, out): pass
    a = Ad([mod(player_start={"error": "boom"})])
    g, _ = G.evaluate(FakeTask(), a, None, tmp_path / "r")
    assert (tmp_path / "r" / "probes_mod.json").exists() and (tmp_path / "r" / "probes_mod_rerun.json").exists()
    assert json.loads((tmp_path / "r" / "gate_probes.json").read_text())["baseline"] == g["baseline"]

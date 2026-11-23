"""Tuxemon adapter, offline: deliverable layout / build check, pool ids vs probes, compare on the recorded pool verification
(tests/data/tuxemon: A/A runs aaD / aaE and the three negative mods of 2026-10-30, trimmed: db hashes of the technique and
economy tables + 5 monsters, 30 maps)."""
import json, shutil
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.tuxemon import probes as P
from gab.game.tuxemon.adapter import Adapter

D = Path(__file__).parent / "data" / "tuxemon"
REPO = Path(__file__).resolve().parents[1]
NEG = gbase.gdir("tuxemon") / "negmods"


def rec(n): return json.loads((D / f"{n}.json").read_text())


def items():
    p = yaml.safe_load((REPO / "benchmark" / "pools" / "tuxemon" / "pool.yaml").read_text())
    return p, [i for i in p["always"] + p["pool"] if i["status"] == "ready"]


def fails(base, got):
    return sorted(i["id"] for i in items()[1] if P.compare_item(i["probe"], base, got)[0] is False)


def test_registered():
    assert "tuxemon" in gbase.GAMES and gbase.title("tuxemon") == "Tuxemon"


def test_pool_matches_probes():
    p, its = items()
    names = {x for i in its for x in i["probe"].split(".")[0].split("+")}
    assert names - {"log_errors"} <= set(P.PROBES) | set(P.SUB)
    ids = {i["id"] for i in its}
    assert set(p["default_picks"]) <= ids and p["game"] == "tuxemon"
    neg = yaml.safe_load((NEG / "negmods.yaml").read_text())
    for m, spec in neg.items():
        assert set(spec["expect_fail"]) <= ids and Adapter._layout(NEG / m) == [NEG / m]


# recorded with the probe outputs of probes.REV 2026-11-06 (selfcheck 20261106-091129, trimmed like aaD): A/A aa2A / aa2B and
# the opening negative mods; the 2026-10-30 records (aaD ...) are kept and must still compare as before
BASE = {"neg_opening": "aa2A", "neg_opening_bullet": "aa2A"}


def test_aa_recorded_agree():
    assert fails(rec("aaD"), rec("aaE")) == []
    assert fails(rec("aa2A"), rec("aa2B")) == []


def test_negatives_fail_exactly():
    neg = yaml.safe_load((NEG / "negmods.yaml").read_text())
    for m, spec in neg.items():
        assert fails(rec(BASE.get(m, "aaD")), rec(m)) == sorted(spec["expect_fail"]), m


def test_opening_negatives_ran_from_stock_start():
    g = rec("neg_opening")
    for p in ("walk", "warp", "battle", "menu", "talk", "items", "shop", "capture", "story", "encounter"):
        assert g[p]["_start"]["mode"] == "stock", p
    assert g["save_load"]["save"]["_start"]["mode"] == "stock" and rec("aa2A")["walk"]["_start"]["mode"] == "new_game"
    assert all(isinstance(v, dict) for v in g["walk"]["alt"].values()) and "steps" not in g["walk"]   # bedroom changed: alt only
    assert g["log_errors"]["not_finished"] == [] and "opening_changed" in g["new_game"]


def test_could_not_test():
    b = rec("aaD"); g = dict(b, battle={"error": "no result (crash or timeout)"})
    assert P.compare_item("battle", b, g)[0] is None
    assert P.compare_item("battle", {k: v for k, v in b.items() if k != "battle"}, b)[0] is None


def test_layout_and_build(tmp_path):
    ad = Adapter.__new__(Adapter)
    tpl = gbase.gdir("tuxemon") / "devkit" / "template" / "mod"
    assert ad.build("x", tpl)[0]
    # nested one level (as selfcheck installs a negative mod): mod/<name>/mods/tuxemon/...
    shutil.copytree(NEG / "neg_door", tmp_path / "m" / "neg_door")
    assert Adapter._layout(tmp_path / "m") == [tmp_path / "m" / "neg_door"]
    assert Adapter.mod_maps(Adapter._layout(tmp_path / "m")) == ["spyder_downstairs.tmx"]
    ok, log = ad.build("x", tmp_path / "m"); assert ok, log
    # bad: empty, stray top-level entry, extra data mod, broken yaml / tmx
    (tmp_path / "e").mkdir(); assert not ad.build("x", tmp_path / "e")[0]
    b = tmp_path / "b"; shutil.copytree(tpl, b)
    (b / "stuff").mkdir(); (b / "mods" / "mymod").mkdir()
    (b / "mods" / "tuxemon" / "db" / "item" / "bad.yaml").write_text("a: [1, 2\n")
    (b / "mods" / "tuxemon" / "maps").mkdir(); (b / "mods" / "tuxemon" / "maps" / "x.tmx").write_text("hello")
    ok, log = ad.build("x", b)
    assert not ok and "stuff" in log and "mymod" in log and "bad.yaml" in log and "x.tmx" in log


def test_errors_in_masks_instance():
    root = "/home/player/oss-tuxemon"
    a = P.errors_in(f"[x] tuxemon.map - ERROR - bad event 12 in {root}/runs/vanilla-1030-1/src/m.tmx", root)
    b = P.errors_in(f"[y] tuxemon.map - ERROR - bad event 7 in {root}/runs/mod-1030-2/src/m.tmx", root)
    assert a == b and len(a) == 1


def test_build_state_layout():
    from gab.game.tuxemon.adapter import build_state, ENGINE_KEYS
    raw = {"screen": "DialogState", "states": ["DialogState", "WorldState"], "map": "spyder_bedroom.tmx", "tile": [4, 4], "facing": "DOWN",
           "name": "A", "money": 500, "steps": 1, "bag": {}, "party": [], "vars_total": 9, "npcs_total": 1,
           "texts": ["  GAB   hello  "], "menu": {"state": "ChoiceState", "items": ["", "NO", "YES"], "sel": 1},
           "mod_vars": {f"v{i:02d}": str(i) for i in range(35)}, "mod_npcs": {"keeper": {"name": "k", "x": 5, "y": 4, "facing": "DOWN"}},
           "mod_events": ["GAB"], "mod_items": [], "mod_monsters": []}
    s = build_state(raw, True)
    assert set(s) == {"running", "engine", "mod", "screen_text"} and tuple(s["engine"]) == ENGINE_KEYS
    assert s["engine"]["map"] == "spyder_bedroom.tmx" and s["engine"]["player_name"] == "A" and s["engine"]["tile"] == {"x": 4, "y": 4}
    assert len(s["mod"]["variables"]) == 30 and s["mod"]["variables_more"] == 5 and "keeper" in s["mod"]["npcs"]
    assert s["screen_text"] == ["GAB hello", "menu (ChoiceState): [NO] | YES"]
    off = build_state({}, False)
    assert off["engine"]["screen"] == "not_running" and off["mod"]["variables"] == {} and off["screen_text"] == []


def test_launcher_imports_game_after_display():
    """gabplay.py must not load a game module that binds tuxemon.prepare.DISPLAY_CONTEXT at import time (graphics, map.view;
    pulled in by network / session / client) before the game's pygame_init(): else maps are drawn at scale 1 (16 px tiles on the
    80 px grid). Only run_tuxemon and tuxemon.prepare (neither loads graphics) may be imported at module level."""
    import ast
    from gab.game.tuxemon.adapter import LAUNCHER
    top = []
    for n in ast.parse(LAUNCHER).body:
        if isinstance(n, ast.Import): top += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom): top.append(n.module)
    game = [m for m in top if m and (m == "tuxemon" or m.startswith(("tuxemon.", "run_tuxemon")))]
    assert game and set(game) <= {"run_tuxemon", "tuxemon.prepare"}, game
    assert "_prep.pygame_init = " in LAUNCHER and LAUNCHER.index("_prep.pygame_init = ") < LAUNCHER.index("run_tuxemon.launch_game(")


# ---- 2026-11-06: a mod that changes the new-game opening (stock start), N/A, TX-STABLE counts only real crashes
STOCK = {"mode": "stock", "why": "no name input: {'state': 'WorldState'}", "home": "spyder_downstairs.tmx"}
ROUTE = {"start": [3, 4], "steps": [["up", [3, 3], [3, 2]]], "end": [3, 2], "facing": "UP", "map": "spyder_bedroom.tmx"}
ALT = {"spyder_paper_town.tmx": {"start": [10, 8], "steps": [["left", [9, 8]]], "end": [9, 8], "facing": "LEFT", "map": "spyder_paper_town.tmx"},
       "spyder_paper_rival_bedroom.tmx": {"start": [3, 5], "steps": [["up", [3, 4]]], "end": [3, 4], "facing": "UP",
                                          "map": "spyder_paper_rival_bedroom.tmx"}}


def test_runner_compiles_and_probes_use_start_game():
    import ast, re
    src = P.RUNNER.replace("__GATE_FRAMES__", "600"); ast.parse(src)
    no_game = {"new_game", "campaign_xero", "campaign_waves", "load", "db", "maps", "gate"}   # p_new_game keeps the real opening
    for name in re.findall(r"^def p_(\w+)\(g, shots\):", src, re.M):
        body = src[src.index(f"def p_{name}("):].split("\ndef ", 1)[0]
        if name in no_game: assert "start_game(g)" not in body, name
        else: assert "start_game(g)" in body and "new_game(g)" not in body, name
    assert 'raise OpeningChanged("no name input' in src and "except OpeningChanged" in src


def test_touched_and_crashed():
    assert P.touched_maps(Adapter._layout(NEG / "neg_opening")) == ["spyder_bedroom.yaml"]
    assert P.touched_maps(Adapter._layout(NEG / "neg_door")) == ["spyder_downstairs.tmx"]
    assert P.touched_maps([NEG / "neg_bullet"]) == []
    assert P.crashed({"ok": False, "error": "no result (crash or timeout)"})           # killed by the timeout / segfault
    assert P.crashed({"ok": False, "raised_by": "game"}) and P.crashed({"ok": False, "raised_by": "no result"})
    assert not P.crashed({"ok": False, "raised_by": "probe"}) and not P.crashed({"ok": True})


def test_stable_ignores_probe_script_errors():
    b = {"errors": 0, "signatures": [], "not_finished": []}
    g = {"errors": 0, "signatures": [], "not_finished": [], "probe_failed": ["walk", "warp"]}
    ok, det = P.compare("log_errors", b, g)
    assert ok is True and det["crashed_or_hung"] == [] and det["probe_failed"] == ["walk", "warp"]
    assert P.compare("log_errors", b, dict(g, not_finished=["battle"]))[0] is False       # a real crash still fails


def test_walk_compare_modes():
    base = dict(ROUTE, alt=ALT)
    assert P.compare("walk", base, json.loads(json.dumps(dict(base, _start={"mode": "new_game"}))))[0] is True
    # stock start, bedroom changed by the mod: only the alt routes run, and they must match
    g = {"alt": dict(ALT, **{"spyder_paper_town.tmx": "skipped: the mod changes this map"}), "_start": STOCK}
    ok, det = P.compare("walk", base, g)
    assert ok is True and "spyder_paper_rival_bedroom.tmx" in det and "stock start" in det
    bad = json.loads(json.dumps(g)); bad["alt"]["spyder_paper_rival_bedroom.tmx"]["steps"] = [["up", [3, 5]]]   # walking broke
    assert P.compare("walk", base, bad)[0] is False
    # new_game start: the bedroom route is compared as before, even with the old baseline (no alt)
    assert P.compare("walk", ROUTE, dict(ROUTE, end=[0, 0], alt=ALT))[0] is False
    assert P.compare("walk", ROUTE, dict(ROUTE, alt=ALT))[0] is True
    # stock start and no route in both -> could not test; every map changed -> not applicable
    assert P.compare("walk", ROUTE, {"alt": ALT, "_start": STOCK})[0] is None
    ok, det = P.compare("walk", base, {"not_applicable": "every map changed", "_start": STOCK})
    assert ok is None and det == {"not_applicable": "every map changed"}


def test_warp_compare_stock_legs():
    base = rec("aaD")["warp"]
    arr = base["arrivals"]
    g = {"arrivals": [["skipped", "spyder_bedroom.tmx", "spyder_downstairs.tmx", "x"]] + arr[1:5]
         + [["skipped", "spyder_downstairs.tmx", "spyder_bedroom.tmx", "x"]], "end": "spyder_downstairs.tmx", "_start": STOCK}
    ok, det = P.compare("warp", base, g)
    assert ok is True and "4 of 6" in det
    bad = json.loads(json.dumps(g)); bad["arrivals"][1] = arr[1][:4] + ["spyder_paper_town.tmx", 12, 9, "DOWN"]   # neg_door's door
    assert P.compare("warp", base, bad)[0] is False
    assert P.compare("warp", base, dict(base, _start={"mode": "new_game"}))[0] is True
    assert P.compare("warp", base, dict(base, arrivals=bad["arrivals"][:1] + arr[1:]))[0] is False   # new_game: exact as before


def test_save_compare_stock_vars():
    base = rec("aaD")["save_load"]
    def with_vars(extra=None, drop=None, start=None):
        g = json.loads(json.dumps(base))
        for part in (g["save"]["saved"], g["load"]):
            part["vars"] = {k: v for k, v in part["vars"].items() if k != drop} | (extra or {})
        if start: g["save"]["_start"] = start
        return g
    assert P.compare("save_load", base, with_vars({"rugby_story_started": "yes"}, start=STOCK))[0] is True    # the mod's own variable
    assert P.compare("save_load", base, with_vars(drop="intro_scoop", start=STOCK))[0] is False             # a vanilla one lost
    assert P.compare("save_load", base, with_vars({"rugby_story_started": "yes"}))[0] is False               # new_game: exact as before
    g = with_vars(start=STOCK); g["load"]["info"]["money"] = 1                                               # load broke
    assert P.compare("save_load", base, g)[0] is False


def test_not_applicable_is_excluded(tmp_path, monkeypatch):
    """gate (evaluation merge): a not-applicable gate item is excluded, neither a pass nor a fail; a probe error still fails"""
    from gab.eval import regression
    monkeypatch.setenv("GAB_GAMES_DIR", str(tmp_path / "games"))
    class T: game = "tuxemon"; id = "T-x"
    LE = {"errors": 0, "signatures": [], "not_finished": []}
    WARP = {"arrivals": [["spyder_house.tmx", [3, 4], "down"]]}
    class Ad:
        name = "tuxemon"; probe_rev = P.REV
        def __init__(self, warp): self.warp = warp
        def version(self): return {"build": "test"}
        def run_probes(self, names, mod_dir, label):
            if label == "vanilla": return {"walk": ROUTE, "warp": WARP, "log_errors": LE}
            return {"walk": {"not_applicable": "every map changed"}, "warp": self.warp, "log_errors": LE}
        def compare(self, spec, base, got): return P.compare_item(spec, base, got)
    g, _ = regression.evaluate(T(), Ad(WARP), None, tmp_path / "a")
    by = {c["id"]: c for c in g["checks"]}
    assert by["TX-MOVE"]["excluded"] and by["TX-MOVE"]["ok"] is None
    assert not by["TX-WARP"].get("excluded") and by["TX-WARP"]["ok"] is True
    assert g["pass"] is True and g["fail"] is None                                   # n/a is not a fail, not infra
    g, _ = regression.evaluate(T(), Ad({"error": "boom"}), None, tmp_path / "b")
    assert g["pass"] is False and g["fail"] == "host_removed"                          # a probe error still fails


def test_baseline_key_has_probe_rev(tmp_path, monkeypatch):
    from gab.eval import regression as R
    monkeypatch.setenv("GAB_GAMES_DIR", str(tmp_path)); monkeypatch.setenv("GAB_HOST", "docker")
    class Ad:
        name = "g"; probe_rev = None
        def version(self): return {"build": "1.0"}
        def run_probes(self, names, mod, label): return {"p": {"v": 1}}
    a = Ad(); _, f0 = R.baseline(a, ["p"])
    a.probe_rev = "r2"; _, f1 = R.baseline(a, ["p"])
    assert f0 != f1 and Adapter.probe_rev == P.REV

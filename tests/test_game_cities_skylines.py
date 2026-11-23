"""Cities: Skylines adapter without the game (registered; the in-game checks are what
`gab selfcheck cities_skylines` must verify). What these tests cover:
- pool <-> probes: every pool item's probe is run by the groups groups_of() picks (dependencies included), negmods and default picks
  name real items;
- compare() / item_result() on SYNTHETIC sessions (made up here in the shape CSProbe writes; no recorded Cities session exists yet):
  an A/A pair with jitter inside the tolerances agrees, each negative mod's intended effect fails exactly its expected items;
- the deliverable structure check, state shaping, start.cfg, the settings files (cgs.py: Mono's string hash checked against a key
  the game itself wrote, 2026-10-16), the runners' syntax;
- the shell plumbing end to end against a FAKE game binary (tests/data/cities_skylines/fake_cities.c, compiled here when gcc is
  available): install / provision / install_mod / start / state / stop through gcs.sh and run_probes through csp.sh, on the local
  host backend in a temp home.
Not covered (needs the game): the C# bridge mods compiling and behaving, every probe's numbers, the tolerances, the negative mods'
real effects, the legal-dialog behaviour, timings."""
import copy, json, os, shutil, subprocess, time, uuid
from pathlib import Path

import pytest
import yaml

from gab.game import base as gbase
from gab.game.cities_skylines import adapter as A, probes as P
from gab.host import base as hbase
from local_backend import Backend as LocalBackend

G = gbase.gdir("cities_skylines")
REPO = Path(__file__).parents[1]
POOL = yaml.safe_load((REPO / "benchmark" / "pools" / "cities_skylines" / "pool.yaml").read_text())
ITEMS = POOL["always"] + POOL["pool"]
NEG = yaml.safe_load((G / "negmods" / "negmods.yaml").read_text())


def cgs():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cgs_under_test", G / "bridge" / "cgs.py"); m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m); return m


# ------------------------------------------------------------------ registration, pool, probes
def test_registered():
    assert "cities_skylines" in gbase.GAMES and gbase.title("cities_skylines") == "Cities: Skylines"
    for f in ("adapter.py", "probes.py", "devkit/README.md", "devkit/template/mod/GabTemplate/GabTemplate.csproj", "devkit/template/mod/GabTemplate/Mod.cs",
              "devkit/template/mod/start.yaml", "bridge/CSProbe/CSProbe.cs", "bridge/GABState/GABState.cs", "bridge/csp.sh", "bridge/gcs.sh",
              "bridge/cs-start.sh", "bridge/cgs.py", "statecheck/statecheck.yaml", "statecheck/testmod/GabStateTest/GabStateTest.csproj"):
        assert (G / f).exists(), f
    assert not (G / "pending_adapter.py").exists()
    req = A.Adapter.requirements(A.Adapter.__new__(A.Adapter))
    assert req["steam"] is True and set(req["hosts"]) == {"pool", "docker"} and req["gpu"] and "own_copy" in req and req["eula"]


def test_pool_items_and_status():
    ids = {i["id"] for i in ITEMS}
    assert POOL["game"] == "cities_skylines" and set(POOL["default_picks"]) <= ids and 4 <= len(POOL["default_picks"]) <= 6
    # nothing is verified in game yet: every item stays `unverified` until `gab selfcheck` passes it (then flip it to ready here)
    assert {i["status"] for i in ITEMS} <= {"ready", "unverified"}
    assert {"CS-DAYNIGHT", "CS-TRANSPORT"} <= ids
    for i in ITEMS:
        asked = set(P.groups_of([i["probe"].split(".")[0]]))
        used = {P.ALIAS.get(p.strip().split(".")[0], p.strip().split(".")[0]) for p in i["probe"].split(",")} - {"log_errors"}
        assert used <= asked, (i["id"], used, asked)


def test_groups_of_dependencies():
    assert P.groups_of(["perf"]) == ["sim"]
    assert P.groups_of(["saveload"]) == ["roads", "zoning", "services", "growth", "save"]
    assert P.groups_of(["services,growth", "log_errors"]) == ["roads", "zoning", "services", "growth"]
    assert P.groups_of(["transport"]) == ["transport"]                  # runs on the probe-city fixture, no regrowth
    assert P.groups_of(["fixture", "transport,growth.population"]) == ["roads", "zoning", "services", "growth", "transport"]
    assert P.FIXTURE_MAKE == ["roads", "zoning", "services", "growth", "fixture"] and set(P.FIXTURE_GROUPS) <= set(P.GROUPS)
    assert P.groups_of(["daynight", "content", "nonsense"]) == ["content", "daynight"]
    order = (G / "bridge" / "CSProbe" / "CSProbe.cs").read_text()
    assert 'new[] { "' + '", "'.join(P.GROUPS) + '" }' in order         # the session order is CSProbe's order


def test_negmods_are_buildable_mods(tmp_path):
    ids = {i["id"] for i in ITEMS}
    ad = A.Adapter.__new__(A.Adapter)
    for m, spec in NEG.items():
        assert set(spec["expect_fail"]) <= ids and P.groups_of(spec["quick_probes"]), m
        mod = tmp_path / m / "mod"; shutil.copytree(G / "negmods" / m, mod / m)
        assert ad.check_layout(mod) == [], m
    tm = tmp_path / "state" / "mod"; shutil.copytree(G / "statecheck" / "testmod", tm)
    assert ad.check_layout(tm) == []
    assert ad.check_layout(G / "devkit" / "template" / "mod") == []


# ------------------------------------------------------------------ compare on synthetic sessions
def synthetic():
    """SYNTHETIC session (made up for these tests, NOT recorded from the game): the fields CSProbe writes per group, plausible values"""
    return {
        "content": {"buildings": {"Wind Turbine": "a1b2", "Water Tower": "c3d4", "Bus Depot": "e5f6"}, "nets": {"Basic Road": "1111", "Medium Road": "2222"},
                    "vehicles": {"Bus": "3333", "Sedan": "4444"}, "n_buildings": 3, "n_nets": 2, "n_vehicles": 2, "n_citizens": 40,
                    "tax_res_low": 9, "tax_com_low": 9, "tax_ind": 9, "start_cash": 70000},
        "sim": {"speed1_fps": 31.2, "speed1_game_hours_per_s": 0.6, "speed1_game_s_per_frame": 8.79, "speed2_fps": 60.4, "speed2_game_hours_per_s": 1.2,
                "speed2_game_s_per_frame": 8.79, "speed3_fps": 118.0, "speed3_game_hours_per_s": 2.4, "speed3_game_s_per_frame": 8.79,
                "paused_frames_3s": 0, "ratio_3_to_1": 3.8, "ratio_2_to_1": 1.9},
        "camera": {"pos_err": 0.4, "size_err": 0.2, "angle_err": 0.1, "cam_height_above_ground": 310.0, "free_camera_hides_ui": True, "ui_back": True},
        "infoviews": {"opened": ["Electricity", "Water", "Garbage", "Traffic", "Density"], "failed": []},
        "daynight": {"enabled": True, "daytime_frames": 65536, "hours_per_frame": 0.000366, "noon_hour": 12.0, "noon_night": False, "noon_sun": 4.2,
                     "noon_ambient": 0.92, "midnight_hour": 1.0, "midnight_night": True, "midnight_sun": 0.55, "midnight_ambient": 0.21},
        "roads": {"highway_end": True, "highway_end_pos": [12, -840], "results": ["ok"] * 12, "segments_added": 13, "nodes_added": 13, "cost": 9600,
                  "zone_blocks_created": 52, "stub": "ok", "upgrade": "ok", "upgraded_to": "Medium Road", "bulldozed": 1},
        "zoning": {"res_cells": 160, "com_cells": 96, "ind_cells": 160, "demand_res": 80, "demand_com": 40, "demand_work": 60},
        "services": {"placed": ["Wind Turbine: ok", "Wind Turbine: ok", "Water Tower: ok", "Landfill Site: ok"], "networks": ["ok"] * 7},
        "growth": {"game_days": 42.1, "wall_s": 300, "population": 820, "households": 300, "zoned_buildings": 60, "res_buildings": 30, "com_buildings": 12,
                   "ind_buildings": 18, "no_power": 0, "no_water": 1, "no_road": 0, "abandoned": 0, "power_capacity": 16000, "power_use": 9000,
                   "water_capacity": 120000, "water_use": 60000, "garbage_capacity": 400000, "max_vehicles": 140, "max_citizen_instances": 420,
                   "cash_change": -21000, "income_acc": 3200, "samples": [[1.0, 0], [20.0, 300]]},
        "transport": {"depot": "ok", "depot_try": 1, "line": "stops 4", "stops": 4, "complete": True, "line_vehicles": 3, "buses_peak": 3,
                      "passengers": 41, "bus_capacity": 90, "bus_capacity_per_bus": 30, "game_days": 12.1},
        "save": {"saved": True, "segments": 61},
        "saveload": {"segments_same": True, "zoned_same": True, "population_same": True, "cash_diff": True, "city": "GAProbe"},
        "_fixture": "0123456789abcdef", "alive_at_end": True, "log_errors": 0, "_runs": [{"step": "run1", "done": True}, {"step": "run2", "done": True}],
    }


def fails(base, got): return sorted(i["id"] for i in ITEMS if P.item_result(i["probe"], base, got)[0] is False)
def cnt(base, got): return sorted(i["id"] for i in ITEMS if P.item_result(i["probe"], base, got)[0] is None)


def jittered(s):
    """a second vanilla session: the run-to-run spread the tolerances must absorb (guessed)"""
    b = copy.deepcopy(s)
    b["sim"].update(speed1_fps=28.0, speed2_fps=55.1, speed3_fps=101.3, ratio_3_to_1=3.6, ratio_2_to_1=2.0)
    b["camera"].update(pos_err=2.1, cam_height_above_ground=290.0)
    b["roads"]["cost"] = 9620; b["roads"]["highway_end_pos"] = [13, -841]
    b["zoning"].update(res_cells=158, demand_res=70)
    b["growth"].update(game_days=42.3, wall_s=330, population=700, households=260, zoned_buildings=55, max_vehicles=120, cash_change=-25000, income_acc=2500,
                       samples=[[1.0, 0]])
    b["transport"].update(passengers=36, buses_peak=2, line_vehicles=3, bus_capacity=60, game_days=12.4)
    b["daynight"]["hours_per_frame"] = 0.000378           # +3.4%: the spread NegWind showed in selfcheck 20261115-072225
    b["daynight"].update(noon_sun=4.0, midnight_ambient=0.2)
    return b


def test_synthetic_aa_agrees():
    a = synthetic(); b = jittered(a)
    assert fails(a, b) == [] and fails(b, a) == [] and cnt(a, b) == []


def test_synthetic_negatives_fail_their_items():
    a = synthetic()
    effects = {      # what each negative mod is written to do, as CSProbe would see it
        "NegRoadCost": lambda s: (s["roads"].update(cost=28800), s["content"]["nets"].update({"Basic Road": "9999"})),
        "NegWind": lambda s: s["growth"].update(power_capacity=0, no_power=55),
        "NegNight": lambda s: s["daynight"].update(enabled=False, midnight_night=False, midnight_sun=4.2, midnight_ambient=0.92),
        "NegBus": lambda s: s["transport"].update(passengers=0, bus_capacity=0, bus_capacity_per_bus=0),
    }
    assert set(effects) == set(NEG)
    for m, f in effects.items():
        g = jittered(a); f(g)
        assert fails(a, g) == sorted(NEG[m]["expect_fail"]), m


def test_missing_crash_and_unmeasured_baseline():
    a = synthetic()
    assert P.item_result("perf", a, {"sim": {"error": "no result"}})[0] is False
    assert P.item_result("perf", a, {})[0] is None
    assert P.item_result("perf", a, {"sim": {**a["sim"], "speed3_fps": 60.0}})[0] is False
    assert P.item_result("log_errors", a, {**a, "alive_at_end": False})[0] is False
    assert P.item_result("log_errors", a, {**a, "log_errors": 3})[0] is False
    assert P.item_result("saveload", a, {**a, "saveload": {**a["saveload"], "population_same": False}})[0] is False
    # a vanilla run that measured nothing cannot score: could not test
    nob = {**a, "transport": {**a["transport"], "buses_peak": 0, "passengers": 0}}
    assert P.item_result("transport", nob, a)[0] is None
    assert P.item_result("daynight", {**a, "daynight": {**a["daynight"], "hours_per_frame": 0}}, a)[0] is None
    assert P.item_result("infoviews", a, {**a, "infoviews": {"opened": ["Water"]}})[0] is False
    # nobody boarded in the baseline (an empty city): the passenger measure cannot test, the seats per bus still can (NegBus)
    empty = {**a, "transport": {**a["transport"], "passengers": 0}}
    r = P.item_result("transport", empty, {**empty, "transport": {**empty["transport"], "buses_peak": 2}})
    assert r[0] is None and "no passenger boarded" in r[1]
    assert P.item_result("transport", empty, {**empty, "transport": {**empty["transport"], "bus_capacity": 0, "bus_capacity_per_bus": 0}})[0] is False
    assert P.item_result("transport", empty, {**empty, "transport": {**empty["transport"], "buses_peak": 0}})[0] is False
    assert P.item_result("transport", a, {**a, "transport": {**a["transport"], "passengers": 8}})[0] is False     # 41 -> 8: beyond (0.5, 2)
    # transport ran on another probe-city fixture than the baseline: not comparable
    r = P.item_result("transport", a, {**a, "_fixture": "fedcba9876543210"})
    assert r[0] is None and "fixtures" in r[1]
    assert P.item_result("transport", a, {**a, "_fixture": "fedcba9876543210", "transport": {"error": "no probe-city fixture"}})[0] is False


# ------------------------------------------------------------------ deliverable, state, settings files
def test_layout_checks(tmp_path):
    ad = A.Adapter.__new__(A.Adapter)
    m = tmp_path / "mod"; m.mkdir()
    assert any("no Cities mod" in p for p in ad.check_layout(m))
    p = m / "Metro"; p.mkdir(); (p / "Metro.csproj").write_text("<Project/>"); (p / "Mod.cs").write_text("class M : IUserMod {}")
    assert ad.check_layout(m) == []
    q = m / "Src"; (q / "Source").mkdir(parents=True); (q / "Source" / "a.cs").write_text("class X {}")
    (m / "GABState").mkdir(); (m / "GABState" / "x.dll").write_bytes(b"MZ")
    w = m / "Wrong"; w.mkdir(); (w / "Other.csproj").write_text("<Project/>"); (w / "Mod.cs").write_text("IUserMod")
    (m / "start.yaml").write_text("mode: x\nmap: A\nsave: B\nfoo: 1\ncity: 'a|b'\n")
    L = "\n".join(ad.check_layout(m))
    for s in ("Src/: no IUserMod", "GABState: name taken", "mode must be game or menu", "not both", "unknown key foo",
              "Wrong/: the project must be named Wrong.csproj", "save B is not mod/saves/B.crp", "city must be"):
        assert s in L, s


def test_layout_parts(tmp_path):
    m = tmp_path / "mod"; m.mkdir()
    p = m / "Metro"; p.mkdir(); (p / "Metro.csproj").write_text("<Project/>"); (p / "Mod.cs").write_text("IUserMod")
    b = m / "Built"; b.mkdir(); (b / "Built.dll").write_bytes(b"MZ")
    for s in ("assets", "maps", "saves"): (m / s).mkdir(); (m / s / f"{s}1.crp").write_bytes(b"x")
    (m / "start.yaml").write_text("save: saves1\n")
    projs, ready, assets, maps, saves, st = A.Adapter._layout(m)
    assert [x.name for x in projs] == ["Metro"] and [x.name for x in ready] == ["Built"]
    assert [a.name for a in assets] == ["assets1.crp"] and [a.name for a in maps] == ["maps1.crp"] and [a.name for a in saves] == ["saves1.crp"]
    assert st == {"save": "saves1"} and A.Adapter.check_layout(A.Adapter.__new__(A.Adapter), m) == []


def test_start_cfg_line():
    assert A.start_cfg_line({}) == "new:Green Plains|GAB City"
    assert A.start_cfg_line({"map": "Black Woods", "city": "Ville"}) == "new:Black Woods|Ville"
    assert A.start_cfg_line({"save": "Mine"}) == "load:Mine"
    assert A.start_cfg_line({"mode": "menu", "map": "X"}) is None


def test_shape_state():
    st = A.shape_state({"running": True, "engine": {"status": "in_game", "money": 70000.0, "time_of_day": {"hour": 13.5, "night": False}},
                        "mod": {"districts": {"GAB District": {"population": 0}}}, "screen_text": ["chirp: a: b"] * 40})
    assert list(st) == ["running", "engine", "mod", "screen_text"] and list(st["engine"]) == A.ENGINE_KEYS
    assert st["engine"]["mods"] == [] and len(st["screen_text"]) == 30 and st["engine"]["time_of_day"]["hour"] == 13.5
    assert {"time_of_day", "transport"} <= set(A.ENGINE_KEYS)
    st = A.shape_state({"running": True, "error": "no answer", "blocked": "DIALOG LegalDisclaimerPanel is showing"})
    assert st["engine"]["status"] == "starting" and st["blocked"].startswith("DIALOG")
    # the engine keys GABState writes are the adapter's
    src = (G / "bridge" / "GABState" / "GABState.cs").read_text()
    for k in A.ENGINE_KEYS: assert f'"{k}", ' in src, k


def test_error_lines():
    log = ("IsAppDlcInstalled returned failure code 12\nNullReferenceException: Object reference not set\n  at Metro.Mod.OnLevelLoaded ()\n"
           "NullReferenceException: Object reference not set\n[CSProbe] Exception in probe\nSteam Exception: whatever\n"
           "Compilation failed: 2 error(s)\n")
    assert A.error_lines(log) == ["NullReferenceException: Object reference not set (x2)", "Compilation failed: 2 error(s)"]


def test_cgs_hash_and_files(tmp_path):
    c = cgs()
    # recorded 2026-10-16: the game itself wrote this key for its own mod folder (WCHello)
    assert c.mod_key("/home/player/.local/share/Colossal Order/Cities_Skylines/Addons/Mods/WCHello") == "WCHello595075863.enabled"
    f = tmp_path / "userGameState.cgs"
    keys = c.enable_mods(f, ["/x/Mods/GABState", "/x/Mods/Metro/"])
    d = c.read(f)
    assert d["_version"] == 4 and all(d["bool"][k] is True for k in keys) and keys[1].startswith("Metro")
    d["string"]["name"] = "Ville é"; d["int"]["n"] = -3; d["float"]["f"] = 0.5; d["long"]["l"] = 2 ** 40; d["inputkey"]["k"] = 7
    c.write(f, d); assert c.read(f) == d
    with pytest.raises(ValueError): (tmp_path / "bad.cgs").write_bytes(b"NOPE"); c.read(tmp_path / "bad.cgs")


def test_runners_parse():
    for f in ("csp.sh", "gcs.sh", "cs-start.sh"):
        assert subprocess.run(["bash", "-n", str(G / "bridge" / f)]).returncode == 0, f
    t = (G / "bridge" / "csp.sh").read_text()
    assert "/scripts/cities" not in t and "cp $GD/colossal.cgs" in t and "legal." not in t     # no legal key is written by gab
    assert "TMPDIR" in (G / "bridge" / "cs-start.sh").read_text()
    for f in G.rglob("*"):
        if f.is_file() and f.suffix in (".py", ".sh", ".cs", ".yaml", ".md"):
            assert "legal.privacy.privacy-policy" not in f.read_text(errors="replace") or f.name == "adapter.py", f


# ------------------------------------------------------------------ the shell plumbing against a fake game binary
needs_gcc = pytest.mark.skipif(not shutil.which("gcc"), reason="gcc compiles the fake Cities.x64")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """a temp seat home on the local backend with a FAKE Cities install (Cities.x64 = tests/data/cities_skylines/fake_cities.c, the
    Legal Disclaimer marked accepted in its colossal.cgs) and the harness DLLs pre-placed (no dotnet here) -> (adapter, home)"""
    home = tmp_path / "home"; gd = home / A.STEAM_DIRS[0]; (gd / "Cities_Data" / "Managed").mkdir(parents=True)
    r = subprocess.run(["gcc", "-O1", "-o", str(gd / "Cities.x64"), str(REPO / "tests/data/cities_skylines/fake_cities.c")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    c = cgs()
    c.write(gd / "colossal.cgs", {"_version": 4, **{k: {} for k in c.KINDS}, "bool": {"legal.privacy.test-doc": True, "legal.terms-of-use.test-doc": True}})
    (gd.parent.parent / "appmanifest_255710.acf").write_text('"AppState"\n{\n\t"buildid"\t\t"424242"\n}\n')
    seat = hbase.Seat(LocalBackend(), None, "7", ":199", "", home=str(home))
    ad = A.Adapter(seat)
    for name, out in (("CSProbe", Path(ad.root) / "CSProbe.dll"), ("GABState", Path(ad.root) / "gab/_harness/GABState.dll")):
        out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"MZ fake"); Path(str(out) + ".src").write_text(A.tree_hash([G / "bridge" / name]))
    for mod in (A.time, P.time): monkeypatch.setattr(mod, "sleep", lambda s, _real=time.sleep: _real(min(s, 0.5)))
    made = [q for q in (Path("/tmp/cslib"), Path("/tmp/gabstate/stock_prefabs.txt"), Path("/tmp/gabstate")) if not q.exists()]
    yield ad, home
    for inst in Path(ad.root, "gab").glob("*"):
        if inst.is_dir(): subprocess.run(["bash", f"{ad.root}/bin/gcs.sh", "stop", str(inst)], env={**os.environ, "CSP_ROOT": ad.root}, capture_output=True)
    for q in Path("/tmp/gabstate").glob("fk-*") if Path("/tmp/gabstate").exists() else []: shutil.rmtree(q, ignore_errors=True)
    for q in made:                     # what cs-start.sh / the fake left in /tmp on this host (the sandbox's /tmp in real use)
        if q.is_dir() and not q.is_symlink(): shutil.rmtree(q, ignore_errors=True)
        elif q.exists(): q.unlink()


@needs_gcc
def test_fake_install_checks(fake, tmp_path):
    ad, home = fake
    assert ad.install() and ad.gd.endswith(A.STEAM_DIRS[0]) and ad.version()["build"] == "Cities: Skylines 1 (Steam build 424242)"
    assert (Path(ad.root) / "bin" / "gcs.sh").read_text() == (G / "bridge" / "gcs.sh").read_text()
    c = cgs(); gd = Path(ad.gd)
    d = c.read(gd / "colossal.cgs"); d["bool"] = {"legal.privacy.test-doc": True}; c.write(gd / "colossal.cgs", d)
    with pytest.raises(RuntimeError, match="Legal Disclaimer"): A.Adapter(ad.seat).install()
    shutil.rmtree(gd)
    with pytest.raises(RuntimeError, match="not installed"): A.Adapter(ad.seat).install()


@needs_gcc
def test_fake_session(fake, tmp_path):
    ad, home = fake; inst = f"fk-{uuid.uuid4().hex[:8]}"; c = cgs()
    r = ad.provision(inst)
    U = Path(ad.udir(inst)); d = Path(ad.idir(inst))
    assert r["display"] == ":165" and (U / "Addons/Mods/GABState/GABState.dll").exists() and (d / "data/gabstate").is_symlink()
    assert (d / "game/Cities.x64").is_file() and not (d / "game/Cities.x64").is_symlink() and (d / "game/Cities_Data").is_symlink()
    assert c.read(d / "game/colossal.cgs")["bool"]["TelemetryConsent"] is False
    assert c.read(U / "userGameState.cgs")["bool"][c.mod_key(str(U / "Addons/Mods/GABState"))] is True
    # a vanilla start: in the city, the stock list recorded once
    ok, log = ad.start(inst)
    assert ok, log
    assert "GAB City" in log and (Path(ad.root) / "stock_prefabs.txt").read_text().startswith("b:")
    st = ad.state(inst)
    assert st["running"] and st["engine"]["status"] == "in_game" and st["engine"]["map"] == "Green Plains" and st["mod"] == {}
    ad.stop(inst)
    assert not ad._alive(inst)
    # the deliverable: a built mod folder + an asset + start.yaml; the mod switched on, the start city named by start.yaml
    m = tmp_path / "mod"; (m / "Metro").mkdir(parents=True); (m / "Metro" / "Metro.dll").write_bytes(b"MZ")
    (m / "assets").mkdir(); (m / "assets" / "Arch.crp").write_bytes(b"crp"); (m / "start.yaml").write_text("city: Test Town\n")
    assert ad.build(inst, m)[0]
    info = ad.install_mod(inst, m)
    assert info["mods"] == ["Metro"] and (U / "Addons/Mods/Metro/Metro.dll").exists() and (U / "Addons/Assets/Arch.crp").exists()
    assert c.read(U / "userGameState.cgs")["bool"][c.mod_key(str(U / "Addons/Mods/Metro"))] is True
    ok, log = ad.load_check(inst)
    assert ok and "Test Town" in log and "Metro" in log, log
    assert not ad._alive(inst)
    # the legal dialog: GABState refuses to start; start() says why at once (not after its 600 s timeout)
    (m / "start.yaml").write_text("map: Blocked Map\n"); ad.install_mod(inst, m)
    t0 = time.time(); ok, log = ad.start(inst); ad.stop(inst)
    assert not ok and "LegalDisclaimerPanel" in log and time.time() - t0 < 60


@needs_gcc
def test_fake_probe_session(fake, tmp_path):
    ad, home = fake
    neg = tmp_path / "neg"; shutil.copytree(G / "negmods" / "NegWind", neg / "NegWind")
    (neg / "Prebuilt").mkdir(); (neg / "Prebuilt" / "Prebuilt.dll").write_bytes(b"MZ")
    shutil.rmtree(neg / "NegWind")                                   # no dotnet here: only the prebuilt folder goes in
    got = ad.run_probes(["perf", "saveload", "log_errors"], neg, label=f"fk{uuid.uuid4().hex[:6]}")
    assert got["alive_at_end"] and got["log_errors"] == 0 and [r["step"] for r in got["_runs"]] == ["run1", "run2"], got
    for g in ("sim", "roads", "zoning", "services", "growth", "save"): assert got[g] == {"fake": 1}, g
    assert got["saveload"]["segments_same"] is True and "daynight" not in got and got["_entered"]["map"] == "Green Plains"
    assert any(m.endswith("/Prebuilt") for m in got["mods"])
    inst = Path(got["_instance"])
    U = inst / "data/Colossal Order/Cities_Skylines"
    assert (U / "Addons/Mods/Prebuilt/Prebuilt.dll").exists() and (U / "Addons/Mods/CSProbe/CSProbe.dll").exists()
    keys = cgs().read(U / "userGameState.cgs")["bool"]
    assert keys[cgs().mod_key(str(U / "Addons/Mods/CSProbe"))] and keys[cgs().mod_key(str(U / "Addons/Mods/Prebuilt"))]


@needs_gcc
def test_fake_fixture_once(fake, tmp_path, monkeypatch):
    """CS-TRANSPORT alone: the probe city is grown once (vanilla fixture session), cached in the sandbox and on the host, and every
    later session (vanilla or modded, a new container) only loads it (runF); the session carries the fixture's hash"""
    ad, home = fake
    monkeypatch.setenv("GAB_GAMES_DIR", str(tmp_path / "games"))
    a = ad.run_probes(["transport", "log_errors"], None, label="fxA")
    assert a["transport"] == {"fake": 1} and a["_fixture"] and [r["step"] for r in a["_runs"]] == ["runF"], a
    assert a["_fixture_city"] == {"population": 500} and "roads" not in a
    key = ad.fixture_key(); host = tmp_path / "games" / "cities_skylines" / "fixtures" / key
    assert key.startswith("424242-") and (host / "fixture.json").exists() and (host / "gafixture.crp").exists() and (host / "fixture.cfg").exists()
    made = [q for q in (Path(ad.root) / "gab").glob(f"probe-fixture-{key[:24]}-*") if not q.name.endswith("-mods")]   # per-container suffix
    assert len(made) == 1; made = made[0]
    # a new container: no sandbox cache -> the host cache is copied in, nothing is regrown
    shutil.rmtree(Path(ad.root) / "fixtures"); shutil.rmtree(made)
    m = tmp_path / "mod"; (m / "Prebuilt").mkdir(parents=True); (m / "Prebuilt" / "Prebuilt.dll").write_bytes(b"MZ")
    b = A.Adapter(ad.seat).run_probes(["transport"], m, label="fxB")
    assert b["_fixture"] == a["_fixture"] and b["transport"] == {"fake": 1} and not made.exists()
    # growth picked too: run1 grows a city anyway, transport runs in it (no fixture, one game start)
    c = ad.run_probes(["transport", "growth.population"], None, label="fxC")
    assert [r["step"] for r in c["_runs"]] == ["run1"] and c["transport"] == {"fake": 1} and "_fixture" not in c


def test_level_load_uses_data_asset():
    """GABState / CSProbe pass the metadata's assetRef (the simulation data) to LoadLevel, as NewGamePanel / LoadPanel do; the
    metadata asset itself fails to deserialize ("File format version not supported (5459297 > 121034)", 2026-11-12)"""
    import re
    from pathlib import Path
    b = Path(__file__).resolve().parents[1] / "gab" / "game" / "cities_skylines" / "bridge"
    for f in (b / "GABState" / "GABState.cs", b / "CSProbe" / "CSProbe.cs"):
        calls = re.findall(r"LoadLevel\(([^,]+),", f.read_text())
        assert len(calls) == 2 and all("assetRef" in c for c in calls), (f.name, calls)

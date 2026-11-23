"""SuperTuxKart adapter without a sandbox: pool <-> probes, compare() on the recorded pool verification (2026-10-30: two
vanilla sessions merged from the headless / ui / battle / fps runs, and the six negative mods), the deliverable structure
check, start.yaml -> command line."""
import json
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.supertuxkart import adapter as A, probes as P

D = Path(__file__).parent / "data" / "supertuxkart"
POOL = yaml.safe_load((Path(__file__).parents[1] / "benchmark" / "pools" / "supertuxkart" / "pool.yaml").read_text())
ITEMS = POOL["always"] + POOL["pool"]
READY = [i for i in ITEMS if i["status"] == "ready"]
NEG = yaml.safe_load((gbase.gdir("supertuxkart") / "negmods" / "negmods.yaml").read_text())


def rec(n): return json.loads((D / f"{n}.json").read_text())


def fails(base, got): return sorted(i["id"] for i in READY if P.item_result(i["probe"], base, got)[0] is False)


def test_registered():
    assert "supertuxkart" in gbase.GAMES and gbase.title("supertuxkart") == "SuperTuxKart"
    assert (gbase.gdir("supertuxkart") / "devkit" / "README.md").exists()


def test_pool_probes_exist():
    names = set(P.STKProbes.ORDER) | {"log_errors", "story", "menu"}
    for i in ITEMS: assert i["probe"].split(".")[0] in names, i["id"]
    assert set(POOL["default_picks"]) <= {i["id"] for i in READY}
    for m, spec in NEG.items():
        assert (gbase.gdir("supertuxkart") / "negmods" / m / "data").is_dir()
        assert set(spec["expect_fail"]) <= {i["id"] for i in READY}
        assert set(spec["quick_probes"]) <= set(P.STKProbes.ORDER)


def test_aa_recorded_agrees():
    assert fails(rec("base"), rec("aa")) == []


def test_negative_recorded():
    """each recorded negative run (it ran only the probes of its own items) fails exactly its negmods.yaml items"""
    b = rec("base")
    for m, spec in NEG.items():
        g = rec(m); probed = {i["id"] for i in READY if i["probe"].split(".")[0] in g}
        exp = {x for x in spec["expect_fail"] if x in probed}; may = {x for x in spec.get("may_fail") or [] if x in probed}
        assert exp <= set(fails(b, g)) <= exp | may, m           # the selfcheck rule (may_fail: shows only sometimes)


def test_heavy_scene_recorded_p4():
    """the pool's FRAMETIME negative as recorded on a test sandbox (fps + tracks only); not a selfcheck negmod (negmods.yaml)"""
    assert fails(rec("base"), rec("neg_heavy_scene")) == ["SK-FRAMETIME", "SK-TRACKS"]


def test_missing_and_crash():
    b = rec("base")
    assert P.item_result("drive.t10+t30", b, {"drive": {"error": "x"}})[0] is False     # a probe error in the mod run is a fail
    assert P.item_result("fps", b, {"fps": {"fps": None}})[0] is None
    assert P.item_result("log_errors", b, {**b, "_runs": [{"tag": "race_lighthouse_0", "crashed": True, "errors": 0}]})[0] is False
    assert P.compare("perf", {"sim_fps": 4000}, {"sim_fps": 2100})[0] is True and P.compare("perf", {"sim_fps": 4000}, {"sim_fps": 1900})[0] is False


def test_build_checks(tmp_path):
    ad = A.Adapter.__new__(A.Adapter)
    m = tmp_path / "mod"; m.mkdir()
    ok, log = ad.build("x", m); assert not ok and "no data/" in log
    (m / "data").mkdir(); (m / "data" / "stk_config.xml").write_text("<config/>")
    assert ad.build("x", m)[0]
    k = m / "addons" / "karts" / "mykart"; k.mkdir(parents=True); (k / "kart.xml").write_text("<kart name='x'/>")
    t = m / "addons" / "tracks" / "mytrack"; t.mkdir(parents=True); (t / "track.xml").write_text("<track/>"); (t / "scene.xml").write_text("<scene/>")
    (m / "start.yaml").write_text("mode: race\ntrack: mytrack\nkart: mykart\nnumkarts: 4\nlaps: 2\n"); assert ad.build("x", m)[0], ad.build("x", m)
    (m / "start.yaml").write_text("mode: race\ntrack: lighthouse\nrace_mode: battle\n"); assert "arena" in ad.build("x", m)[1]
    (m / "start.yaml").write_text("mode: fly\ntrack: nowhere\nkart: nokart\nrace_mode: soccer\nlaps: 99\nfoo: 1\n")
    ok, log = ad.build("x", m)
    assert not ok and all(s in log for s in ("mode", "nowhere", "nokart", "soccer", "laps", "foo"))
    (m / "start.yaml").unlink(); (m / "data" / "bad.xml").write_text("<a><b></a>")
    ok, log = ad.build("x", m); assert not ok and "bad.xml" in log
    (m / "data" / "bad.xml").unlink(); (m / "addons" / "music").mkdir(); assert "addons/music" in ad.build("x", m)[1]


def test_layout_side_by_side(tmp_path):
    """mod/<name>/data (also how `gab selfcheck` installs a negative mod) and mod/data are both one mod each"""
    m = tmp_path / "mod"; (m / "a" / "data").mkdir(parents=True); (m / "b" / "addons" / "karts").mkdir(parents=True)
    assert [x.name for x in A.Adapter._layout(m)[0]] == ["a", "b"]
    assert A.Adapter._layout(m / "a")[0] == [m / "a"]


def test_start_args(tmp_path):
    assert P.start_args({}) == [] and P.start_args({"mode": "menu"}) == []
    a = P.start_args({"mode": "race", "track": "zengarden", "kart": "gnu", "laps": 2})
    assert a[:3] == ["-R", "--track=zengarden", "--kart=gnu"] and "--laps=2" in a and "--mode=0" in a
    b = P.start_args({"mode": "race", "track": "stadium", "race_mode": "battle"})
    assert "--mode=2" in b and not any(x.startswith("--laps") for x in b)
    inst = P.Instance.__new__(P.Instance); inst.home = tmp_path
    (tmp_path / "data" / "supertuxkart" / "addons" / "karts" / "mykart").mkdir(parents=True)
    assert "--kart=addon_mykart" in P.start_args({"mode": "race", "kart": "mykart"}, inst)


def test_track_kind(tmp_path):
    for attrs, kind in (('name="x"', "race"), ('arena = "Y" ctf="Y"', "arena"), ('soccer="Y"', "soccer"), ('cutscene="Y"', "internal"),
                        ('max-arena-players="10"', "race")):
        (tmp_path / "track.xml").write_text(f"<track {attrs}/>"); assert P.track_kind(tmp_path) == kind, attrs


def test_is_menu(tmp_path):
    """main menu = yellow bottom bar (dev_menu.png bottom strip ~ (210, 178, 91)); loading screen = grey (~ (79, 79, 79))"""
    from PIL import Image
    for rgb, want in (((210, 178, 91), True), ((79, 79, 79), False)):
        im = Image.new("RGB", (1024, 600), (120, 120, 120)); im.paste(rgb, (0, 560, 1024, 600)); im.save(tmp_path / "s.png")
        assert P.is_menu(tmp_path / "s.png") is want


def test_parse_state():
    """STATE_SPEC shape from the state() shell output: live bridge lines -> race + karts; mod_static.json -> mod"""
    ms = {"karts_added": [], "tracks_added": {}, "objects_added": [{"track": "lighthouse", "tag": "library", "id": "gab_x"}],
          "settings_changed": {"kart_characteristics.xml:x@power": ["950", "1400"]}, "files": [], "n_files": 3}
    t = ("AGE 1\nLOADS 1\nGP 1\nYou chose to start in track 'lighthouse'\nERR 0\n"
         "[info   ] Script: GABST 0 3 1100 16.8 -13.9 -50.7 3.0 0.0 4.0 23.0\n[info   ] Script: GABST 2 3 1100 10 1 2 0 0 0 23\n"
         "[info   ] Script: GABMSG Welcome to the race\nALIVE\nSTART {}\nMODSTATIC\n" + json.dumps(ms))
    st = A.parse_state(t, True, {"mode": "race"})
    assert set(st) == {"running", "engine", "mod", "screen_text"}
    e = st["engine"]
    assert e["screen"] == "race" and e["track"] == "lighthouse" and e["race"]["mode"] == "normal race" and e["race"]["karts"] == 3
    assert [k["kart"] for k in e["karts"]] == [0, 2] and e["karts"][0]["speed"] == 5.0 and e["karts"][0]["pos"] == [16.8, -13.9, -50.7]
    assert st["mod"]["objects_added"][0]["id"] == "gab_x" and st["screen_text"] == ["Welcome to the race"]
    menu = A.parse_state("AGE 30\nLOADS 0\nGP 1\nERR 0\nMODSTATIC\n", True, {"mode": "menu"})
    assert menu["engine"]["screen"] == "menu" and menu["engine"]["karts"] == [] and menu["mod"] == {} and menu["screen_text"] == []
    assert gbase.state_diff(menu, st)


def test_start_fails_fast_when_the_track_scripts_do_not_compile(monkeypatch):
    """2026-11-08: a mod whose track scripts fail to build ("Scripting: Build() failed") never reaches the race screen
    (gab's tracker is built with them); the gate used to wait the full 900 s for it. Now it fails ~30 s after the error."""
    import types
    ad = A.Adapter.__new__(A.Adapter)
    clock = {"t": 0.0}
    monkeypatch.setattr(A.time, "time", lambda: clock["t"])
    monkeypatch.setattr(A.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    ad._start = lambda inst: {"mode": "race", "track": "t"}
    ad.mdir = lambda inst: "/m"; ad.stop = lambda inst: None; ad.iname = lambda inst: "i"; ad.idir = lambda inst: "/i"
    ad._modargs = lambda d: ""; ad.root = "/r"; ad.gl = "gl"
    ad._py = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="")
    ad._alive = lambda inst: True
    ad._x = lambda inst, cmd, timeout=180: types.SimpleNamespace(stdout="123")      # the window is there
    ad.state = lambda inst: {"engine": {"screen": "loading"}}
    ad.log_tail = lambda inst: "[error  ] Scripting: Build() failed"
    ad.seat = types.SimpleNamespace(display=":1", exec=lambda cmd, *a, **k: types.SimpleNamespace(stdout="2" if "Build() failed" in cmd else ""))
    ok, log = ad._start_once("inst")
    assert not ok and "scripts do not compile" in log and clock["t"] < 120

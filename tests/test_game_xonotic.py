"""Xonotic adapter without a sandbox: pool <-> probes, compare() on the recorded pool verification (2026-10-30: two vanilla
sessions and the negative mods), the deliverable structure check, the start script."""
import json, zipfile
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.xonotic import adapter as A, probes as P

D = Path(__file__).parent / "data" / "xonotic"
POOL = yaml.safe_load((Path(__file__).parents[1] / "benchmark" / "pools" / "xonotic" / "pool.yaml").read_text())
ITEMS = POOL["always"] + POOL["pool"]
READY = [i for i in ITEMS if i["status"] == "ready"]


def rec(n): return json.loads((D / f"{n}.json").read_text())


def fails(base, got): return sorted(i["id"] for i in READY if P.item_result(i["probe"], base, got)[0] is False)


def test_registered():
    assert "xonotic" in gbase.GAMES and gbase.title("xonotic") == "Xonotic"
    assert (gbase.gdir("xonotic") / "devkit" / "README.md").exists()


def test_pool_probes_exist():
    names = set(P.XOProbes.ORDER) | {"log_errors", "client"}
    for i in ITEMS: assert i["probe"].split(".")[0] in names, i["id"]
    assert set(POOL["default_picks"]) <= {i["id"] for i in READY}
    neg = yaml.safe_load((gbase.gdir("xonotic") / "negmods" / "negmods.yaml").read_text())
    for m, spec in neg.items():
        assert (gbase.gdir("xonotic") / "negmods" / m).is_dir()
        assert set(spec["expect_fail"]) <= {i["id"] for i in READY}


def test_aa_recorded_agrees():
    a, b = rec("aa1"), rec("aa2")
    assert fails(a, b) == []
    assert all(P.item_result(i["probe"], a, b)[0] is True for i in READY)


def test_negative_recorded():
    a = rec("aa1")
    assert fails(a, rec("neg_gravity")) == ["XO-GRAVITY", "XO-JUMP", "XO-PHYSICS-SETTINGS"]
    assert fails(a, rec("neg_vortex")) == ["XO-WEAPON-SETTINGS", "XO-WEAPONS"]
    assert fails(a, rec("neg_hud")) == ["XO-HUD"]


def test_missing_and_crash():
    a = rec("aa1")
    assert P.item_result("move.run_speed+travel", a, {"move": {"error": "x"}})[0] is None
    assert P.item_result("client.hud", a, {"client": {"hud": {"error": "aborted"}}})[0] is None
    assert P.item_result("log_errors", a, {**a, "_crashed_at": "weapons"})[0] is False
    assert P.compare("perf", {"cpu_pct": 6.0}, {"cpu_pct": 11.9})[0] is True and P.compare("perf", {"cpu_pct": 6.0}, {"cpu_pct": 12.5})[0] is False


def test_build_checks(tmp_path):
    ad = A.Adapter.__new__(A.Adapter)
    m = tmp_path / "mod"; m.mkdir()
    ok, log = ad.build("x", m); assert not ok and "nothing" in log
    (m / "autoexec.cfg").write_text("sv_gravity 700\n")
    assert ad.build("x", m)[0]
    with zipfile.ZipFile(m / "zz-x.pk3", "w") as z: z.writestr("maps/mymap.bsp", b"x")
    (m / "start.yaml").write_text("map: mymap\ngametype: ctf\nbots: 3\n"); assert ad.build("x", m)[0]
    (m / "start.yaml").write_text("map: nomap\ngametype: soccer\nbots: 40\nmode: x\nfoo: 1\n")
    ok, log = ad.build("x", m)
    assert not ok and all(s in log for s in ("nomap", "soccer", "bots", "mode", "foo"))
    (m / "start.yaml").unlink(); (m / "bad.pk3").write_text("not a zip")
    ok, log = ad.build("x", m); assert not ok and "bad.pk3" in log


def test_start_cfg_and_launcher():
    c = A.START_CFG.format(bots=2, pw="pw1", map="map catharsis")
    assert "set bot_number 2" in c and 'rcon_password "pw1"' in c and c.rstrip().endswith("map catharsis")
    for s in ("_termsofservice_accepted", "cl_allow_uid2name\" \"0", "cl_curl_enabled 0", "net_address 127.0.0.1", "echo $$ > $U.pid"):
        assert s in P.CLIENT_SH_TEXT, s


# ---- STATE_SPEC helpers (2026-10-31)
def test_state_parsers():
    e = P.parse_edict("\nserver EDICT 1:\nclassname      player\norigin         '720 -944 -7.96875095'\nhealth         100\nnetname        GameAnything\n")
    assert e == {"classname": "player", "origin": "'720 -944 -7.96875095'", "health": "100", "netname": "GameAnything"}
    cv = P.parse_cvarlist('sv_gravity is "800" ["800"] gravity\ngab_x is "a b" ["a b"] custom cvar\n2 cvar(s)\n')
    assert cv == {"sv_gravity": "800", "gab_x": "a b"}
    assert P.parse_aliases("Current alias commands:\n+attack2 : +button3\ngab_hi : echo hi\n") == {"+attack2": "+button3", "gab_hi": "echo hi"}
    d = P.cvar_diff({"sv_gravity": "600", "gab_x": "1", "rcon_password": "x", "qport": "5", "same": "1"},
                    {"sv_gravity": "800", "rcon_password": "y", "qport": "6", "same": "1"})
    assert d == {"gab_x": {"value": "1", "vanilla": None}, "sv_gravity": {"value": "600", "vanilla": "800"}}


def test_screen_lines():
    log = ("old line\n\n<===================================>\n\nStormkeep\nSound sound/x.wav not found\n"
           "server received rcon command from 127.0.0.1:1:  prvm_global server time; echo __XOP1_1_0__; echo __XOPEND__\n"
           "time: 3.2\n__XOP1_1_0__ \n__XOPPAD____\n__XOPEND__ \n__XOPPAD____\n"
           "\x1b[1;33mGameAnything\x1b[m: ^2hello\nGAB NOTICE\n"
           "server received rcon command from 127.0.0.1:1:  cvarlist; echo __XOP1_2_0__\nsv_gravity is \"800\" [\"800\"]\n")
    assert P.screen_lines(log) == ["Stormkeep", "GameAnything: hello", "GAB NOTICE"]
    assert P.screen_lines("cvar_a : x\n__XOPEND__ \nline after\n") == ["line after"]          # the tail starts inside a block


def test_shape_state():
    st = A.shape_state({"running": True, "engine": {"map": "stormkeep", "player": {"health": 100}}, "mod": {"cvars": {"x": {"value": "1"}}},
                        "screen_text": ["a"] * 20})
    assert list(st) == ["running", "engine", "mod", "screen_text"] and list(st["engine"]) == A.ENGINE_KEYS
    assert list(st["engine"]["player"]) == A.PLAYER_KEYS and st["engine"]["player"]["ammo"] == {} and st["engine"]["others"] == {}
    assert st["mod"]["entities"] == {} and st["mod"]["cvars"] == {"x": {"value": "1"}} and len(st["screen_text"]) == 15
    st = A.shape_state({"running": False})
    assert st["running"] is False and st["engine"]["map"] is None and st["mod"] == {"cvars": {}, "aliases": {}, "entities": {}}


def test_map_classnames(tmp_path):
    u = tmp_path / "u"; (u / "data" / "zz.pk3dir" / "maps").mkdir(parents=True)
    (u / "data" / "zz.pk3dir" / "maps" / "m1.ent").write_text('{ "classname" "worldspawn" }\n{ "classname" "weapon_shotgun" "origin" "0 0 0" }')
    with zipfile.ZipFile(u / "data" / "m2.pk3", "w") as z:
        lump = b'{ "classname" "item_health_mega" }'
        hdr = b"IBSP" + (46).to_bytes(4, "little") + (16).to_bytes(4, "little") + len(lump).to_bytes(4, "little")
        z.writestr("maps/m2.bsp", hdr + lump)
    assert P.map_classnames(u, "m1") == {"worldspawn", "weapon_shotgun"}
    assert P.map_classnames(u, "m2") == {"item_health_mega"}


def test_statecheck_files():
    d = gbase.gdir("xonotic") / "statecheck"; spec = yaml.safe_load((d / "statecheck.yaml").read_text())
    ad = A.Adapter.__new__(A.Adapter)
    assert ad.build("x", d / spec["mod"]["dir"])[0]
    assert {e.split(":")[0] for e in spec["mod"]["expect"]} == {"mod", "screen_text"}

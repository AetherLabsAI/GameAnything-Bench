"""Terraria (tModLoader) adapter without a sandbox: pool <-> probes, compare() on the recorded pool verification (2026-10-30: two
vanilla sessions and the four negative mods, content dumps trimmed), the probe-name expansion, the deliverable structure check,
the state shaping and the gate's error text."""
import json, subprocess
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.terraria import adapter as A, probes as P

D = Path(__file__).parent / "data" / "terraria"
POOL = yaml.safe_load((Path(__file__).parents[1] / "benchmark" / "pools" / "terraria" / "pool.yaml").read_text())
ITEMS = POOL["always"] + POOL["pool"]
READY = [i for i in ITEMS if i["status"] == "ready"]
NEG = yaml.safe_load((gbase.gdir("terraria") / "negmods" / "negmods.yaml").read_text())


def rec(n): return json.loads((D / f"{n}.json").read_text())


def fails(base, got): return sorted(i["id"] for i in READY if P.item_result(i["probe"], base, got)[0] is False)


def test_registered():
    assert "terraria" in gbase.GAMES and gbase.title("terraria") == "Terraria (tModLoader)"
    g = gbase.gdir("terraria")
    for f in ("devkit/README.md", "devkit/template/mod/GabTemplate/build.txt", "devkit/template/mod/GabTemplate/Items/GabSword.png",
              "bridge/GABState/GABState.cs", "bridge/GAProbe/GAProbe.cs", "bridge/trp.sh", "bridge/gtr.sh", "statecheck/statecheck.yaml",
              "statecheck/testmod/GabStateTest/build.txt"):
        assert (g / f).exists(), f
    req = A.Adapter.requirements(A.Adapter.__new__(A.Adapter))
    assert req["steam"] is True and req["hosts"] == ["pool", "docker"] and "own_copy" in req


def test_pool_probes_run():
    """every group an item compares is in the session the framework asks for (it passes the part of `probe:` before the first '.')"""
    for i in READY:
        asked = set(P.groups_of([i["probe"].split(".")[0]])) | {"log_errors"}
        used = {part.strip().split(".")[0] for part in i["probe"].split(",")}
        assert used <= asked | {"log_errors"}, (i["id"], used, asked)
        assert used <= set(P.ALL) | {"log_errors"}, i["id"]
    assert set(POOL["default_picks"]) <= {i["id"] for i in READY}
    for m, spec in NEG.items():
        assert (gbase.gdir("terraria") / "negmods" / m / "build.txt").is_file()
        assert set(spec["expect_fail"]) <= {i["id"] for i in READY}
        assert P.groups_of(spec["quick_probes"])


def test_groups_of():
    assert P.groups_of(["craft,content", "save,saveload", "log_errors", "newplayer,inventory,accessory"]) == \
        ["craft", "content", "save", "saveload", "newplayer", "inventory", "accessory"]


def test_aa_recorded_agrees():
    a, b = rec("aa1"), rec("aa2")
    assert fails(a, b) == [] and fails(b, a) == []
    assert all(P.item_result(i["probe"], a, b)[0] is True for i in READY)


def test_negative_recorded():
    a = rec("aa1")
    got = {m: fails(a, rec(f)) for m, f in (("NegFall", "neg_fall"), ("NegRecipe", "neg_recipe2"), ("NegSlime", "neg_slime2"), ("NegOres", "neg_ores"))}
    for m, f in got.items(): assert f == sorted(NEG[m]["expect_fail"]), (m, f)


def test_missing_and_crash():
    a = rec("aa1")
    assert P.item_result("move", a, {"move": {"error": "no result (crashed)"}})[0] is False
    assert P.item_result("move", a, {})[0] is None
    assert P.item_result("log_errors", a, {**a, "alive_at_end": False})[0] is False
    assert P.item_result("log_errors", a, {**a, "log_errors": 3})[0] is False
    assert P.compare("perf", {"update_ms_median": 0.8, "ups": 60}, {"update_ms_median": 1.7, "ups": 59})[0] is True
    assert P.compare("perf", {"update_ms_median": 0.8, "ups": 60}, {"update_ms_median": 2.0, "ups": 59})[0] is False


def mkmod(root, name, cs, png=True, extra=None):
    d = root / name; d.mkdir(parents=True)
    (d / "build.txt").write_text("displayName = x\nversion = 0.1\n")
    (d / f"{name}.cs").write_text(cs)
    if png: (d / f"{name}.png").write_bytes(b"\x89PNG")
    for k, v in (extra or {}).items(): (d / k).parent.mkdir(parents=True, exist_ok=True); (d / k).write_text(v)
    return d


def test_layout_checks(tmp_path):
    ad = A.Adapter.__new__(A.Adapter)
    m = tmp_path / "mod"; m.mkdir()
    assert any("no tModLoader mod" in p for p in ad.check_layout(m))
    mkmod(m, "GoodMod", "namespace GoodMod { public class GoodMod : Terraria.ModLoader.Mod { } }")
    assert ad.check_layout(m) == []
    mkmod(m, "Bad-Name", "class X {}")
    mkmod(m, "GABState", "class Y {}")
    (m / "loose").mkdir()
    mkmod(m, "NoTex", "public class Sword : ModItem { }", png=False)
    L = "\n".join(ad.check_layout(m))
    for s in ("Bad-Name", "GABState: name taken", "loose/: no build.txt", "texture PNG"):
        assert s in L, s
    (m / "start.yaml").write_text("mode: arena\nsize: huge\nworld: Nope\nfoo: 1\nplayer: 'x/y'\n")
    L = "\n".join(ad.check_layout(m))
    for s in ("mode must be game or menu", "size must be", "Nope", "unknown key foo", "player must be"):
        assert s in L, s


def test_layout_parts(tmp_path):
    m = tmp_path / "mod"; m.mkdir()
    mkmod(m, "A1", "x"); (m / "B2.tmod").write_bytes(b"tmod"); (m / "worlds").mkdir()
    (m / "worlds" / "Town.wld").write_bytes(b"w"); (m / "worlds" / "Town.twld").write_bytes(b"t")
    (m / "start.yaml").write_text("world: Town\nmode: game\n")
    srcs, tmods, worlds, st = A.Adapter._layout(m)
    assert [s.name for s in srcs] == ["A1"] and [t.name for t in tmods] == ["B2.tmod"]
    assert sorted(w.name for w in worlds) == ["Town.twld", "Town.wld"] and st == {"world": "Town", "mode": "game"}
    assert A.Adapter.check_layout(A.Adapter.__new__(A.Adapter), m) == []
    h1 = A.tree_hash([m / "A1"]); (m / "A1" / "obj").mkdir(); (m / "A1" / "obj" / "x.cache").write_text("junk")
    assert A.tree_hash([m / "A1"]) == h1                 # build output is not part of the source hash
    (m / "A1" / "A1.cs").write_text("y"); assert A.tree_hash([m / "A1"]) != h1


def test_shape_state():
    raw = {"id": "1", "running": True, "engine": {"screen": "in_game", "player": {"name": "Player"}, "mods": ["GabTemplate 0.1"]},
           "mod": {"vars": {"GabTemplate.S.x": 1}}, "screen_text": ["chat: hi"] * 40}
    st = A.shape_state(raw)
    assert list(st) == ["running", "engine", "mod", "screen_text"] and list(st["engine"]) == A.ENGINE_KEYS
    assert st["engine"]["menus_open"] == [] and st["engine"]["npcs"] == {} and len(st["screen_text"]) == 30
    st = A.shape_state({"running": True, "error": "no answer from the game in 2.5 s"})
    assert st["running"] and st["engine"]["screen"] == "starting" and st["mod"] == {} and "error" in st
    assert A.shape_state({"running": False, "error": "x"}, running=False)["running"] is False


def test_error_lines():
    log = ("[12:00:00.000] [Main Thread/INFO] [tML]: Loading mods\n"
           "[12:00:01.000] [Main Thread/ERROR] [tML]: An error occurred while loading BadMod\n"
           "System.Exception: Texture not found: BadMod/Items/Sword\n   at Terraria.ModLoader.ModContent.Request\n"
           "[12:00:01.500] [Main Thread/WARN] [tML]: Disabling Mod: BadMod\n"
           "[12:00:02.000] [Main Thread/INFO] [tML]: GAPROBE {\"probe\": \"x\"}\n"
           "[12:00:02.500] [Main Thread/ERROR] [tML]: An error occurred while loading BadMod\n")
    e = A.error_lines(log)
    assert any("An error occurred while loading BadMod" in x for x in e) and any("Disabling Mod: BadMod" in x for x in e)
    assert not any("GAPROBE" in x for x in e) and len(e) == len(set(e))
    one = A.error_lines(log, 1); assert len(one) == 2 and one[1].startswith("... ") and "more distinct" in one[1]
    g = gbase.read_gate_log(gbase.gate_log(e, "tModLoader could not load the mods", "tail"))
    assert g["reason"].startswith("tModLoader") and g["errors"] == e


def test_runner_scripts_parse():
    for f in ("trp.sh", "gtr.sh"):
        assert subprocess.run(["bash", "-n", str(gbase.gdir("terraria") / "bridge" / f)]).returncode == 0, f
    assert "TRP_ROOT" in (gbase.gdir("terraria") / "bridge" / "trp.sh").read_text()


def test_steam_sandbox_selfcheck_recorded():
    """2026-10-31 gab selfcheck on a Steam sandbox: A/A agree; NegFall's combat run had the slime knocked out of
    reach (4 -> 2 hits, slime speed 5.5 -> 7.7) -- with the combat compare change only TR-MOVE fails"""
    a, b = rec("lq_aa1"), rec("lq_aa2")
    assert fails(a, b) == [] and fails(b, a) == []
    n = rec("lq_neg_fall")
    assert n["combat"]["hits"] == 2 and a["combat"]["hits"] == 4 and fails(a, n) == ["TR-MOVE"]
    assert P.compare("combat", a["combat"], {**a["combat"], "hits": 0})[0] is False          # no hit landing still fails

"""Minecraft adapter without a sandbox: pool <-> probes, compare() on the recorded self-check runs (2026-10-31: two vanilla
instances and the negative mods), the deliverable layout / start.yaml checks, the launcher's version-file logic, the
resources-only build, the game-log error extraction, the state shape."""
import json, zipfile
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.minecraft import adapter as A, mcgab as M, probes as P

D = Path(__file__).parent / "data" / "minecraft"
POOL = yaml.safe_load((Path(__file__).parents[1] / "benchmark" / "pools" / "minecraft" / "pool.yaml").read_text())
ITEMS = POOL["always"] + POOL["pool"]
READY = [i for i in ITEMS if i["status"] == "ready"]
NEG = yaml.safe_load((gbase.gdir("minecraft") / "negmods" / "negmods.yaml").read_text())


def rec(n): return json.loads((D / f"{n}.json").read_text())


def fails(base, got): return sorted(i["id"] for i in READY if P.item_result(i["probe"], base, got)[0] is False)


def test_registered():
    assert "minecraft" in gbase.GAMES and gbase.title("minecraft").startswith("Minecraft")
    assert (gbase.gdir("minecraft") / "devkit" / "README.md").exists()
    assert (gbase.gdir("minecraft") / "devkit" / "template" / "ACCESS.md").exists()


def test_pool_probes_exist():
    for i in ITEMS: assert i["probe"].split(".")[0] in P.NAMES, i["id"]
    assert set(POOL["default_picks"]) <= {i["id"] for i in READY}
    assert P.ORDER == __import__("gab.game.minecraft.mcprobes", fromlist=["ORDER"]).ORDER
    for m, spec in NEG.items():
        assert (gbase.gdir("minecraft") / "negmods" / m).is_dir()
        assert set(spec["expect_fail"]) <= {i["id"] for i in READY}
        assert not M.layout(gbase.gdir("minecraft") / "negmods" / m)["problems"], m


def test_aa_recorded_agrees():
    a, b = rec("aaA"), rec("aaB")
    assert fails(a, b) == []
    assert all(P.item_result(i["probe"], a, b)[0] is True for i in READY)


def test_negative_recorded():
    a = rec("aaA")
    for m, spec in NEG.items():
        f = fails(a, rec(f"neg_{m}"))
        assert set(spec["expect_fail"]) <= set(f) <= set(spec["expect_fail"]) | set(spec.get("may_fail") or []), (m, f)


def test_compare_rules():
    assert P.compare("tick_time", {"mspt": 10.0}, {"mspt": 19.9})[0] is True and P.compare("tick_time", {"mspt": 10.0}, {"mspt": 20.5})[0] is False
    assert P.compare("tick_time", {"mspt": 2.0}, {"mspt": 9.9})[0] is True                     # + 8 ms
    h = [[64] * 10 for _ in range(10)]; g = [r[:] for r in h]
    for k in range(7): g[k][0] = 70
    assert P.compare("terrain_spawn", {"heights": h}, {"heights": g})[0] is True             # 7%
    g[8][0] = 70; g[9][0] = 70
    assert P.compare("terrain_spawn", {"heights": h}, {"heights": g})[0] is False            # 9%
    assert P.compare("log_errors", {"errors": 0}, {"errors": 0})[0] is True
    assert P.compare("log_errors", {"errors": 0}, {"errors": 2, "lines": ["x"]})[0] is False
    assert P.compare("log_errors", {"errors": 3}, {"errors": 0, "crash": True, "crash_report": ["boom"]})[0] is False
    assert P.compare("combat", {"damage_dealt": True, "_h": [10, 9]}, {"damage_dealt": True, "_h": [10, 8]})[0] is True   # _debug ignored


def test_item_result_missing_and_crash():
    base = {"player_start": {"items": [], "health": 20.0}, "log_errors": {"errors": 0}}
    assert P.item_result("player_start.items", base, {"player_start": {"error": "x"}})[0] is None
    assert P.item_result("player_start.items", base, {})[0] is None
    assert P.item_result("combat", base, {"combat": {"damage_dealt": True}})[0] is None          # not in the baseline
    assert P.item_result("log_errors", base, {"log_errors": {"errors": 0}, "_crashed_at": "combat"})[0] is False
    assert P.item_result("player_start.items", base, {"player_start": {"items": [["minecraft:stick", 2]], "health": 20.0}})[0] is False
    assert P.item_result("player_start.health", base, {"player_start": {"items": [["minecraft:stick", 2]], "health": 20.0}})[0] is True


def _jar(p, meta):
    with zipfile.ZipFile(p, "w") as z:
        if meta is not None: z.writestr("fabric.mod.json", json.dumps(meta))
        z.writestr("a/B.class", b"x")


def test_layout(tmp_path):
    m = tmp_path / "mod"; m.mkdir()
    assert any("nothing installable" in p for p in M.layout(m)["problems"])
    pj = m / "my_mod" / "src" / "main" / "resources"; pj.mkdir(parents=True); (pj / "fabric.mod.json").write_text('{"id": "my_mod"}')
    _jar(m / "extra.jar", {"id": "extra"}); _jar(m / "forge.jar", None)
    dp = m / "quests"; (dp / "data" / "q" / "function").mkdir(parents=True); (dp / "pack.mcmeta").write_text("{}")
    rp = m / "resourcepacks" / "looks"; (rp / "assets" / "minecraft").mkdir(parents=True); (rp / "pack.mcmeta").write_text("{}")
    (m / "saves" / "town").mkdir(parents=True); (m / "saves" / "town" / "level.dat").write_bytes(b"x"); (m / "saves" / "junk").mkdir()
    (m / "config").mkdir(); (m / "README.md").write_text("x"); (m / "stuff").mkdir()
    (m / "start.yaml").write_text("world: town\nseed: 5\ngame_mode: creative\ncheats: yes\n")
    L = M.layout(m)
    assert [p.name for p in L["projects"]] == ["my_mod"] and [p.name for p in L["jars"]] == ["extra.jar"]
    assert [p.name for p in L["datapacks"]] == ["quests"] and [p.name for p in L["resourcepacks"]] == ["looks"]
    assert [p.name for p in L["saves"]] == ["town"] and L["config"] and L["start"] == {"world": "town", "seed": 5, "game_mode": "creative", "cheats": True}
    assert any("forge.jar" in p and "fabric.mod.json" in p for p in L["problems"]) and any("saves/junk" in p for p in L["problems"])
    assert any(w.startswith("stuff/") for w in L["warnings"])
    (m / "start.yaml").write_text("world: nowhere\ngame_mode: god\nseed: abc\nfoo: 1\nbad line\n")
    P_ = M.layout(m)["problems"]
    for s in ("nowhere", "game_mode must be", "seed must be", "unknown key foo", "line 5"): assert any(s in p for p in P_), s


def test_layout_single_project_and_pack(tmp_path):
    p = tmp_path / "mod"; (p / "src" / "main" / "java").mkdir(parents=True); (p / "fabric.mod.json").write_text('{"id": "x"}')
    (p / "start.yaml").write_text("difficulty: hard\n")
    L = M.layout(p); assert L["projects"] == [p] and not L["problems"] and L["start"] == {"difficulty": "hard"}
    d = tmp_path / "pack"; (d / "data").mkdir(parents=True); (d / "pack.mcmeta").write_text("{}")
    L = M.layout(d); assert L["datapacks"] == [d] and not L["problems"]


def test_template_and_statecheck_layouts():
    g = gbase.gdir("minecraft")
    L = M.layout(g / "devkit" / "template" / "mod"); assert not L["problems"] and [p.name for p in L["projects"]] == ["gab_template"]
    assert L["start"]["game_mode"] == "survival"
    spec = yaml.safe_load((g / "statecheck" / "statecheck.yaml").read_text())
    L = M.layout(g / "statecheck" / spec["mod"]["dir"]); assert not L["problems"] and L["projects"] and L["datapacks"]
    assert {e.split(":")[0] for e in spec["mod"]["expect"]} == {"mod", "screen_text"}


def test_build_resources_only(tmp_path):
    src = tmp_path / "p"; r = src / "src" / "main" / "resources"; (r / "assets" / "x").mkdir(parents=True)
    (r / "fabric.mod.json").write_text('{"id": "res_only", "version": "${version}"}'); (r / "assets" / "x" / "a.txt").write_text("hi")
    out = M.build(src, tmp_path / "o.jar", "/nonexistent", "v", "/nonexistent.jar")
    assert out["ok"] and out["modid"] == "res_only"
    with zipfile.ZipFile(tmp_path / "o.jar") as z:
        assert json.loads(z.read("fabric.mod.json"))["version"] == "1.0.0" and "assets/x/a.txt" in z.namelist()
    (r / "fabric.mod.json").write_text('{"id": "Bad Id"}')
    assert not M.build(src, tmp_path / "o2.jar", "/x", "v", "/x.jar")["ok"]


def test_version_files(tmp_path):
    v = tmp_path / "versions"; (v / "26.3").mkdir(parents=True); (v / "fab").mkdir()
    (v / "26.3" / "26.3.json").write_text(json.dumps({"id": "26.3", "mainClass": "net.minecraft.client.main.Main", "assets": "34",
        "arguments": {"jvm": ["-cp", "${classpath}"], "game": ["--username", "${auth_player_name}"]},
        "libraries": [{"name": "org.ow2.asm:asm:9.0", "downloads": {"artifact": {"path": "org/ow2/asm/asm/9.0/asm-9.0.jar"}}},
                      {"name": "org.lwjgl:lwjgl:3.3:natives-linux", "rules": [{"action": "allow", "os": {"name": "linux"}}]},
                      {"name": "org.lwjgl:lwjgl:3.3:natives-macos", "rules": [{"action": "allow", "os": {"name": "osx"}}]},
                      {"name": "ca.weblite:objc:1.1", "rules": [{"action": "allow"}, {"action": "disallow", "os": {"name": "linux"}}]}]}))
    (v / "fab" / "fab.json").write_text(json.dumps({"id": "fab", "inheritsFrom": "26.3", "mainClass": "Knot",
        "arguments": {"jvm": ["-DFabricMcEmu= x "]}, "libraries": [{"name": "org.ow2.asm:asm:9.9", "url": "https://maven"}]}))
    ver, cp = M.classpath(str(tmp_path), "fab")
    assert ver["mainClass"] == "Knot" and ver["jar_version"] == "26.3" and ver["arguments"]["jvm"][-1] == "-DFabricMcEmu= x "
    rel = [str(Path(p).relative_to(tmp_path)) for p in cp]
    assert rel == ["libraries/org/ow2/asm/asm/9.9/asm-9.9.jar", "libraries/org/lwjgl/lwjgl/3.3/lwjgl-3.3-natives-linux.jar", "versions/26.3/26.3.jar"]
    assert M.allowed([{"action": "allow", "features": {"has_custom_resolution": True}}], {"has_custom_resolution": True})
    assert not M.allowed([{"action": "allow", "features": {"is_demo_user": True}}], {})
    assert not M.allowed([{"action": "allow", "os": {"arch": "x86"}}])


def test_offline_uuid():
    assert M.offline_uuid("Player") == "a01e3843e5213998958af459800e4d11"        # the game logged this uuid for Player (offline)


def test_errors(tmp_path):
    i = tmp_path / "inst"; (i / "logs").mkdir(parents=True)
    (i / "logs" / "latest.log").write_text("\n".join([
        "[20:34:55] [Minecraft Services Discovery/WARN]: Failed to connect",
        "com.mojang.authlib.exceptions.MinecraftClientException: Failed to read from https://discovery.minecraftservices.com",
        "Caused by: java.net.ConnectException: Connection refused",
        "[20:34:55] [Download-2/ERROR]: Failed to fetch user properties",
        "com.mojang.authlib.exceptions.AuthenticationUnavailableException",
        "[20:34:56] [Download-2/ERROR]: Couldn't connect to realms",
        "[20:35:00] [Render thread/WARN]: some warning",
        "java.lang.IllegalStateException: only a warning",
        "[20:35:01] [Server thread/ERROR]: Failed to load function mypack:start",
        "java.lang.IllegalArgumentException: Unknown function mypack:helper",
        "\tat net.minecraft.Foo.bar(Foo.java:1)",
        "Caused by: java.lang.NullPointerException: boom",
        "[20:35:02] [Server thread/ERROR]: Failed to load function mypack:start",
        "[20:35:03] [Render thread/INFO]: fine"]))
    e = M.errors(i)
    assert e["errors"] == 2 and not e["crash"]
    assert e["lines"] == ["[Server thread/ERROR]: Failed to load function mypack:start", "java.lang.IllegalArgumentException: Unknown function mypack:helper",
                          "Caused by: java.lang.NullPointerException: boom"]
    (i / "crash-reports").mkdir(); (i / "crash-reports" / "c.txt").write_text("---- Minecraft Crash Report ----\n// joke\nDescription: Initializing game\n")
    assert M.errors(i)["crash"] and "Description: Initializing game" in M.errors(i)["crash_report"]
    j = tmp_path / "j"; (j / "gab").mkdir(parents=True); (j / "gab" / "launcher.log").write_text("Error: Could not find or load main class Knot\n")
    assert M.errors(j)["errors"] == 1


def test_shape_state():
    st = A.shape_state({"running": True, "engine": {"screen": "in_game", "pos": [1, 2, 3], "fps": 60}, "mod": {"mods": {"x": "1"}},
                        "screen_text": ["a"] * 60})
    assert list(st) == ["running", "engine", "mod", "screen_text"] and list(st["engine"]) == A.ENGINE_KEYS and "fps" not in st["engine"]
    assert list(st["mod"]) == list(A.MOD_KEYS) and st["mod"]["mods"] == {"x": "1"} and st["mod"]["entities"] == {} and len(st["screen_text"]) == 45
    st = A.shape_state({"running": False})
    assert st["running"] is False and st["engine"]["pos"] is None and st["engine"]["inventory"] == {} and st["mod"]["datapacks"] == []


def test_bridge_files_and_options():
    files, h = A.bridge_files()
    assert "fabric.mod.json" in files and any(k.endswith("GabBridge.java") for k in files) and len(h) == 16
    o = (gbase.gdir("minecraft") / "options.txt").read_text()
    for s in ("onboardAccessibility:false", "tutorialStep:none", "pauseOnLostFocus:false", "telemetryOptInExtra:false", "realmsNotifications:false"):
        assert s in o, s


def test_install_and_fresh_world(tmp_path):
    inst = tmp_path / "inst"; (inst / "mods").mkdir(parents=True); (inst / "gab").mkdir()
    (inst / "options.txt").write_text("lang:en_us\nresourcePacks:[]\n")
    _jar(inst / "mods" / "fabric-api-0.162.0+26.3.jar", {"id": "fabric-api"}); _jar(inst / "mods" / "gabbridge.jar", {"id": "gabbridge"})
    _jar(inst / "mods" / "old_mod.jar", {"id": "old"})
    m = tmp_path / "mod"; m.mkdir()
    _jar(m / "cool.jar", {"id": "cool"}); _jar(m / "fapi.jar", {"id": "fabric-api"})
    (m / "pack" / "data").mkdir(parents=True); (m / "pack" / "pack.mcmeta").write_text("{}")
    (m / "looks" / "assets").mkdir(parents=True); (m / "looks" / "pack.mcmeta").write_text("{}")
    (m / "config").mkdir(); (m / "config" / "cool.json").write_text("{}")
    (m / "saves" / "town").mkdir(parents=True); (m / "saves" / "town" / "level.dat").write_bytes(b"x")
    (m / "start.yaml").write_text("world: town\ngame_mode: creative\n")
    r = M.install(m, inst, "/nomain", "v", "/noapi.jar", tmp_path / "cache")
    assert r["ok"] and r["mods"] == ["cool.jar"] and r["start"]["mode"] == "save" and r["start"]["game_mode"] == "creative"
    assert sorted(p.name for p in (inst / "mods").iterdir()) == ["cool.jar", "fabric-api-0.162.0+26.3.jar", "gabbridge.jar"]
    assert 'resourcePacks:["vanilla", "file/looks"]' in (inst / "options.txt").read_text() and (inst / "config" / "cool.json").exists()
    st = M.fresh_world(inst)
    assert st["world"] == "town" and (inst / "saves" / "town" / "level.dat").exists() and (inst / "saves" / "town" / "datapacks" / "pack" / "pack.mcmeta").exists()
    r = M.install(m, inst, "/nomain", "v", "/noapi.jar", tmp_path / "cache", probe=True)       # probes: the default fresh world
    assert r["start"]["mode"] == "fresh" and r["start"]["seed"] == 2024 and r["start"]["game_mode"] == "survival"
    M.fresh_world(inst); assert (inst / "saves" / "gab_world" / "datapacks" / "pack").is_dir() and not (inst / "saves" / "town").exists()
    e = tmp_path / "empty"; e.mkdir()
    r = M.install(e, inst, "/nomain", "v", "/noapi.jar", tmp_path / "cache")                 # empty mod/ = the vanilla game
    assert r["ok"] and r["mods"] == [] and not list((inst / "gab" / "datapacks").iterdir())


# ---- gate / `game build` answers (gbase.gate_log: REASON + ERRORS first), with a fake seat
class _Seat:
    sandbox, seat, display, home, lease, info = "box", "1", ":101", "/h", "", {}
    def __init__(self, answers): self.answers = answers
    def exec(self, cmd, timeout=600, env=None, check=False):
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, next((v for k, v in self.answers.items() if k in cmd), ""), "")
    def put(self, path, data): pass


CRASH = """---- Minecraft Crash Report ----
// Who set us up the TNT?
Time: 2026-10-31 21:05:16
Description: Initializing game
java.lang.RuntimeException: Could not execute entrypoint stage 'main' due to errors, provided by 'crashmod' at 'org.x.Crash'!
\tat net.fabricmc.loader.impl.FabricLoaderImpl.lambda$invokeEntrypoints$0(FabricLoaderImpl.java:413)
Caused by: java.lang.IllegalStateException: crashmod: the config table is missing
\tat knot//org.x.Crash.onInitialize(Crash.java:3)
"""
LOG = """[21:05:12] [Download-2/ERROR]: Failed to fetch user properties
com.mojang.authlib.exceptions.AuthenticationUnavailableException
\tat knot//com.mojang.authlib.exceptions.MinecraftClientException.toAuthenticationException(MinecraftClientException.java:29)
[21:05:12] [Minecraft Services Discovery/WARN]: Failed to connect
com.mojang.authlib.exceptions.MinecraftClientException: Services are unavailable
[21:06:01] [Render thread/ERROR]: Failed to load function bad:load
java.util.concurrent.CompletionException: java.lang.IllegalArgumentException: Whilst parsing command on line 2: Unknown or incomplete command
[21:06:01] [Render thread/ERROR]: Couldn't connect to realms
"""


def test_gate_crash_and_timeout(monkeypatch):
    ad = A.Adapter(_Seat({"crash-reports": CRASH + LOG, "install_error": "", "start.json": "{}"}), main="/m")
    monkeypatch.setattr(ad, "stop", lambda inst: None)
    monkeypatch.setattr(ad, "_json", lambda args, timeout=0: {"ok": True} if args.startswith("launch") else {"ok": False, "error": "the game exited"})
    ok, log = ad.load_check("eval-x")
    g = gbase.read_gate_log(log)
    assert not ok and g["reason"] == "the game crashed or exited before reaching the world"
    assert g["errors"][:3] == ["Description: Initializing game",
                               "java.lang.RuntimeException: Could not execute entrypoint stage 'main' due to errors, provided by 'crashmod' at 'org.x.Crash'!",
                               "Caused by: java.lang.IllegalStateException: crashmod: the config table is missing"]
    assert any("Failed to load function bad:load" in e for e in g["errors"])
    assert not any("authlib" in e or "realms" in e.lower() or "user properties" in e for e in g["errors"])
    monkeypatch.setattr(ad, "_json", lambda args, timeout=0: {"ok": True} if args.startswith("launch") else
                        {"ok": False, "error": "not in the world after 240 s", "health": {"phase": "loading"}})
    g = gbase.read_gate_log(ad.load_check("eval-x")[1])
    assert "timed out" in g["reason"] and "loading" in g["reason"]


def test_gate_not_loaded_and_install_error(monkeypatch):
    ad = A.Adapter(_Seat({"install.json": json.dumps({"mods": ["mymod.jar"]}), "crash-reports": LOG}), main="/m")
    monkeypatch.setattr(ad, "start", lambda inst: (True, "in the world"))
    monkeypatch.setattr(ad, "state", lambda inst: {"running": True, "mod": {"mods": {}}})
    g = gbase.read_gate_log(ad.load_check("eval-x")[1])
    assert g["reason"] == "mods not loaded by Fabric: ['mymod']" and "[Render thread/ERROR]: Failed to load function bad:load" in g["errors"]
    ad = A.Adapter(_Seat({"install_error": "the mod could not be installed:\nmy: FAILED\nX.java:3: error: cannot find symbol\n  symbol:   class Foo"}), main="/m")
    monkeypatch.setattr(ad, "stop", lambda inst: None)
    g = gbase.read_gate_log(ad.start("dev")[1])
    assert "could not be installed" in g["reason"] and "X.java:3: error: cannot find symbol" in g["errors"]


def test_build_answer_javac(monkeypatch, tmp_path):
    m = tmp_path / "mod"; (m / "src" / "main" / "java").mkdir(parents=True); (m / "fabric.mod.json").write_text('{"id": "my"}')
    ad = A.Adapter(_Seat({}), main="/m"); ad._ready = True
    monkeypatch.setattr(ad, "_stage", lambda inst, d: "/st/mod")
    monkeypatch.setattr(ad, "_json", lambda args, timeout=0: {"ok": False, "problems": [], "warnings": [], "build": [
        "my: FAILED\njavac --release 25 (1 files): rc 1\n/st/mod/src/main/java/A.java:1: error: cannot find symbol\n  Foo y;\n  ^\n  symbol:   class Foo\n1 error"]})
    ok, log = ad.build("x", m); g = gbase.read_gate_log(log)
    assert not ok and g["reason"] == "javac failed for ['my']"
    assert g["errors"] == ["mod/src/main/java/A.java:1: error: cannot find symbol", "symbol:   class Foo"]
    e = tmp_path / "empty"; e.mkdir()
    g = gbase.read_gate_log(ad.build("x", e)[1]); assert "layout" in g["reason"] and "nothing installable" in g["errors"][0]

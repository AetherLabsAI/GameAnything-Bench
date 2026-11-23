"""Valheim (BepInEx) adapter without a sandbox: pool <-> probes (the group mapping), compare() on the recorded pool verification
(2026-10-30: two vanilla sessions and the two negative plugins, content lists trimmed), the deliverable structure check, the state
shaping and the gate's error text, the runners' syntax."""
import json, subprocess
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.valheim import adapter as A, probes as P

D = Path(__file__).parent / "data" / "valheim"
POOL = yaml.safe_load((Path(__file__).parents[1] / "benchmark" / "pools" / "valheim" / "pool.yaml").read_text())
ITEMS = POOL["always"] + POOL["pool"]
READY = [i for i in ITEMS if i["status"] == "ready"]
NEG = yaml.safe_load((gbase.gdir("valheim") / "negmods" / "negmods.yaml").read_text())
RESULT_OF = {"build": "buildcraft", "craft": "buildcraft", "saveload": "save"}   # session result key -> the group that makes it


def rec(n): return json.loads((D / f"{n}.json").read_text())


def fails(base, got): return sorted(i["id"] for i in READY if P.item_result(i["probe"], base, got)[0] is False)


def test_registered():
    assert "valheim" in gbase.GAMES and gbase.title("valheim") == "Valheim (BepInEx)"
    g = gbase.gdir("valheim")
    for f in ("devkit/README.md", "devkit/template/mod/GabTemplate/GabTemplate.csproj", "devkit/template/mod/GabTemplate/Plugin.cs",
              "bridge/ValheimBridge/Plugin.cs", "bridge/driver.py", "bridge/vhp.sh", "bridge/gvh.sh", "bridge/vhstate.py",
              "statecheck/statecheck.yaml", "statecheck/testmod/GabStateTest/GabStateTest.csproj"):
        assert (g / f).exists(), f
    req = A.Adapter.requirements(A.Adapter.__new__(A.Adapter))
    assert req["steam"] is True and req["hosts"] == ["pool", "docker"] and "own_copy" in req


def test_pool_probes_run():
    for i in READY:
        asked = set(P.groups_of([i["probe"].split(".")[0]]))
        used = {RESULT_OF.get(p.strip().split(".")[0], p.strip().split(".")[0]) for p in i["probe"].split(",")} - {"log_errors"}
        assert used <= asked, (i["id"], used, asked)
    assert set(POOL["default_picks"]) <= {i["id"] for i in READY}
    for m, spec in NEG.items():
        assert next((gbase.gdir("valheim") / "negmods" / m).glob("*.csproj"))
        assert set(spec["expect_fail"]) <= {i["id"] for i in READY} and P.groups_of(spec["quick_probes"])


def test_groups_of():
    assert P.groups_of(["move,swim", "craft,content", "build", "saveload", "log_errors"]) == ["content", "move", "swim", "buildcraft", "save"]


def test_aa_recorded_agrees():
    a, b = rec("aa1"), rec("aa2")
    assert fails(a, b) == [] and fails(b, a) == []


def test_negative_recorded():
    a = rec("aa1")
    assert fails(a, rec("neg_run2")) == sorted(NEG["NegRun"]["expect_fail"])
    assert fails(a, rec("neg_club")) == sorted(NEG["NegClub"]["expect_fail"])


def test_missing_and_crash():
    a = rec("aa1")
    assert P.item_result("perf", a, {"perf": {"error": "no result"}})[0] is False
    assert P.item_result("perf", a, {})[0] is None
    assert P.item_result("log_errors", a, {**a, "alive_at_end": False})[0] is False
    assert P.compare("perf", {"fps_median": 150}, {"fps_median": 106})[0] is True and P.compare("perf", {"fps_median": 150}, {"fps_median": 100})[0] is False


def test_layout_checks(tmp_path):
    ad = A.Adapter.__new__(A.Adapter)
    m = tmp_path / "mod"; m.mkdir()
    assert any("no BepInEx plugin" in p for p in ad.check_layout(m))
    p = m / "Ski"; p.mkdir(); (p / "Ski.csproj").write_text("<Project/>")
    (p / "Plugin.cs").write_text('[BepInPlugin("a.b", "Ski", "0.1")] public class P : BaseUnityPlugin {}')
    assert ad.check_layout(m) == []
    q = m / "NoAttr"; q.mkdir(); (q / "NoAttr.csproj").write_text("<Project/>"); (q / "x.cs").write_text("class X {}")
    r = m / "Empty"; r.mkdir(); (r / "readme.txt").write_text("x")
    (m / "ValheimBridge.dll").write_bytes(b"MZ")
    (m / "worlds").mkdir(); (m / "worlds" / "Hill.fwl").write_bytes(b"w")
    (m / "start.yaml").write_text("mode: race\nworld: Hill\nfoo: 1\nseed: 'a b'\n")
    L = "\n".join(ad.check_layout(m))
    for s in ("NoAttr/: no [BepInPlugin", "Empty/: neither", "ValheimBridge: name taken", "worlds/Hill: a world is two files",
              "mode must be game or menu", "unknown key foo", "seed must be"):
        assert s in L, s


def test_layout_parts(tmp_path):
    m = tmp_path / "mod"; m.mkdir()
    p = m / "Ski"; p.mkdir(); (p / "Ski.csproj").write_text("<Project/>"); (p / "Plugin.cs").write_text("BepInPlugin")
    b = m / "Built"; b.mkdir(); (b / "Built.dll").write_bytes(b"MZ")
    (m / "Loose.dll").write_bytes(b"MZ"); (m / "worlds").mkdir()
    (m / "worlds" / "Hill.fwl").write_bytes(b"a"); (m / "worlds" / "Hill.db").write_bytes(b"b")
    projs, plugs, dlls, worlds, st = A.Adapter._layout(m)
    assert [x.name for x in projs] == ["Ski"] and [x.name for x in plugs] == ["Built"] and [x.name for x in dlls] == ["Loose.dll"]
    assert sorted(w.name for w in worlds) == ["Hill.db", "Hill.fwl"] and st == {}
    assert A.Adapter.check_layout(A.Adapter.__new__(A.Adapter), m) == []


def test_shape_state_and_errors():
    st = A.shape_state({"running": True, "engine": {"screen": "in_game", "player": {"name": "player"}}, "mod": {"objects": {"gab#1": {}}},
                        "screen_text": ["hud: x"] * 40})
    assert list(st) == ["running", "engine", "mod", "screen_text"] and list(st["engine"]) == A.ENGINE_KEYS and len(st["screen_text"]) == 30
    assert st["engine"]["inventory"] == {} and st["engine"]["equipment"] == {} and st["engine"]["mods"] == []
    st = A.shape_state({"running": None, "error": "bridge not answering"}, running=True)
    assert st["running"] and st["engine"]["screen"] == "starting"
    log = ("[Info   :   BepInEx] Loading [ValheimBridge 0.2.0]\n[Error  : Unity Log] DllNotFoundException: Unable to load DLL 'party': libParty.so\n"
           "[Error  :   BepInEx] Could not load [Ski 0.1.0] because it has missing dependencies\n"
           "[Error  :   BepInEx] Could not load [Ski 0.1.0] because it has missing dependencies\n"
           "NullReferenceException: Object reference not set\n  at Ski.Plugin.Update ()\n")
    e = A.error_lines(log)
    assert e == ["[Error  :   BepInEx] Could not load [Ski 0.1.0] because it has missing dependencies (x2)", "NullReferenceException: Object reference not set"]
    g = gbase.read_gate_log(gbase.gate_log(e, "BepInEx did not load: Ski", "tail"))
    assert g["reason"] == "BepInEx did not load: Ski" and g["errors"] == e


def test_runner_scripts_parse():
    for f in ("vhp.sh", "gvh.sh"):
        assert subprocess.run(["bash", "-n", str(gbase.gdir("valheim") / "bridge" / f)]).returncode == 0, f
    t = (gbase.gdir("valheim") / "bridge" / "vhp.sh").read_text()
    assert "VHP_ROOT" in t and "VHP_BEPINEX" in t and "start_game_bepinex.sh" in t
    assert '"$R"/gab/*/game/valheim.x86_64' in t       # stale gab probe games are found and stopped (2026-10-31: they kept the port)
    import ast; ast.parse((gbase.gdir("valheim") / "bridge" / "vhstate.py").read_text())


def test_steam_sandbox_selfcheck_recorded():
    """2026-10-31 gab selfcheck on a Steam sandbox (Steam build 26249040): A/A agree; NegRun's first club hit rolled 9.7 (random skill
    factor at Clubs 0) -- with the widened club_first_hit tolerance only VH-MOVE fails"""
    a, b, n = rec("lq_aa1"), rec("lq_aa2"), rec("lq_neg_run")
    assert fails(a, b) == [] and fails(b, a) == []
    assert n["combat"]["club_hits"][0] < 10 and fails(a, n) == ["VH-MOVE"]
    assert P.compare("combat", a["combat"], {**a["combat"], "club_hits": [37.0, 0, 0]})[0] is False


def _fake_bridge(status, take_input):
    """a ValheimBridge harness channel on a free port (JSON-RPC lines): health + the eval_query answers vhstate.py asks for"""
    import json, socket, socketserver, threading
    ans = {"status": status, "input_state": {"take_input": take_input}, "world": {"world": "gab", "seed": "s1", "time": 2100.0}}

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            r = json.loads(self.rfile.readline())
            res = {"status": "in_world", "fps": 60} if r["method"] == "health" else ans.get(r["params"]["query"], {})
            self.wfile.write((json.dumps({"jsonrpc": "2.0", "id": r["id"], "result": res}) + "\n").encode())

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_vhstate_reports_teleporting(tmp_path):
    """2026-11-12: a mod's Player.TeleportTo that never finished left the game on its black loading screen (all-black replays) while
    the state said in_game; the state now says loading, with player.teleporting / takes_input"""
    import json, sys
    script = gbase.gdir("valheim") / "bridge" / "vhstate.py"
    for tele, screen in ((True, "loading"), (False, "in_game")):
        srv = _fake_bridge({"name": "player", "pos": [1, 2, 3], "teleporting": tele, "day_fraction": 0.55, "time_of_day": "13:12"}, not tele)
        try:
            r = subprocess.run([sys.executable, str(script), str(srv.server_address[1]), str(tmp_path), "-"], capture_output=True, text=True, timeout=30)
        finally: srv.shutdown(); srv.server_close()
        st = A.shape_state(json.loads(r.stdout.strip().splitlines()[-1]))
        e = st["engine"]
        assert e["screen"] == screen and e["player"]["teleporting"] is tele and e["player"]["takes_input"] is (not tele), r.stdout
        assert e["time"]["time_of_day"] == "13:12" and e["world"]["name"] == "gab"

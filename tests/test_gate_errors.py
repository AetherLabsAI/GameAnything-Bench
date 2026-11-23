"""Every gate / `game build` failure shows the actual error text (2026-10-31: Widelands answered only {"rc": 124, "errors": 15}).
The shared helpers, then each open-source adapter's load_check() with fake runners / seats and log fixtures: the log it
returns starts with REASON + ERRORS (gab.game.base.gate_log), which `game build` answers and eval/gate.log keeps. The Widelands
fixtures are real gate runs (2026-10-31); the other logs are short samples in each engine's log format."""
import json, subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from gab.game import base as gbase

DATA = Path(__file__).parent / "data" / "gate_errors"


class Seat:
    """exec answers by the first matching substring of the command (else empty)"""
    sandbox, seat, display, home, lease, info = "box", "1", ":101", "/h", "", {}

    def __init__(self, answers=None): self.answers, self.cmds = answers or {}, []

    def exec(self, cmd, timeout=600, env=None, check=False):
        self.cmds.append(cmd)
        out = next((v for k, v in self.answers.items() if k in cmd), "")
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def put(self, path, data): pass
    def copy_in(self, src, dst): return True


def head(log):
    g = gbase.read_gate_log(log)
    assert g["reason"] and g["errors"], log[:400]
    return g


# ---------------------------------------------------------------------------------------------- shared helpers
def test_error_lines_dedup_order_stamps_cap():
    log = "\n".join(["2026-10-31 03:19:11: ERROR[Main]: boom 1", "[00:00:01.280 real] ERROR: two", "info", "2026-10-31 03:19:12: ERROR[Main]: boom 1",
                     "\x1b[31m[error  ]\x1b[0m red", "ERROR: benign config"] + [f"ERROR: e{i}" for i in range(50)])
    E = gbase.error_lines(log, r"ERROR|\[error", r"benign", n=5)
    assert E[:4] == ["ERROR[Main]: boom 1 (x2)", "ERROR: two", "[error  ] red", "ERROR: e0"] and E[-1] == "... 48 more distinct error lines"
    assert gbase.error_lines("x\nTraceback\n  a\n  b\nz", r"^Traceback", after=2) == ["Traceback", "a", "b"]
    assert len(gbase.error_lines("ERROR " + "x" * 500, "ERROR")[0]) == gbase.ERR_W


def test_gate_log_roundtrip():
    t = gbase.gate_log(["a", "b"], "why\nmore", '{"rc": 1}')
    assert t.startswith("REASON: why / more\nERRORS (2):\n  a\n  b\n---\n")
    assert gbase.read_gate_log(t) == {"reason": "why / more", "errors": ["a", "b"], "detail": '{"rc": 1}'}
    assert gbase.read_gate_log("plain log") == {"reason": None, "errors": [], "detail": "plain log"}
    assert gbase.gate_log([], None, "ok") == "ok"


def test_game_build_answer_shows_errors(tmp_path, monkeypatch, capsys):
    """`game build` (gab.run.control): reason + the error lines whole, the adapter's detail cut"""
    from gab.run import control
    class Ad:
        extra_help = ""; screen = (1, 1)
        def attach(self, inst, mod): pass
        def build(self, inst, mod): return True, "ok: structure"
        def provision(self, inst): pass
        def install_mod(self, inst, mod): pass
        def stop(self, inst): pass
        def load_check(self, inst): return False, gbase.gate_log(["ERROR: x.lua:3: boom"], "the game exited", "D" * 9000)
    (tmp_path / "deliverable" / "mod").mkdir(parents=True)
    monkeypatch.setattr(control, "load_ctx", lambda p: ({"game": "g", "inst": "dev", "workdir": str(tmp_path), "deadline": 9e12}, Ad()))
    control.main(["--ctx", str(tmp_path / "x"), "game", "build"])
    a = json.loads(capsys.readouterr().out)
    assert a["ok"] is False and a["reason"] == "the game exited" and a["errors"] == ["ERROR: x.lua:3: boom"] and len(a["load_log"]) == 2500


def test_eval_gate_record(tmp_path):
    from gab.eval.run import gate_check
    class Ad:
        def __init__(s, b, l): s.b, s.l = b, l
        def build(s, inst, mod): return s.b
        def provision(s, inst): pass
        def install_mod(s, inst, mod): pass
        def stop(s, inst): pass
        def load_check(s, inst): return s.l
    (tmp_path / "mod").mkdir()
    g = gate_check(Ad((True, "ok"), (False, gbase.gate_log(["E1"], "hung", "{}"))), "i", tmp_path / "mod", tmp_path / "e")
    assert g["reason"] == "hung" and g["errors"] == ["E1"] and "REASON: hung" in (tmp_path / "e" / "gate.log").read_text()
    g = gate_check(Ad((False, "start.yaml: no map\nmod/: empty"), None), "i", tmp_path / "mod", tmp_path / "e")
    assert not g["build"] and g["errors"] == ["start.yaml: no map", "mod/: empty"] and "structure" in g["reason"]


# ---------------------------------------------------------------------------------------------- per adapter
def test_voxelibre(monkeypatch):
    from gab.game.voxelibre import adapter as A
    ad = A.Adapter(Seat({"debug.txt": (DATA / "voxelibre_qwen_debug.txt").read_text()}))
    monkeypatch.setattr(ad, "start", lambda inst: (False, "instance eval did not come up: ...tail"))
    monkeypatch.setattr(ad, "I", lambda inst: NS(run_dir="/r", log_errors=lambda: {}))
    g = head(ad.load_check("i")[1])
    assert "mod error" in g["reason"] and g["errors"][0].startswith("ERROR[Main]: ServerError: AsyncErr: Lua: Runtime error from mod 'frozen_fever'")
    assert any("init.lua:125: in function 'set_objective'" in e for e in g["errors"]) and not any("Audio" in e for e in g["errors"])


def test_xonotic(monkeypatch):
    from gab.game.xonotic import adapter as A
    gate = {"ok": False, "alive": False, "bot_alive": True, "map": "stormkeep",
            "_error_lines": ["Host_Error: PRVM_ED_ParseEdict: parse error", "Host_Error: PRVM_ED_ParseEdict: parse error", "QC crash report for server:"]}
    ad = A.Adapter(Seat({"gate.json": json.dumps(gate)})); monkeypatch.setattr(ad, "_py", lambda *a, **k: None)
    g = head(ad.load_check("i")[1])
    assert "server load test failed: the server did not survive 10 s" == g["reason"]
    assert g["errors"] == ["Host_Error: PRVM_ED_ParseEdict: parse error (x2)", "QC crash report for server:"]


def test_flare(monkeypatch):
    from gab.game.flare import adapter as A
    a = {"alive_at_end": True, "crash": False, "errors": []}
    b = {"alive_at_end": False, "crash": True, "maps": [], "rc": -11,
         "errors": ["ERROR: ItemManager: items/items.txt: unknown key 'bonus_xp'", "ERROR: ItemManager: items/items.txt: unknown key 'bonus_xp'",
                    "ERROR: MapParser: maps/arrival.txt: tileset not found"]}
    runs = iter([a, b])
    monkeypatch.setattr(A.P, "FLProbes", lambda *x, **k: NS(setup=lambda: None, game=lambda *x, **k: next(runs), base="/b"))
    ad = A.Adapter(Seat()); ad._mods = ["m"]
    g = head(ad.load_check("i")[1])
    assert "loading a level-1 save into Arrival failed" in g["reason"] and "crashed" in g["reason"]
    assert g["errors"] == ["ERROR: ItemManager: items/items.txt: unknown key 'bonus_xp' (x2)", "ERROR: MapParser: maps/arrival.txt: tileset not found"]


def test_tuxemon(monkeypatch):
    from gab.game.tuxemon import adapter as A, probes as P
    log = ("2026-10-31 10:00:00 - tuxemon.db - ERROR - could not load monster 'fluffy': missing field 'types'\n"
           "Traceback (most recent call last):\n  File \"tuxemon/main.py\", line 10, in main\n    run()\n"
           "  File \"tuxemon/map_loader.py\", line 88, in load\n    tmx = load_pygame(path)\nFileNotFoundError: mods/tuxemon/maps/rome.tmx\n")
    r = {"gate": {"top": "StartState", "maps": {"rome.tmx": {"ok": False, "err": "FileNotFoundError: rome.tmx"}}},
         "log_errors": {"runs": []}, "_error_lines": [f"gate: {x}" for x in P.error_text(log)]}
    monkeypatch.setattr(A.P, "TXProbes", lambda *x, **k: NS(run_all=lambda *x, **k: r, inst="/i"))
    ad = A.Adapter(Seat()); monkeypatch.setattr(ad, "_installed", lambda inst: {"host": [], "maps": ["rome.tmx"]})
    g = head(ad.load_check("i")[1])
    assert "1 map(s) of the mod did not load: rome.tmx" in g["reason"]
    assert g["errors"][1] == 'gate: Traceback: File "tuxemon/map_loader.py", line 88, in load / FileNotFoundError: mods/tuxemon/maps/rome.tmx'


def test_supertuxkart(monkeypatch):
    from gab.game.supertuxkart import adapter as A
    g0 = {"ok": False, "runs": [{"track": "rome", "kart": "tux", "karts_driving": 0, "crashed": True, "not_found": [],
                                 "error_lines": ["[error  ] Track: scene.xml: unknown object 'colosseum'", "[fatal  ] Segmentation fault"]}]}
    ad = A.Adapter(Seat()); monkeypatch.setattr(ad, "_gate", lambda inst, st: g0); monkeypatch.setattr(ad, "_start", lambda inst: {})
    g = head(ad.load_check("i")[1])
    assert g["reason"] == "headless load test failed: rome + tux: crashed, 0/2 karts driving after 8 s"
    assert g["errors"] == ["[error  ] Track: scene.xml: unknown object 'colosseum'", "[fatal  ] Segmentation fault"]


GODOT_LOG = ("Godot Engine v4.7.2.stable\nSCRIPT ERROR: Parse Error: Identifier \"hp\" not declared in the current scope.\n"
             "   at: GDScript::reload (res://player/player.gd:42)\nERROR: Failed to load script \"res://player/player.gd\" with error \"Parse error\".\n"
             "   at: load (modules/gdscript/gdscript.cpp:2936)\n")


def test_godot_error_lines():
    from gab.game import godot_common as GC
    from gab.game.godot_tps import probes as P
    assert GC.error_lines(GODOT_LOG, P.ERR, P.ERR_IGNORE)[:2] == [
        'SCRIPT ERROR: Parse Error: Identifier "hp" not declared in the current scope. @ GDScript::reload (res://player/player.gd:42)',
        'ERROR: Failed to load script "res://player/player.gd" with error "Parse error". @ load (modules/gdscript/gdscript.cpp:2936)']


def test_godot_tps(monkeypatch):
    from gab.game import godot_common as GC
    from gab.game.godot_tps import adapter as A
    gate = {"resources": {"files": 3, "ok": 2, "failed": {"res://player/player.gd": "load failed"}}, "_finished": True, "_exit": 0,
            "_error_lines": GC.error_lines(GODOT_LOG, A.P.ERR, A.P.ERR_IGNORE)}
    monkeypatch.setattr(A.P, "GTProbes", lambda *x, **k: NS(name="n", gate=lambda: gate, cleanup=lambda: None))
    monkeypatch.setattr(A.P, "build_sh", lambda *x, **k: "true")
    g = head(A.Adapter(Seat()).load_check("i")[1])
    assert g["reason"] == "headless load test failed: 1 file(s) of the modded project failed: res://player/player.gd: load failed (exit 0)"
    assert "res://player/player.gd:42" in g["errors"][0]


def test_tof2(monkeypatch):
    from gab.game.tof2 import adapter as A
    errs = A.P.log_errors("SCRIPT ERROR: Invalid access to property 'tiles' on a base object of type 'Nil'.\n   at: load_map (res://scenes/board.gd:77)\n")
    res = {"maps": {"_crashed": "rc=? done=False", "_log_errors": 1, "_error_lines": errs}, "menu": {}, "campaign": {"error": "mission 2 did not start", "_error_lines": errs}}
    monkeypatch.setattr(A.P, "TOFProbes", lambda *x, **k: NS(name="n", build=lambda: None, run=lambda names: res, cleanup=lambda: None))
    g = head(A.Adapter(Seat()).load_check("i")[1])
    assert g["reason"] == "headless load test failed: maps run: did not finish (crashed or hung); campaign run: mission 2 did not start"
    assert g["errors"] == ["SCRIPT ERROR: Invalid access to property 'tiles' on a base object of type 'Nil'. @ load_map (res://scenes/board.gd:77) (x2)"]


def test_colobot(monkeypatch):
    from gab.game.colobot import adapter as A
    runs = [{"name": "stock_custom101", "_lines": ["[ERROR]: Error while parsing scene: line 12: unknown command 'CreateObjet'",
                                                   "[ERROR]: An error occurred while trying to load a level"]}]
    pr = NS(inst="/i", pm="/p", display=":1", runs=runs, _upload_mods=lambda: None,
            stock_levels=lambda sc: {"custom101": {"loaded": False, "alive_after_14s": True, "load_errors": 2}})
    monkeypatch.setattr(A.P, "CBProbes", lambda *x, **k: pr); monkeypatch.setattr(A.P, "build_probe_mod", lambda *x, **k: None)
    ad = A.Adapter(Seat()); monkeypatch.setattr(ad, "_installed", lambda inst: {"start": {"scene": "custom101"}})
    g = head(ad.load_check("i")[1])
    assert g["reason"] == "custom101: the level did not load (level error)" and "unknown command 'CreateObjet'" in g["errors"][0]


def test_celeste64(monkeypatch):
    from gab.game.celeste64 import adapter as A
    blog = ("/h/oss-celeste64/gab/builds/ab/src/Source/Actors/Player.cs(42,13): error CS0103: The name 'hp' does not exist in the current context "
            "[/h/oss-celeste64/gab/builds/ab/src/Celeste64.csproj]\nBuild FAILED.\n"
            "/h/oss-celeste64/gab/builds/ab/src/Source/Actors/Player.cs(42,13): error CS0103: The name 'hp' does not exist in the current context "
            "[/h/oss-celeste64/gab/builds/ab/src/Celeste64.csproj]\n    1 Error(s)\n")
    ad = A.Adapter(Seat()); monkeypatch.setattr(ad, "_mod", lambda inst: {"units": ["/u"], "src": "ab"})
    monkeypatch.setattr(ad, "_binaries", lambda *x: (False, None, blog))
    g = head(ad.load_check("i")[1])
    assert g["reason"] == "the C# build of the mod's Source/ failed"
    assert g["errors"] == ["Source/Actors/Player.cs(42,13): error CS0103: The name 'hp' does not exist in the current context (x2)"]
    # runtime: the game's log + its ErrorLog.txt (2 stack lines kept)
    log = "Unhandled exception. System.NullReferenceException: Object reference not set to an instance of an object.\n   at Celeste64.Player.Update() in Player.cs:line 42\n   at Celeste64.World.Update()\n"
    E = gbase.error_lines(log, A.P.ERR, A.P.ERR_IGNORE, after=2)
    monkeypatch.setattr(ad, "_binaries", lambda *x: (True, "/b", "ok"))
    monkeypatch.setattr(A.P, "prepare_game", lambda *x, **k: None)
    monkeypatch.setattr(A.P, "session", lambda *x, **k: {"alive_at_end": False, "_runs": [{"done": False}], "hub": {}, "_error_lines": E})
    g = head(ad.load_check("i")[1])
    assert "the game crashed or hung" in g["reason"] and g["errors"][1] == "at Celeste64.Player.Update() in Player.cs:line 42"


def test_shot_to_a_path_of_the_game_machine_is_the_agents_error(tmp_path, monkeypatch, capsys):
    """2026-11-07, qwen3.8-flash Minecraft T-IO-afedff0bf3: `game shot /home/player/gab-minecraft/.../07.png` (a path of the
    game's machine) -> PermissionError on /home/player on the host -> harness_bug, systematic: the run started no new trial"""
    from gab.run import control
    class Ad:
        extra_help = ""; screen = (1, 1)
        def attach(self, inst, mod): pass
        def screenshot(self, inst, p): raise PermissionError(13, "Permission denied", "/home/player")
    monkeypatch.setattr(control, "load_ctx", lambda p: ({"game": "g", "inst": "dev", "workdir": str(tmp_path), "deadline": 9e12}, Ad()))
    (tmp_path / "x").mkdir()
    control.main(["--ctx", str(tmp_path / "x" / "ctx.json"), "game", "shot", "/home/player/gab-minecraft/inst/07.png"])
    a = json.loads(capsys.readouterr().out)
    assert "environment_error" not in a and "work dir" in a["error"]
    rec = json.loads((tmp_path / "x" / "calls.jsonl").read_text().splitlines()[-1])
    assert "infra" not in rec
    # any command: an OSError on (a parent of) a path the agent passed is its own mistake
    e = PermissionError(13, "Permission denied", "/home/player")
    assert control.agent_file_error(e, tmp_path, ["/home/player/gab-minecraft/x.png"])
    assert not control.agent_file_error(e, tmp_path, ["relative.png"])

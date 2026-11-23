"""Stardew Valley adapter, offline: deliverable layout / structure checks, error-text extraction (build + SMAPI log), mod load
status, pool ids vs probes, compare on the recorded selfcheck runs (tests/data/stardew_valley: the vanilla A/A pair and the
negative mods of the 2026-10-31 selfcheck, as recorded)."""
import json, shutil
from pathlib import Path

import pytest
import yaml

from gab.game import base as gbase
from gab.game.stardew_valley import adapter as A
from gab.game.stardew_valley import probes as P

D = Path(__file__).parent / "data" / "stardew_valley"
REPO = Path(__file__).resolve().parents[1]
GD = gbase.gdir("stardew_valley")


def rec(n): return json.loads((D / f"{n}.json").read_text())


def items():
    p = yaml.safe_load((REPO / "benchmark" / "pools" / "stardew_valley" / "pool.yaml").read_text())
    return p, [i for i in p["always"] + p["pool"] if i["status"] == "ready"]


def fails(base, got):
    return sorted(i["id"] for i in items()[1] if P.compare_item(i["probe"], base, got)[0] is False)


class FakeSeat:
    sandbox, seat, home, display = "fake", "1", "/home/player", ":101"
    def exec(self, *a, **k): raise AssertionError("no sandbox in unit tests")


def test_registered():
    assert "stardew_valley" in gbase.GAMES and gbase.title("stardew_valley").startswith("Stardew Valley")
    r = A.Adapter(FakeSeat()).requirements()
    assert r["own_copy"] and "Steam" in r["store"] and r["gpu"] is True   # GPU via VirtualGL (2026-11-07)


def test_pool_matches_probes():
    p, its = items()
    names = {i["probe"].split(".")[0] for i in its}
    assert names - {"log_errors"} <= set(P.ORDER)
    ids = {i["id"] for i in its}
    assert set(p["default_picks"]) <= ids and p["game"] == "stardew_valley"
    neg = yaml.safe_load((GD / "negmods" / "negmods.yaml").read_text())
    assert len(neg) >= 2
    for m, spec in neg.items():
        assert set(spec["expect_fail"]) <= ids and A.layout(GD / "negmods" / m) == [GD / "negmods" / m]


def test_layout_and_structure(tmp_path):
    ad = A.Adapter(FakeSeat())
    tpl = GD / "devkit" / "template" / "mod"
    assert [m.name for m in A.layout(tpl)] == ["GabTemplate"]
    # structure problems are reported before anything is built (no sandbox needed)
    m = tmp_path / "mod"; (m / "Bad").mkdir(parents=True); (m / "Bad" / "manifest.json").write_text("{nope")
    r = ad._build(m)
    assert not r["ok"] and "not valid JSON" in r["log"]
    shutil.rmtree(m / "Bad"); (m / "Two").mkdir(); (m / "Two" / "manifest.json").write_text(json.dumps({"Name": "x", "UniqueID": "a.b"}))
    r = ad._build(m)
    assert not r["ok"] and "neither EntryDll" in r["log"]
    (m / "Two" / "manifest.json").write_text(json.dumps({"Name": "x", "UniqueID": "a.b", "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"}}))
    assert "needs content.json" in ad._build(m)["log"]
    assert not ad._build(tmp_path / "empty")["ok"]
    (tmp_path / "s").mkdir(); (tmp_path / "s" / "start.yaml").write_text("screen: title\n")
    assert A.start_screen(tmp_path / "s") == "title" and A.start_screen(tpl) == "game"


def test_tree_hash_ignores_build_output(tmp_path):
    (tmp_path / "a.cs").write_text("x"); h = A.tree_hash(tmp_path)
    (tmp_path / "bin").mkdir(); (tmp_path / "bin" / "a.dll").write_text("y")
    assert A.tree_hash(tmp_path) == h
    (tmp_path / "a.cs").write_text("z"); assert A.tree_hash(tmp_path) != h


BUILD_LOG = """  Determining projects to restore...
/h/gab-stardew/builds/8bc7a4/src/Foo/ModEntry.cs(12,17): error CS0103: The name 'Gamee1' does not exist in the current context [/h/gab-stardew/builds/8bc7a4/src/Foo/Foo.csproj]
/h/gab-stardew/builds/8bc7a4/src/Foo/ModEntry.cs(12,17): error CS0103: The name 'Gamee1' does not exist in the current context [/h/gab-stardew/builds/8bc7a4/src/Foo/Foo.csproj]
/w/src/Foo/ModEntry.cs(20,5): error CS1002: ; expected [/w/src/Foo/Foo.csproj]
CSC : warning CS9057: The analyzer assembly ... [/w/src/Foo/Foo.csproj]
Build FAILED.
"""
SMAPI_LOG = """[10:00:01 INFO  SMAPI] SMAPI 4.5.2 with Stardew Valley 1.6.15 build 24356 on Unix
[10:00:05 TRACE SMAPI]    Foo (from Mods/Foo/Foo.dll, ID: me.Foo, assembly version: 1.0.0)...
[10:00:05 ERROR SMAPI] Skipped mods
[10:00:05 ERROR SMAPI] --------------------------------------------------
[10:00:05 ERROR SMAPI]    These mods could not be added to your game.
[10:00:05 ERROR SMAPI]    - Bar 1.0.0 because its DLL 'Bar.dll' doesn't exist.
[10:00:06 INFO  SMAPI] Loaded 4 mods:
[10:00:06 INFO  SMAPI]    Console Commands 4.5.2 by SMAPI | Adds SMAPI console commands.
[10:00:06 INFO  SMAPI]    Foo 1.0.0 by me | test
[10:00:06 INFO  SMAPI]    Baz 1.0.0 by me | test

[10:00:06 DEBUG SMAPI] Launching mods...
[10:00:06 ERROR Baz] Mod crashed on entry and might not work correctly. Technical details:
NullReferenceException: Object reference not set to an instance of an object.
   at Baz.ModEntry.Entry(IModHelper helper)
[10:00:07 ERROR game] Oops! Steam achievements won't work because Steam isn't loaded. You can launch the game through Steam to fix that.
"""


def test_error_text_is_the_real_lines():
    e = A.build_errors(BUILD_LOG)
    assert sum("CS0103" in x for x in e) == 1 and any("CS1002" in x for x in e) and not any("CS9057" in x for x in e)
    assert e[0] == "mod/Foo/ModEntry.cs(12,17): error CS0103: The name 'Gamee1' does not exist in the current context (x2)"
    r = "\n".join(A.relevant_errors(SMAPI_LOG, "Unhandled exception. System.Exception: boom\n"))
    assert "Bar 1.0.0 because its DLL" in r and "Mod crashed on entry" in r and "NullReferenceException" in r and "at Baz.ModEntry" in r
    assert "Steam isn't loaded" not in r and "stdout: Unhandled exception" in r and "-----" not in r
    st = A.mod_load_status(SMAPI_LOG, [{"folder": "Foo", "name": "Foo"}, {"folder": "Bar", "name": "Bar"}, {"folder": "Baz", "name": "Baz"},
                                       {"folder": "Qux", "name": "Qux"}])
    assert st["Foo"] == "loaded" and st["Bar"].startswith("skipped") and st["Baz"].startswith("failed") and st["Qux"] == "not listed"


def test_build_structure_failure_is_a_gate_log(tmp_path):
    m = tmp_path / "mod"; (m / "Bad").mkdir(parents=True); (m / "Bad" / "manifest.json").write_text("{nope")
    ok, log = A.Adapter(FakeSeat()).build("x", m)
    g = gbase.read_gate_log(log)
    assert not ok and g["reason"] and any("not valid JSON" in e for e in g["errors"])


def test_log_errors_compare():
    b = {"alive": True, "crashed": [], "errors": {"game: Oops! Steam": 1}, "exceptions": 0}
    assert P.compare("log_errors", b, dict(b))[0] is True
    assert P.compare("log_errors", b, {**b, "errors": {**b["errors"], "Foo: boom": 2}})[0] is False
    assert P.compare("log_errors", b, {**b, "alive": False})[0] is False
    assert P.compare("log_errors", b, {"error": "x"})[0] is None


def test_compare_tolerances():
    assert P.compare("clock", {"runs": True, "sec_per_step": 7.0}, {"runs": True, "sec_per_step": 7.6})[0] is True
    assert P.compare("clock", {"runs": True, "sec_per_step": 7.0}, {"runs": True, "sec_per_step": 14.0})[0] is False
    assert P.compare("walk", {"east": 6, "west": 6, "rows": [20, 20, 20]}, {"east": 5, "west": 6, "rows": [20, 20, 20]})[0] is True
    assert P.compare("walk", {"east": 6, "west": 6, "rows": [20, 20, 20]}, {"east": 3, "west": 3, "rows": [20, 20, 20]})[0] is False
    w = {"to_busstop": True, "busstop_tile": [0, 23], "to_farm": True, "farm_tile": [79, 17]}
    assert P.compare("warp_works", w, {**w, "farm_tile": [78, 17]})[0] is True
    assert P.compare("warp_works", w, {**w, "to_farm": False})[0] is False
    assert P.compare_item("player_start.money", {"player_start": {"money": 500, "tile": [9, 9]}},
                          {"player_start": {"money": 500, "tile": [8, 9]}})[0] is True


@pytest.mark.skipif(not (D / "aaA.json").exists(), reason="no recorded selfcheck runs")
def test_recorded_aa_and_negatives():
    a = rec("aaA")
    assert fails(a, rec("aaB")) == []
    neg = yaml.safe_load((GD / "negmods" / "negmods.yaml").read_text())
    for m, spec in neg.items():
        if (D / f"neg_{m}.json").exists():
            f = fails(a, rec(f"neg_{m}"))
            assert set(spec["expect_fail"]) <= set(f) <= set(spec["expect_fail"]) | set(spec.get("may_fail") or []), m


class Installed(Exception): pass


def structure_ok(ad, m):
    """True when _build passes the structure checks (it then calls install(): stopped here, no sandbox)"""
    def stop(*a, **k): raise Installed()
    ad.install = stop
    try: r = ad._build(m)
    except Installed: return True, ""
    return False, r["log"]


def test_content_patcher_packs_structure(tmp_path):
    ad = A.Adapter(FakeSeat())
    tpl = GD / "devkit" / "template" / "cp"
    assert [x.name for x in A.layout(tpl)] == ["GabCPTemplate"]
    mf = json.loads((tpl / "GabCPTemplate" / "manifest.json").read_text())
    assert mf["ContentPackFor"]["UniqueID"] == A.CP_ID and "EntryDll" not in mf and (tpl / "GabCPTemplate" / "content.json").exists()
    assert structure_ok(ad, tpl)[0]
    # a CP pack next to a C# mod (the C# template) passes the structure check too
    m = tmp_path / "mod"; m.mkdir()
    shutil.copytree(tpl / "GabCPTemplate", m / "GabCPTemplate"); shutil.copytree(GD / "devkit" / "template" / "mod" / "GabTemplate", m / "GabTemplate")
    assert structure_ok(ad, m)[0]
    # problems
    def man(**k): return json.dumps({"Name": "x", "UniqueID": "me.x", **k})
    cases = {
        "needs content.json": ("P", man(ContentPackFor={"UniqueID": "Pathoschild.ContentPatcher"}), False),
        "not both": ("P", man(ContentPackFor={"UniqueID": "Pathoschild.ContentPatcher"}, EntryDll="P.dll"), True),
        "which is not installed": ("P", man(ContentPackFor={"UniqueID": "spacechase0.JsonAssets"}), True),
        "name taken": ("ContentPatcher", man(ContentPackFor={"UniqueID": "Pathoschild.ContentPatcher"}), True),
        "is taken": ("P", json.dumps({"Name": "x", "UniqueID": "Pathoschild.ContentPatcher", "EntryDll": "C.dll"}), True),
    }
    for want, (folder, text, content) in cases.items():
        d = tmp_path / want.replace(" ", "_"); (d / folder).mkdir(parents=True)
        (d / folder / "manifest.json").write_text(text)
        if content: (d / folder / "content.json").write_text("{}")
        ok, log = structure_ok(ad, d)
        assert not ok and want in log, (want, log)
    # a pack for another framework that the hand-in brings along is fine
    d = tmp_path / "fw"; (d / "Fw").mkdir(parents=True); (d / "Pk").mkdir()
    (d / "Fw" / "manifest.json").write_text(json.dumps({"Name": "Fw", "UniqueID": "me.Fw", "EntryDll": "Fw.dll"})); (d / "Fw" / "Fw.dll").write_text("x")
    (d / "Pk" / "manifest.json").write_text(man(ContentPackFor={"UniqueID": "me.Fw"}))
    assert structure_ok(ad, d)[0]


CP_LOG = """[09:56:43 INFO  SMAPI] Loaded 4 mods:
[09:56:43 INFO  SMAPI]    Console Commands 4.5.2 by SMAPI | Adds SMAPI console commands that let you manipulate the game.
[09:56:43 INFO  SMAPI]    Content Patcher 2.9.0 by Pathoschild | Loads content packs which edit game data, images, and maps without changing the game files.

[09:56:43 INFO  SMAPI] Loaded 2 content packs:
[09:56:43 INFO  SMAPI]    CP Test Pack 1.0.0 by gab | for Content Patcher | Live check.
[09:56:43 INFO  SMAPI]    CP Broken Pack 1.0.0 by gab | for Content Patcher | Live check: edits an item and adds a dialogue line.

[09:56:43 INFO  SMAPI]    Patched game code
[09:56:44 ERROR Content Patcher] Error preloading content pack 'CP Broken Pack'. Technical details:
Newtonsoft.Json.JsonReaderException: Can't parse JSON file at /home/player/gab-stardew/gab/dev-cp_bad-gate/game/Mods/CPBroken/content.json. This doesn't seem to be valid JSON.
Technical details: After parsing a value an unexpected character was encountered: ". Path 'Changes[0].Target', line 5, position 6.
   at StardewModdingAPI.Toolkit.Serialization.JsonHelper.ReadJsonFileIfExists[TModel](String fullPath, TModel& result) in /home/runner/work/SMAPI/SMAPI/src/SMAPI.Toolkit/Serialization/JsonHelper.cs:line 86
[09:58:10 WARN  Content Patcher] Ignored CP Bad Patch Pack > EditDta: invalid Action value 'EditDta', expected one of: Load, EditImage, EditData, EditMap, Include.
[09:58:10 TRACE game] Window_ClientSizeChanged(); Window.ClientBounds={X:0 Y:0 Width:1280 Height:720}
"""


def test_content_patcher_errors_and_pack_status():
    e = A.cp_errors(CP_LOG)
    assert e[0] == "ERROR Content Patcher: Error preloading content pack 'CP Broken Pack'. Technical details:"
    assert "mod/CPBroken/content.json" in e[1] and "After parsing a value" in e[2] and len(e) == 4 and any(x.startswith("WARN Content Patcher: Ignored CP Bad Patch Pack") for x in e)
    assert A.cp_errors(SMAPI_LOG) == []
    st = A.mod_load_status(CP_LOG, [{"folder": "CPTest", "name": "CP Test Pack"}, {"folder": "CPBroken", "name": "CP Broken Pack"},
                                     {"folder": "Nope", "name": "Nope"}])
    assert st == {"CPTest": "loaded", "CPBroken": "loaded", "Nope": "not listed"}      # SMAPI loads both: CP's lines tell the rest
    assert {"ContentPatcher", "GabBridge"} <= set(A.HARNESS_MODS)


# ---- host `docker`: the user's game folder is bind-mounted read-only (never in the image)
def _fake_game(d, smapi=True):
    d.mkdir(parents=True); (d / "Stardew Valley.dll").write_bytes(b"x"); (d / "Content").mkdir()
    if smapi: (d / "StardewModdingAPI.dll").write_bytes(b"y")
    return d


def test_docker_mounts(tmp_path, monkeypatch):
    monkeypatch.delenv("GAB_STARDEW_GAME_DIR", raising=False); monkeypatch.delenv("GAB_STARDEW_SOURCES_DIR", raising=False)
    with pytest.raises(RuntimeError, match="no game folder configured"): A.docker_mounts("")
    with pytest.raises(RuntimeError, match="does not exist"): A.docker_mounts(str(tmp_path / "nope"))
    g0 = _fake_game(tmp_path / "nosmapi" / "game", smapi=False)
    with pytest.raises(RuntimeError, match="install-smapi.sh"): A.docker_mounts(str(g0))
    g = _fake_game(tmp_path / "sv" / "game")
    assert A.docker_mounts(str(g)) == [(str(g), "/lab/game", "ro")]
    (tmp_path / "sv" / "sd-sources" / "Stardew Valley").mkdir(parents=True)          # decompiled sources next to it -> mounted too
    assert A.docker_mounts(str(g))[1] == (str(tmp_path / "sv" / "sd-sources"), "/lab/sd-sources", "ro")
    monkeypatch.setenv("GAB_STARDEW_GAME_DIR", str(g))
    assert A.Adapter.docker_mounts()[0] == (str(g), "/lab/game", "ro")


def test_source_path_per_host(monkeypatch):
    monkeypatch.setenv("GAB_STARDEW_GAME_DIR", "/host/path/game")
    class B: name = "docker"
    class DSeat(FakeSeat): backend = B()
    assert A.Adapter(DSeat()).src == "/lab/game"                 # docker: the mount point, whatever the host path
    assert A.Adapter(FakeSeat()).src == "/host/path/game"        # pool / local: the path in the sandbox itself
    monkeypatch.setenv("GAB_STARDEW_GAME_DIR", "")     # "" = not configured, whatever this machine's gab.local.json says
    assert A.Adapter(FakeSeat()).src == "/lab/game"
    assert "docker" in A.Adapter(FakeSeat()).requirements()["hosts"]


def test_docker_backend_mounts_game(tmp_path, monkeypatch):
    import sys
    from gab.host import docker as D
    fake = Path(__file__).resolve().parent / "fake_docker.py"
    monkeypatch.setenv("FAKE_DOCKER_DIR", str(tmp_path / "fd")); monkeypatch.setenv("GAB_DOCKER", f"{sys.executable} {fake}")
    man = tmp_path / "images.yaml"
    man.write_text(yaml.safe_dump({"registry": "ghcr.io/example", "tag": "t", "games": {
        "stardew_valley": {"repo": "gameanything-bench-stardew_valley", "digest": "sha256:" + "2" * 64, "gpu": False}}}))
    monkeypatch.setenv("GAB_DOCKER_IMAGES", str(man))
    for k in ("GAB_DOCKER_IMAGE", "GAB_DOCKER_TAG", "GAB_DOCKER_PULL", "GAB_DOCKER_GPUS"): monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GAB_STARDEW_GAME_DIR", "")     # not configured, independent of gab.local.json
    with pytest.raises(RuntimeError, match="no game folder configured"): D.Backend().acquire({"game": "stardew_valley"}, "gab-t-sv")
    g = _fake_game(tmp_path / "sv" / "game"); monkeypatch.setenv("GAB_STARDEW_GAME_DIR", str(g))
    seat = D.Backend().acquire({"game": "stardew_valley", "mounts": [("/h/w", "/h/w", "rw")]}, "gab-t-sv")
    calls = [json.loads(l) for l in (tmp_path / "fd" / "calls.jsonl").read_text().splitlines()]
    a = next(c for c in calls if c[0] == "run")
    assert f"{g}:/lab/game:ro" in a and "/h/w:/h/w:rw" in a and "GAB_SCREEN=1280x720x24" in a and a[a.index("--network") + 1] == "none"
    assert seat.info["mounts"][0] == [str(g), "/lab/game", "ro"]
    assert A.Adapter(seat).src == "/lab/game"
    seat.release()


def test_click_accepts_a_button_name(monkeypatch):
    a = A.Adapter(FakeSeat())
    cmds = []
    monkeypatch.setattr(a, "_x", lambda inst, cmd, timeout=180: cmds.append(cmd))
    a.input("i", {"do": "click", "x": 1, "y": 2, "button": "right"})
    assert "mousedown 3" in cmds[0] and "mouseup 3" in cmds[0]


def test_session_uses_vglrun_when_the_container_has_a_gpu():
    """2026-11-07: Mesa llvmpipe took ~4 of ~6 cores per instance; with a GPU the game runs under vglrun -d egl. A container
    started with --gpus device=N has /dev/nvidiaN (not always nvidia0)"""
    from gab.game.stardew_valley import adapter as A
    s = A.SESSION_SH
    assert 'ls /dev/nvidia[0-9]*' in s and 'VGL="vglrun -d egl"' in s and "$VGL ./StardewModdingAPI" in s
    assert A.Adapter.requirements.__code__ and '"gpu": True' in open(A.__file__).read()

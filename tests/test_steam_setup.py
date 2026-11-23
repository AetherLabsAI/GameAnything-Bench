"""`gab get-game <steam game>` (gab/setup/steamsetup.py) with a fake docker CLI: the terminal login types the account / password /
Steam Guard code into the hub through STDIN only (never an argv), skips when already logged on, retries on a login error; the app
ids go in as appmanifests (EULA asked first); the user-namespace permission; the SSA before the image build; get-game's branch."""
import json, os, sys
from pathlib import Path

import pytest
import yaml

from gab.setup import steamsetup as T
from gab.host import docker as D
from gab.host import steam as S

FAKE = Path(__file__).resolve().parent / "fake_docker.py"
PW = "s3cret-PW!x"


@pytest.fixture
def env(tmp_path, monkeypatch):
    st = tmp_path / "fakedocker"
    monkeypatch.setenv("FAKE_DOCKER_DIR", str(st))
    monkeypatch.setenv("GAB_DOCKER", f"{sys.executable} {FAKE}")
    man = tmp_path / "images.yaml"
    man.write_text(yaml.safe_dump({"registry": "ghcr.io/example", "steam_hub": {"repo": "gameanything-bench-steam", "tag": "local"},
                                   "games": {"valheim": {"repo": "gameanything-bench-steam", "tag": "local", "steam": True}}}))
    monkeypatch.setenv("GAB_DOCKER_IMAGES", str(man))
    sd = tmp_path / "steamstate"
    for d in (sd, sd / "home", sd / "vnc", sd / "relay"): d.mkdir(mode=0o700); os.chmod(d, 0o700)
    monkeypatch.setenv("GAB_STEAM_STATE", str(sd))
    for k in ("GAB_DOCKER_IMAGE", "GAB_DOCKER_TAG", "FAKE_STEAM_STATUS", "FAKE_IMAGE_UID", "FAKE_HUB_VNC", "GAB_STEAM_HUB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    cfg = {}
    monkeypatch.setattr(T, "set_local", lambda k, v: (cfg.__setitem__(k, v), monkeypatch.setenv("GAB_" + k.upper(), str(v))))
    (st).mkdir(parents=True, exist_ok=True)
    (st / "images.json").write_text(json.dumps(["ghcr.io/example/gameanything-bench-steam:local"]))
    (st / "containers.json").write_text(json.dumps({"gab-steam-hub": ["run"]}))           # a running hub
    class E:
        dir = st; state = sd; config = cfg
        def seq(self, *items): (st / "status_seq.json").write_text(json.dumps(list(items)))
        def calls(self): return (st / "calls.jsonl").read_text() if (st / "calls.jsonl").exists() else ""
        def typed(self): return [json.loads(l) for l in (st / "xdotool.jsonl").read_text().splitlines()] if (st / "xdotool.jsonl").exists() else []
        def helper(self): return [json.loads(l) for l in (st / "helper.jsonl").read_text().splitlines()] if (st / "helper.jsonl").exists() else []
    return E()


WIN = ["4194311 800x600+240+100 Sign in to Steam"]
NOT = {"running": True, "logged_on": False, "client": True, "account": None, "windows": WIN}
ON = {"running": True, "logged_on": True, "client": True, "account": "myacct", "remember": True, "windows": []}


def hub(): return S.Hub(D.Backend())


def no(*a, **k): raise AssertionError("must not prompt")


def test_login_types_secrets_through_stdin_only(env):
    env.seq(NOT, NOT, NOT, ON)
    st = T.terminal_login(hub(), ask=lambda q: "myacct", askpass=lambda q: PW, out=lambda *a: None, hidden=lambda p, t: None)
    assert st["logged_on"]
    assert PW not in env.calls() and "myacct" not in env.calls()            # never in a docker argv
    typed = [t["stdin"] for t in env.typed() if t["stdin"] is not None]
    assert typed == ["myacct", PW]                                          # account, then password, both via stdin
    cmds = [t["cmd"] for t in env.typed()]
    assert any("windowfocus --sync 4194311" in c for c in cmds) and any("key --clearmodifiers Return" in c for c in cmds)
    assert any("key --clearmodifiers Tab" in c for c in cmds)


def test_login_skipped_when_logged_on(env):
    env.seq(ON)
    assert T.terminal_login(hub(), ask=no, askpass=no, out=lambda *a: None, hidden=no)["logged_on"]
    assert env.typed() == []


def test_saved_login_waits_for_auto_logon(env):
    saved = dict(NOT, account="myacct", remember=True)
    env.seq(saved, saved, ON)
    assert T.terminal_login(hub(), ask=no, askpass=no, out=lambda *a: None, hidden=no)["logged_on"]


def test_guard_code_typed_through_stdin(env):
    env.seq(NOT, NOT, NOT, NOT, NOT, ON)
    codes = iter(["AB12C"])
    T.terminal_login(hub(), ask=lambda q: "myacct", askpass=lambda q: PW, out=lambda *a: None, hidden=lambda p, t: next(codes, None))
    assert [t["stdin"] for t in env.typed() if t["stdin"]] == ["myacct", PW, "AB12C"]
    assert "AB12C" not in env.calls()


def test_login_error_retries_then_fails(env):
    bad = dict(NOT, login_error="[2026-11-05 10:00:00] [Logon Failed: InvalidPassword]")
    env.seq(NOT, bad)
    msgs = []
    with pytest.raises(SystemExit, match="--vnc"):
        T.terminal_login(hub(), ask=lambda q: "myacct", askpass=lambda q: PW, out=msgs.append, hidden=lambda p, t: None, tries=2)
    assert any("InvalidPassword" in m for m in msgs) and sum("attempt" in m for m in msgs) == 2


def test_remember_off_warns(env):
    env.seq(NOT, dict(ON, remember=False))
    msgs = []
    T.terminal_login(hub(), ask=lambda q: "myacct", askpass=lambda q: PW, out=msgs.append, hidden=lambda p, t: None)
    assert any("Remember me" in m for m in msgs)


def test_install_apps_via_steam_dialogs(env, monkeypatch):
    """Steam's own install path: steam://install/<id>, Install clicked, the EULA only after the user's yes, confirmed by the manifest"""
    clicks = []
    monkeypatch.setattr(T, "click_dialog", lambda hub, before, timeout=40, finder=None: clicks.append(timeout) or {"id": "9", "w": 500, "h": 300, "name": ""})
    monkeypatch.setattr(T, "_windows", lambda st: [])
    q = lambda **apps: dict(ON, apps=apps)
    env.seq(q(), q(), q(), q(**{"105600": {"state": "1026"}}), q(**{"105600": {"state": "1026"}}),
            q(**{"105600": {"state": "1026"}, "1281930": {"state": "1026"}}),
            q(**{"105600": {"installed": True}, "1281930": {"state": "1026", "bytes_done": "1", "bytes_total": "9"}}),
            q(**{"105600": {"installed": True}, "1281930": {"installed": True}}))
    asked = []
    T.install_apps(hub(), "terraria", ask=lambda x: (asked.append(x), "y")[1], out=lambda *a: None)
    h = env.helper()
    assert ["install", "105600"] in h and ["install", "1281930"] in h
    assert not any(x[0] in ("manifest", "restart") for x in h)            # the dialogs worked: no fallback
    assert len(asked) == 1 and "tModLoader EULA" in asked[0] and "1281930_eula" in asked[0]


def test_install_falls_back_to_manifest(env, monkeypatch):
    monkeypatch.setattr(T, "click_dialog", lambda *a, **k: None)          # no dialog came up
    monkeypatch.setattr(T, "_windows", lambda st: [])
    env.seq(*[dict(ON, apps={})] * 35, dict(ON, apps={"892970": {"installed": True}}))
    T.install_apps(hub(), "valheim", ask=no, out=lambda *a: None)
    h = env.helper()
    assert ["manifest", "892970 Valheim Valheim"] in h and ["restart", ""] in h


def test_install_refused_eula(env):
    env.seq(dict(ON, apps={}))
    with pytest.raises(SystemExit, match="EULA"):
        T.install_apps(hub(), "terraria", ask=lambda q: "n", out=lambda *a: None)
    assert not any(x[0] == "install" and x[1] == "1281930" for x in env.helper())


def test_install_skips_installed(env):
    env.seq(dict(ON, apps={"892970": {"installed": True}}))
    T.install_apps(hub(), "valheim", ask=no, out=lambda *a: None)
    assert not any(x[0] in ("manifest", "restart", "install") for x in env.helper())


def test_primary_button_found_on_screenshot(tmp_path):
    """the largest blue-gradient button of a dialog (Steam's Install / Accept / Sign in), not the blue label text"""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1024, 768), (20, 22, 28)); d = ImageDraw.Draw(im)
    d.rectangle((300, 200, 800, 600), fill=(35, 38, 46))
    d.text((320, 220), "SIGN IN WITH ACCOUNT NAME", fill=(26, 159, 255))
    for x in range(560, 760):                                              # the gradient button
        d.line((x, 540, x, 580), fill=(int(11 + (x - 560) * 0.15), int(191 - (x - 560) * 0.5), 255))
    png = tmp_path / "s.png"; im.save(png)
    x, y = T.primary_button(str(png), (300, 200, 500, 400))
    assert 640 <= x <= 680 and 555 <= y <= 565
    assert T.primary_button(str(png), (300, 200, 200, 100)) is None        # a region without a button


def test_userns_sudo_profile(env, tmp_path, monkeypatch):
    monkeypatch.setattr(T, "ETC", tmp_path / "etc"); monkeypatch.setattr(T, "apparmor_enabled", lambda: True)
    for k in ("GAB_STEAM_HUB_SECCOMP", "GAB_STEAM_HUB_APPARMOR"): monkeypatch.setenv(k, "")
    ran = []
    r = T.ensure_userns(ask=lambda q: "", out=lambda *a: None, run=lambda c: (ran.append(c), type("R", (), {"returncode": 0}))[1])
    assert r == "profile installed" and env.config == {"steam_hub_seccomp": T.SECCOMP, "steam_hub_apparmor": "gab-steam-hub"}
    assert ran[0][:2] == ["sudo", "install"] and ran[1][:2] == ["sudo", "apparmor_parser"]


def test_userns_no_sudo_falls_back_after_asking(env, tmp_path, monkeypatch):
    monkeypatch.setattr(T, "ETC", tmp_path / "etc"); monkeypatch.setattr(T, "apparmor_enabled", lambda: True)
    for k in ("GAB_STEAM_HUB_SECCOMP", "GAB_STEAM_HUB_APPARMOR"): monkeypatch.setenv(k, "")
    answers = iter(["y", "y"])
    r = T.ensure_userns(ask=lambda q: next(answers), out=lambda *a: None, run=lambda c: type("R", (), {"returncode": 1}))
    assert r == "unconfined" and env.config["steam_hub_apparmor"] == "unconfined"


def test_userns_already_configured(env, monkeypatch):
    monkeypatch.setenv("GAB_STEAM_HUB_SECCOMP", T.SECCOMP); monkeypatch.setenv("GAB_STEAM_HUB_APPARMOR", "gab-steam-hub")
    assert T.ensure_userns(ask=no, out=lambda *a: None, run=no) == "configured"


def test_image_needs_ssa(env, monkeypatch):
    (env.dir / "images.json").write_text("[]")
    runs = []
    monkeypatch.setattr(T, "_build", lambda *a, **k: runs.append((a, k)))
    with pytest.raises(SystemExit, match="Subscriber"):
        T.ensure_image(hub(), ask=lambda q: "", out=lambda *a: None)
    assert runs == []
    def build(*a, **k):
        runs.append((a, k)); (env.dir / "images.json").write_text(json.dumps(["ghcr.io/example/gameanything-bench-steam:local"]))
    monkeypatch.setattr(T, "_build", build)
    T.ensure_image(hub(), ask=lambda q: "y", out=lambda *a: None)
    assert runs and runs[0][1]["env"]["STEAM_ACCEPT_SSA"] == "yes" and runs[0][1]["env"]["TAG"] == "local"


def test_get_game_steam_branch(monkeypatch):
    from gab.setup import getgame
    got = []
    monkeypatch.setattr(T, "setup_game", lambda g, do_check=True: got.append((g, do_check)))
    monkeypatch.setattr(getgame, "set_local", lambda k, v: None)
    getgame.get_game("terraria,valheim", do_check=False)
    assert got == [("terraria", False), ("valheim", False)]


def test_setup_game_rerun_skips_everything(env, monkeypatch):
    """a machine where all is done (after the user's login): no prompt, no install, no restart"""
    monkeypatch.setenv("GAB_STEAM_HUB_SECCOMP", T.SECCOMP); monkeypatch.setenv("GAB_STEAM_HUB_APPARMOR", "gab-steam-hub")
    env.seq(dict(ON, apps={"892970": {"installed": True}}))
    T.setup_game("valheim", do_check=False, ask=no, askpass=no, out=lambda *a: None)
    assert not any(x[0] in ("install", "manifest", "restart", "stop") for x in env.helper())


def test_setup_game_restarts_a_vnc_hub(env, monkeypatch):
    monkeypatch.setenv("GAB_STEAM_HUB_SECCOMP", T.SECCOMP); monkeypatch.setenv("GAB_STEAM_HUB_APPARMOR", "gab-steam-hub")
    monkeypatch.setenv("FAKE_HUB_VNC", "1")
    env.seq(dict(ON, apps={"892970": {"installed": True}}))
    T.setup_game("valheim", do_check=False, ask=no, askpass=no, out=lambda *a: None)
    assert ["stop", ""] in env.helper()
    runs = [json.loads(l) for l in env.calls().splitlines() if json.loads(l)[0] == "run"]
    assert runs and "GAB_STEAM_VNC=0" in runs[-1]


def test_setup_game_redeploys_an_old_hub(env, monkeypatch):
    """a hub started by an older gab (no /opt/gab-steam mount: its helper lacks fields): restarted before its status is trusted"""
    monkeypatch.setenv("GAB_STEAM_HUB_SECCOMP", T.SECCOMP); monkeypatch.setenv("GAB_STEAM_HUB_APPARMOR", "gab-steam-hub")
    monkeypatch.setenv("FAKE_HUB_OLD", "1")
    env.seq(dict(ON, apps={"892970": {"installed": True}}))
    T.setup_game("valheim", do_check=False, ask=no, askpass=no, out=lambda *a: None)
    assert ["stop", ""] in env.helper()
    runs = [json.loads(l) for l in env.calls().splitlines() if json.loads(l)[0] == "run"]
    assert runs and any(x.endswith(f":{S.SCRIPTS}:ro") for x in runs[-1])

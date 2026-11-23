"""Cities: Skylines' one-time Legal Disclaimer step (gab/setup/citieslegal.py) without Steam: the acceptance test on colossal.cgs (same
rule as the adapter's _legal_ok), the skip when accepted, the one acceptance seat (network=bridge, library rw, noVNC published on
host 127.0.0.1 only, x11vnc -localhost with a password file, the game from the library with cs-start.sh), waiting without ever
clicking, the teardown on success / timeout / error, the old-image check; the images.yaml entry, the noVNC packages, the dotnet
XDG_RUNTIME_DIR fix in the adapter, and setup_game's call of the step for cities_skylines only."""
import importlib.util, json, os, socket, types
from pathlib import Path

import pytest
import yaml

from gab.setup import citieslegal as L
from gab.host import steam as S

REPO = Path(__file__).resolve().parent.parent
PRIV, TERMS = "legal.privacy.privacy-policy-2018-11-02", "legal.terms-of-use.Terms-of-use-2019-04-05"


def cgs():
    spec = importlib.util.spec_from_file_location("cgs_t", REPO / "gab" / "game" / "cities_skylines" / "bridge" / "cgs.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def write_cgs(path, bools):
    path.parent.mkdir(parents=True, exist_ok=True)
    cgs().write(path, {"_version": 4, "bool": {"TelemetryConsent": True, **bools}})


def test_accepted_rule(tmp_path):
    p = tmp_path / "colossal.cgs"
    assert not L.accepted(p)                                               # no file
    write_cgs(p, {PRIV: True}); assert not L.accepted(p)                    # one document only
    write_cgs(p, {PRIV: True, TERMS: False}); assert not L.accepted(p)      # a false key is no acceptance
    write_cgs(p, {PRIV: True, TERMS: True}); assert L.accepted(p)
    assert L.accepted_keys(p) == [PRIV, TERMS]
    p.write_bytes(b"NOPE"); assert not L.accepted(p)                        # unreadable: not accepted
    assert L.legal_prefixes() == ("legal.privacy.", "legal.terms-of-use.")  # the adapter's own rule


def test_cgs_path_is_the_library_file(tmp_path):
    assert L.cgs_path(tmp_path) == tmp_path / "home" / S.LIB / "Cities_Skylines" / "colossal.cgs"


def test_free_port_is_free_on_localhost():
    p = L.free_port()
    s = socket.socket(); s.bind(("127.0.0.1", p)); s.close()
    assert p >= 6090


class FakeSeat:
    def __init__(self, log): self.log = log; self.home = "/home/player"; self.display = ":101"; self.sandbox = "gab-gab-cities-legal-x"
    def put(self, path, data): self.log.append(("put", path, data[:40]))
    def exec(self, cmd, timeout=600, env=None, check=False):
        self.log.append(("exec", cmd)); return types.SimpleNamespace(returncode=0, stdout="started\n", stderr="")
    def release(self): self.log.append(("release",))


class FakeBackend:
    def __init__(self, novnc=True): self.novnc = novnc; self.runs = []
    def _run(self, args, timeout=600, **kw):
        self.runs.append(args); return types.SimpleNamespace(returncode=0 if self.novnc else 1, stdout="ok\n" if self.novnc else "", stderr="")
    def has_image(self, ref): return True


def fake_hub(tmp_path, novnc=True):
    (tmp_path / "home" / S.LIB / "Cities_Skylines").mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(st=tmp_path, b=FakeBackend(novnc))


@pytest.fixture(autouse=True)
def image(monkeypatch): monkeypatch.setattr(S, "hub_image", lambda b: "ghcr.io/example/gameanything-bench-steam:local")


def test_skipped_when_already_accepted(tmp_path):
    hub = fake_hub(tmp_path); write_cgs(L.cgs_path(tmp_path), {PRIV: True, TERMS: True})
    assert L.run(hub, out=lambda *a: None, acquire=lambda: pytest.fail("no container when accepted")) == "accepted before"
    assert hub.b.runs == []                                                   # not even the image check


def test_acceptance_flow(tmp_path, monkeypatch):
    for k in ("GAB_DOCKER_NETWORK", "GAB_STEAM_LIBRARY_RW"): monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GAB_DOCKER_RUN_ARGS", "--cpus 4")
    hub = fake_hub(tmp_path); log = []; seen = {}; outs = []
    def acquire():
        seen.update({k: os.environ.get(k) for k in ("GAB_DOCKER_NETWORK", "GAB_STEAM_LIBRARY_RW", "GAB_DOCKER_RUN_ARGS")})
        return FakeSeat(log)
    polls = []
    def sleep(s):                                                             # the user clicks Agree on both after two polls
        polls.append(s)
        if len(polls) == 2: write_cgs(L.cgs_path(tmp_path), {PRIV: True, TERMS: True})
    r = L.run(hub, out=outs.append, acquire=acquire, sleep=sleep, port=6091, password="pw123456", timeout_s=600)
    assert r == "accepted" and len(polls) == 2
    # the one acceptance seat: a network, the library writable, noVNC on HOST 127.0.0.1 only
    assert seen == {"GAB_DOCKER_NETWORK": "bridge", "GAB_STEAM_LIBRARY_RW": "1", "GAB_DOCKER_RUN_ARGS": "--cpus 4 -p 127.0.0.1:6091:6080"}
    assert os.environ.get("GAB_DOCKER_NETWORK") is None and os.environ.get("GAB_STEAM_LIBRARY_RW") is None   # restored for trials
    assert os.environ["GAB_DOCKER_RUN_ARGS"] == "--cpus 4"
    assert ("exec", "chmod +x /tmp/gab-legal/cs-start.sh") == next((x[0], x[1]) for x in log if x[0] == "exec" and "chmod" in x[1])
    cmd = next(x[1] for x in log if x[0] == "exec" and "x11vnc" in x[1])
    assert "x11vnc -display :101 -localhost" in cmd and "-passwdfile /tmp/gab-legal/pass" in cmd and "pw123456" in cmd
    assert "websockify --web /usr/share/novnc 0.0.0.0:6080 localhost:5900" in cmd
    assert "GAME_DIR=/home/player/.steam/debian-installation/steamapps/common/Cities_Skylines" in cmd and "cs-start.sh" in cmd
    assert "xdotool" not in cmd and "click" not in cmd and "legal." not in cmd           # gab never clicks, never writes a key
    assert ("put", "/tmp/gab-legal/cs-start.sh") == next(x[:2] for x in log if x[0] == "put")
    assert log[-1] == ("release",)
    txt = "\n".join(map(str, outs))
    assert "Forward port 6091" in txt and "http://localhost:6091/vnc.html" in txt and "ssh -L 6091:localhost:6091" in txt
    assert "Agree on BOTH" in txt


def test_timeout_tears_down(tmp_path, monkeypatch):
    hub = fake_hub(tmp_path); log = []
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(L.time, "time", lambda: next(clock))
    with pytest.raises(SystemExit, match="not accepted within"):
        L.run(hub, out=lambda *a: None, acquire=lambda: FakeSeat(log), sleep=lambda s: None, port=6092, password="x", timeout_s=600)
    assert log[-1] == ("release",)


def test_start_failure_tears_down(tmp_path):
    hub = fake_hub(tmp_path); log = []
    class Bad(FakeSeat):
        def exec(self, cmd, **k): return types.SimpleNamespace(returncode=1, stdout="", stderr="no x11vnc")
    with pytest.raises(SystemExit, match="did not start"):
        L.run(hub, out=lambda *a: None, acquire=lambda: Bad(log), sleep=lambda s: None, port=6093, password="x")
    assert log[-1] == ("release",)


def test_old_image_without_novnc(tmp_path):
    hub = fake_hub(tmp_path, novnc=False)
    with pytest.raises(SystemExit, match="predates noVNC"):
        L.run(hub, out=lambda *a: None, acquire=lambda: pytest.fail("no seat"), sleep=lambda s: None)
    built = []
    hub2 = fake_hub(tmp_path / "b", novnc=False); log = []
    def sleep(s): write_cgs(L.cgs_path(tmp_path / "b"), {PRIV: True, TERMS: True})
    assert L.run(hub2, out=lambda *a: None, acquire=lambda: FakeSeat(log), sleep=sleep, rebuild=lambda: built.append(1),
                 port=6094, password="x") == "accepted"
    assert built == [1]
    assert hub.b.runs[0][:4] == ["run", "--rm", "--network", "none"]          # the image check itself runs offline


def test_not_installed(tmp_path):
    hub = types.SimpleNamespace(st=tmp_path, b=FakeBackend())
    with pytest.raises(SystemExit, match="not installed"): L.run(hub, out=lambda *a: None, acquire=lambda: pytest.fail("no seat"))


def test_image_entry_packages_and_build_fix():
    m = yaml.safe_load((REPO / "docker" / "images.yaml").read_text())
    c = m["games"]["cities_skylines"]
    assert c["steam"] is True and c["repo"] == m["games"]["valheim"]["repo"] and c.get("tag") == "local"
    assert "novnc websockify" in (REPO / "docker" / "steam" / "install.sh").read_text()
    a = (REPO / "gab" / "game" / "cities_skylines" / "adapter.py").read_text()
    assert 'mkdir -p -m 700 \\"$XDG_RUNTIME_DIR\\"' in a                      # dotnet's NuGet-Migrations mutex needs it to exist


def test_trials_stay_offline():
    from gab import config
    assert config.DEFAULTS["docker_network"] == "none"
    assert "bridge" not in (REPO / "gab" / "host" / "docker.py").read_text().split("def acquire")[1].split("def exec")[0]


def test_setup_game_runs_the_step_for_cities_only(monkeypatch):
    from gab.setup import steamsetup as T
    calls = []
    import gab.setup.citieslegal as mod
    monkeypatch.setattr(mod, "run", lambda hub, out, rebuild=None: calls.append(hub) or "accepted before")
    hub = types.SimpleNamespace(status=lambda: {"account": "a", "logged_on": True}, has_vnc=lambda: False)
    for f in ("ensure_pydeps", "ensure_image", "ensure_hub", "terminal_login", "install_apps"): monkeypatch.setattr(T, f, lambda *a, **k: None)
    monkeypatch.setattr(T, "ensure_userns", lambda *a, **k: "configured")
    monkeypatch.setattr(T, "prep_game", lambda *a, **k: "not needed")
    monkeypatch.setattr(T.S, "Hub", lambda b: types.SimpleNamespace(**vars(hub), exec=lambda c, **k: types.SimpleNamespace(stdout="ok")))
    T.setup_game("valheim", do_check=False, out=lambda *a: None); assert calls == []
    T.setup_game("cities_skylines", do_check=False, out=lambda *a: None); assert len(calls) == 1

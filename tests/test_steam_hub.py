"""Steam on host `docker` (gab/host/steam.py): trial containers of Steam games use the machine's one Steam hub through its IPC,
keep --network none and get the library read only; the hub itself; the state-dir checks. Fake docker CLI, no Engine, no Steam."""
import json, os, sys
from pathlib import Path

import pytest
import yaml

from gab.host import docker as D
from gab.host import steam as S

FAKE = Path(__file__).resolve().parent / "fake_docker.py"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    st = tmp_path / "fakedocker"
    monkeypatch.setenv("FAKE_DOCKER_DIR", str(st))
    monkeypatch.setenv("GAB_DOCKER", f"{sys.executable} {FAKE}")
    man = tmp_path / "images.yaml"
    man.write_text(yaml.safe_dump({"registry": "ghcr.io/example", "tag": "t", "steam_hub": {"repo": "gameanything-bench-steam", "tag": "local"},
                                   "games": {"flare": {"repo": "gameanything-bench-flare", "gpu": False},
                                             "valheim": {"repo": "gameanything-bench-steam", "tag": "local", "gpu": True, "steam": True}}}))
    monkeypatch.setenv("GAB_DOCKER_IMAGES", str(man))
    sd = tmp_path / "steamstate"
    for d in (sd, sd / "home", sd / "vnc", sd / "relay"): d.mkdir(mode=0o700); os.chmod(d, 0o700)
    monkeypatch.setenv("GAB_STEAM_STATE", str(sd))
    for k in ("GAB_DOCKER_IMAGE", "GAB_DOCKER_TAG", "GAB_DOCKER_GPUS", "GAB_DOCKER_STEAM", "GAB_STEAM_HUB_SECCOMP", "GAB_STEAM_HUB_APPARMOR", "GAB_STEAM_REQUIRE_LOGON",
              "GAB_STEAM_HUB", "GAB_STEAM_HUB_IMAGE", "FAKE_STEAM_STATUS", "FAKE_IMAGE_UID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GAB_DOCKER_GPUS", "none")
    class F:
        dir = st; state = sd
        def calls(self): return [json.loads(l) for l in (st / "calls.jsonl").read_text().splitlines()] if (st / "calls.jsonl").exists() else []
        def runs(self): return [c for c in self.calls() if c[0] == "run"]
    return F()


def start_hub(fake):
    h = S.Hub(D.Backend()); assert h.up() == "started"; return h


def test_hub_run_args(fake):
    h = start_hub(fake); a = fake.runs()[-1]
    assert a[a.index("--name") + 1] == "gab-steam-hub" and a[-1] == "ghcr.io/example/gameanything-bench-steam:local"
    assert a[a.index("--ipc") + 1] == "shareable" and a[a.index("--network") + 1] == "bridge"       # the hub alone has network
    assert f"{fake.state}/home:{S.CPATH}" in a and f"{fake.state}/vnc:{S.VNC_DIR}" in a and f"HOME={S.CPATH}" in a and "GAB_STEAM_VNC=0" in a
    assert f"{D.config.REPO / 'docker/steam'}:{S.SCRIPTS}:ro" in a and a[a.index("--entrypoint") + 1] == f"{S.SCRIPTS}/gab-steam-hub"
    assert "no-new-privileges" in a and "--privileged" not in a and not any("SYS_ADMIN" in x for x in a)
    assert h.up() == "already running"


def test_steam_trial_uses_hub_ipc_and_library_ro(fake):
    start_hub(fake)
    seat = D.Backend().acquire({"game": "valheim"}, "gab-t-vh")
    a = fake.runs()[-1]
    assert a[a.index("--network") + 1] == "none"                                   # the game / agent side stays offline
    assert a[a.index("--ipc") + 1] == "container:gab-steam-hub"
    h = f"{fake.state}/home"
    assert f"{h}/{S.LIB}:/home/player/{S.LIB}:ro" in a
    assert f"{h}/{S.CLIENT_SO}:/home/player/.steam/sdk64/steamclient.so:ro" in a
    assert not any("/config" in x or x.endswith(f"{h}:/home/player/.steam-state") for x in a)      # never the login token
    assert a[a.index("--pid") + 1] == "container:gab-steam-hub"                   # Steam watches the game's pid
    assert f"BASH_ENV={S.OWN_PROC_BASH_ENV}" in a                                   # kill builtin: this container only
    assert f"{fake.state}/relay:{S.RELAY_TRIAL}:ro" in a and f"GAB_STEAM_RELAY_DIR={S.RELAY_TRIAL}" in a
    assert seat.info["steam_hub"] == "gab-steam-hub"
    setup = [c for c in fake.calls() if c[0] == "exec" and "steam.pid" in c[-1]]
    assert setup and "echo 1 > $S/steam.pid" in setup[0][-1] and setup[0][1:3] == ["-u", "0"]
    assert "runuser -u player -- setsid nohup $R trial" in setup[0][-1]
    assert f"{D.config.REPO / 'docker/steam/gab-steam-relay'}:{S.SCRIPTS}/gab-steam-relay:ro" in a      # the relay of this checkout


def test_hub_security_opts_only_when_configured(fake, monkeypatch):
    """Steam needs user namespaces; loosening seccomp / AppArmor is the operator's explicit choice, and for the hub only"""
    monkeypatch.setenv("GAB_STEAM_HUB_SECCOMP", ""); monkeypatch.setenv("GAB_STEAM_HUB_APPARMOR", "")   # not this machine's gab.local.json
    start_hub(fake); a = fake.runs()[-1]
    assert not any(x.startswith(("seccomp=", "apparmor=")) for x in a)
    S.Hub(D.Backend()).down()
    monkeypatch.setenv("GAB_STEAM_HUB_SECCOMP", "docker/steam/seccomp-steam.json"); monkeypatch.setenv("GAB_STEAM_HUB_APPARMOR", "gab-steam-hub")
    start_hub(fake); a = fake.runs()[-1]
    assert f"seccomp={D.config.REPO / 'docker/steam/seccomp-steam.json'}" in a and "apparmor=gab-steam-hub" in a
    D.Backend().acquire({"game": "valheim"}, "gab-t-vh"); t = fake.runs()[-1]
    assert not any(x.startswith(("seccomp=", "apparmor=")) for x in t)


def test_refuses_without_hub_or_logon(fake, monkeypatch):
    with pytest.raises(RuntimeError, match="hub, which is not running"):
        D.Backend().acquire({"game": "valheim"}, "gab-t-vh")
    start_hub(fake)
    monkeypatch.setenv("FAKE_STEAM_STATUS", json.dumps({"running": True, "logged_on": False, "client": True}))
    monkeypatch.setenv("GAB_STEAM_LOGON_WAIT_MIN", "0")                            # no wait: refuse at once
    with pytest.raises(RuntimeError, match="logged on"):
        D.Backend().acquire({"game": "valheim"}, "gab-t-vh")
    monkeypatch.setenv("GAB_STEAM_REQUIRE_LOGON", "0")                             # pre-login plumbing tests only
    D.Backend().acquire({"game": "valheim"}, "gab-t-vh")


def test_waits_for_the_operator_to_log_in_again(fake, monkeypatch):
    """2026-11-08: the hub fell off its login mid-run and every queued Valheim trial failed within seconds; now a trial
    waits (steam_logon_wait_min) and starts once the hub is logged on again"""
    from gab.host import steam
    start_hub(fake)
    monkeypatch.setenv("FAKE_STEAM_STATUS", json.dumps({"running": True, "logged_on": False, "client": True}))
    calls = []; free = []
    real = steam.Hub.status
    def status(self):
        import fcntl                                   # the machine-wide GPU lock must be free while a trial waits for Steam
        with open(D.Backend.GPU_LOCK, "a+") as f:
            if len(calls) < 2:                          # still logged off: waiting (trial_args checks once more under the lock)
                try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); free.append(True); fcntl.flock(f, fcntl.LOCK_UN)
                except OSError: free.append(False)
        calls.append(1)
        if len(calls) >= 3: monkeypatch.setenv("FAKE_STEAM_STATUS", json.dumps({"running": True, "logged_on": True, "client": True}))
        return real(self)
    monkeypatch.setattr(steam.Hub, "status", status)
    monkeypatch.setattr("time.sleep", lambda s: None)
    D.Backend().acquire({"game": "valheim"}, "gab-t-vh")                           # waited, then started
    assert len(calls) >= 3 and "--ipc" in fake.runs()[-1]
    assert all(free), "waited for the Steam login while holding docker.py's machine-wide lock"


def test_non_steam_game_and_switch_off(fake, monkeypatch):
    D.Backend().acquire({"game": "flare"}, "gab-t-fl"); a = fake.runs()[-1]
    assert "--ipc" not in a and not any("steam" in x for x in a)
    monkeypatch.setenv("GAB_DOCKER_STEAM", "off")
    D.Backend().acquire({"game": "valheim"}, "gab-t-vh"); a = fake.runs()[-1]
    assert "--ipc" not in a


def test_state_dir_checks(tmp_path):
    p = tmp_path / "s"; assert "does not exist" in S.check_state_dir(p)[0]
    p.mkdir(); os.chmod(p, 0o755); assert "must be 700" in S.check_state_dir(p)[0]
    os.chmod(p, 0o700); assert S.check_state_dir(p) == []


def test_hub_refuses_open_state_dir(fake):
    os.chmod(fake.state, 0o750)
    with pytest.raises(RuntimeError, match="700"): S.Hub(D.Backend()).up()


def test_probe_script_without_lib(tmp_path, monkeypatch):
    """steam_probe.py outside any container: no libsteam_api -> a clear JSON error, exit 2"""
    import subprocess
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.delenv("GAB_STEAM_API_LIB", raising=False)
    r = subprocess.run([sys.executable, str(Path(S.__file__).parent / "steam_probe.py"), "--appid", "480"], capture_output=True, text=True)
    assert r.returncode == 2 and "no 64-bit libsteam_api.so" in json.loads(r.stdout)["error"]


def test_hub_refuses_image_of_another_uid(fake, monkeypatch):
    """the image's game user is written into the host state dir: it must be the caller, never another host account"""
    monkeypatch.setenv("FAKE_IMAGE_UID", "1000" if os.getuid() != 1000 else "1001")
    with pytest.raises(RuntimeError, match="player uid"): S.Hub(D.Backend()).up()
    assert not fake.runs()


def test_init_state_modes(fake, tmp_path, monkeypatch):
    sd = tmp_path / "fresh"; monkeypatch.setenv("GAB_STEAM_STATE", str(sd))
    S.Hub(D.Backend()).init_state()
    for d in (sd, sd / "home", sd / "vnc", sd / "relay"):
        assert oct(d.stat().st_mode & 0o777) == "0o700" and d.stat().st_uid == os.getuid()


def _echo_server(sock):
    import threading
    def run():
        while True:
            try: c, _ = sock.accept()
            except OSError: return
            d = c.recv(100); c.sendall(b"echo:" + d); c.close()
    threading.Thread(target=run, daemon=True).start()


def test_relay_both_halves(tmp_path):
    """docker/steam/gab-steam-relay: hub half = unix socket -> 127.0.0.1:port of the Steam client; trial half = 127.0.0.1:port -> unix socket"""
    import socket, subprocess, time
    relay = Path(S.__file__).resolve().parents[2] / "docker" / "steam" / "gab-steam-relay"
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5); port = srv.getsockname()[1]; _echo_server(srv)
    d = tmp_path / "relay"
    hub = subprocess.Popen([sys.executable, str(relay), "hub", str(d)], env={**os.environ, "GAB_STEAM_RELAY_PORTS": str(port)})
    try:
        for _ in range(50):
            if (d / "ports").exists() and str(port) in (d / "ports").read_text(): break
            time.sleep(0.1)
        assert oct((d / f"{port}.sock").stat().st_mode & 0o777) == "0o700"
        u = socket.socket(socket.AF_UNIX); u.connect(str(d / f"{port}.sock")); u.sendall(b"hi"); assert u.recv(100) == b"echo:hi"
    finally: hub.kill()
    # trial half: a free port -> a unix echo server
    t = tmp_path / "t"; t.mkdir(); us = socket.socket(socket.AF_UNIX)
    free = socket.socket(); free.bind(("127.0.0.1", 0)); p2 = free.getsockname()[1]; free.close()
    us.bind(str(t / f"{p2}.sock")); us.listen(5); _echo_server(us); (t / "ports").write_text(f"{p2}\n")
    tr = subprocess.Popen([sys.executable, str(relay), "trial", str(t)])
    try:
        for _ in range(50):
            try: c = socket.create_connection(("127.0.0.1", p2), timeout=1); break
            except OSError: time.sleep(0.1)
        c.sendall(b"yo"); assert c.recv(100) == b"echo:yo"
    finally: tr.kill()


def test_trial_proc_tools_only_match_this_container(tmp_path):
    """2026-11-06/07: Steam trials share the hub's pid namespace; an agent's `pkill -f valheim.x86_64` killed the games of the
    other running Valheim trials. The trial setup puts pkill / pgrep / killall wrappers first on PATH that match only processes of
    the container's own mount namespace (checked with two containers on one pid namespace)"""
    import base64, re, subprocess
    from gab.host import steam
    cmd = steam.Hub.trial_setup_cmd()
    assert cmd.startswith(steam.own_proc_tools_cmd())
    for name in ("pgrep", "pkill", "killall", "ps", "pidof", "kill", steam.OWN_PROC_BASH_ENV):
        path = name if name.startswith("/") else f"/usr/local/bin/{name}"
        m = re.search(rf"echo (\S+) \| base64 -d > {re.escape(path)} && chmod 755", cmd)
        body = base64.b64decode(m.group(1)).decode()
        assert body == steam.OWN_PROC_TOOLS[name]
        assert "--ns $$ --nslist mnt" in body or name not in ("pgrep", "pkill", "killall")
        f = tmp_path / Path(name).name; f.write_text(body)
        assert subprocess.run(["bash" if name in ("kill", steam.OWN_PROC_BASH_ENV) else "sh", "-n", str(f)]).returncode == 0



def _own_tools(tmp_path):
    """the wrappers of OWN_PROC_TOOLS in tmp_path/bin (kill sourcing tmp_path/own.bash), as a trial would get them"""
    b = tmp_path / "bin"; b.mkdir()
    env_file = tmp_path / "own.bash"; env_file.write_text(S.OWN_PROC_TOOLS[S.OWN_PROC_BASH_ENV])
    for name, body in S.OWN_PROC_TOOLS.items():
        if name.startswith("/"): continue
        (b / name).write_text(body.replace(S.OWN_PROC_BASH_ENV, str(env_file))); (b / name).chmod(0o755)
    return b, env_file


@pytest.fixture
def other_cgroup():
    """a process in another cgroup than this test (pid 1 on a normal host: /init.scope), standing in for another trial's game"""
    me = Path("/proc/self/cgroup").read_text()
    if Path("/proc/1/cgroup").read_text() == me or not Path("/usr/bin/ps").exists() or not Path("/usr/bin/pidof").exists():
        pytest.skip("needs procps and pid 1 in another cgroup")
    return 1


def test_trial_ps_kill_pidof_only_this_container(tmp_path, other_cgroup):
    """2026-11-16: agents in a Cities trial ran `ps -eo pid,cmd | grep Cities.x64 | ... kill -9 $p` (bash builtin kill, pids from
    ps) and killed every Cities game on the host -- Steam trials share the hub's pid namespace. ps / pidof show only processes of
    this container (cgroup), the kill builtin (BASH_ENV) and /usr/bin/kill refuse other containers' pids and kill -1. Run here
    on the host: processes of this test's cgroup = "this container", pid 1 (/init.scope) = "another container"."""
    import subprocess
    b, env_file = _own_tools(tmp_path)
    env = {**os.environ, "PATH": f"{b}:/usr/bin:/bin", "BASH_ENV": str(env_file)}
    sh = lambda c: subprocess.run(["bash", "-c", c], env=env, capture_output=True, text=True, timeout=60)
    child = subprocess.Popen(["setsid", "sleep", "3061"]); me = str(child.pid)
    try:
        for fmt in ("aux", "-ef", "-e", "-eo pid,args", "-eo pid=,comm=", "-eo args,pid", "-eo comm=,pid=", "axo pid,comm", "-eLf"):
            r = sh(f"ps {fmt}"); assert r.returncode == 0, (fmt, r.stderr)
            rows = r.stdout.splitlines()
            assert any(me in ln.split() for ln in rows), (fmt, r.stdout[:500])          # own processes stay visible
            real = subprocess.run(["/usr/bin/ps", *fmt.split()], capture_output=True, text=True).stdout.splitlines()
            assert len(rows) < len(real)                                                # ... the others are gone
        r = sh("ps -eo pid=,comm="); assert "1" not in [ln.split()[0] for ln in r.stdout.splitlines()]
        r = sh("ps -eo comm=,pid="); assert all(ln.split()[-1] != "1" for ln in r.stdout.splitlines() if ln.split())
        r = sh("ps -eo comm="); assert r.returncode == 0 and "sleep" in r.stdout.split() and "systemd" not in r.stdout.split()
        r = sh("ps -eo args,pid"); assert r.stdout.splitlines()[0].split() == ["COMMAND", "PID"]
        assert any(ln.startswith("sleep 3061") and ln.split()[-1] == me for ln in r.stdout.splitlines())
        r = sh("ps -p 1 -o pid="); assert r.returncode == 1 and r.stdout == ""          # another container's pid: none
        r = sh(f"ps -p {me} -o comm="); assert r.returncode == 0 and r.stdout.strip() == "sleep"
        r = sh(f"ps aux | awk '$2 == {me} || $2 == 1' | grep -c ."); assert r.stdout.strip() == "1"     # ps | grep | kill
        # pidof
        r = sh("pidof sleep"); assert r.returncode == 0 and me in r.stdout.split()
        init = Path("/proc/1/comm").read_text().strip()
        r = sh(f"pidof {init}"); assert r.returncode == 1 and r.stdout == ""
        # kill: the builtin (via BASH_ENV) and /usr/bin/kill -- only -0 ever reaches another cgroup's pid in this test
        r = sh("kill -0 1"); assert r.returncode == 1 and "No such process" in r.stderr
        r = sh(f"kill -0 1 {me}"); assert r.returncode == 1 and child.poll() is None
        r = sh(f"kill -s 0 {me} && kill -0 -- -{me} && kill -n 0 {me}"); assert r.returncode == 0, r.stderr   # own pid / own group
        r = sh("kill -0 -1"); assert r.returncode == 1 and "not permitted" in r.stderr  # all processes of the uid: never
        r = sh("kill -0 -- -1"); assert r.returncode == 1
        r = sh("echo 1 | xargs kill -0"); assert r.returncode != 0 and "No such process" in r.stderr
        r = sh("type kill; kill -l 9"); assert "function" in r.stdout and "KILL" in r.stdout
        r = sh("sleep 3062 & p=$!; kill %1 && wait $p; echo rc=$?"); assert "rc=143" in r.stdout              # job specs pass
        r = sh(f"echo {me} | xargs kill -TERM"); assert r.returncode == 0
        assert child.wait(10) == -15
    finally:
        if child.poll() is None: child.kill()
    child = subprocess.Popen(["sleep", "300"])
    try:
        r = sh(f"kill -9 {child.pid}"); assert r.returncode == 0 and child.wait(10) == -9
        r = sh(f"kill -KILL {child.pid}"); assert r.returncode == 1                      # gone now: as the builtin says
    finally:
        if child.poll() is None: child.kill()

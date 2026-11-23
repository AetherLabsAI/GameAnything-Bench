"""Cities: Skylines' one-time in-game Legal Disclaimer (Paradox privacy policy + terms of use), the last step of
`gab get-game cities_skylines` (gab/setup/steamsetup.py setup_game). THE USER clicks Agree; gab only shows the user the game screen and
waits until the game itself has saved the acceptance.

Why a special container:
  - the game fetches the two documents from Paradox at the main menu. Without a network (every trial runs --network none) the
    fetch fails (Player.log: ApplicationException in PopsApi EndLegalGetDocumentsList / LegalDocumentLoader) and the game goes
    to the main menu WITHOUT showing the dialog. Offline, a missing acceptance is therefore silent: the adapter's _legal_ok()
    (cities_skylines/adapter.py install()) is the guard, and this step is the only container with a network;
  - the game writes the acceptance into colossal.cgs of the folder it runs from (bool legal.privacy.<doc> / legal.terms-of-use.<doc>);
    _legal_ok() reads the Steam library's Cities_Skylines/colossal.cgs, so the game runs from the library itself, mounted rw
    (GAB_STEAM_LIBRARY_RW=1, as steamsetup.prep_game does for Terraria) -- trials mount the library read only and copy that file.

What runs: one seat of the Steam image (the hub's IPC + pid namespace, like a trial) with network=bridge, the library rw, the game
started from the library with the adapter's cs-start.sh (VirtualGL, the seat's screen :101), and inside the same container
x11vnc (-localhost, one-time password) + websockify/noVNC (Ubuntu packages novnc / websockify in docker/steam/install.sh, no
download at run time). The noVNC port is published on the HOST's 127.0.0.1 only, on a free port gab picks; the user reaches it
through VS Code port forwarding or `ssh -L`. gab polls colossal.cgs (on the host, no container) until both documents are
accepted or the timeout passes, then removes the container (and with it the VNC chain). Skipped when already accepted.
gab never clicks the dialog and never writes a legal key.
"""
import importlib.util, os, secrets, shlex, socket, time
from pathlib import Path

from ..host import steam as S

GAME = "cities_skylines"
APP_DIR = "Cities_Skylines"
NOVNC_PORT = 6080                                   # inside the container; published on host 127.0.0.1:<free port>
VNC_PORT = 5900                                     # x11vnc inside the container, -localhost
NOVNC_WEB = "/usr/share/novnc"
HERE = Path(__file__).resolve().parent.parent / "game" / "cities_skylines"


def _cgs():
    spec = importlib.util.spec_from_file_location("gab_cities_cgs", HERE / "bridge" / "cgs.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def legal_prefixes():
    from ..game.cities_skylines.adapter import LEGAL
    return LEGAL


def cgs_path(state_dir):
    """the library's colossal.cgs on the HOST (the hub's state dir; the seat sees the same file at ~/<LIB>/Cities_Skylines)"""
    return Path(state_dir) / "home" / S.LIB / APP_DIR / "colossal.cgs"


def accepted_keys(path):
    p = Path(path)
    if not p.exists(): return []
    try: d = _cgs().read(p)
    except Exception: return []
    return sorted(k for k, v in (d.get("bool") or {}).items() if k.startswith("legal.") and v)


def accepted(path):
    """the same test as the adapter's _legal_ok(): one true key per document (privacy policy, terms of use)"""
    keys = accepted_keys(path)
    return all(any(k.startswith(p) for k in keys) for p in legal_prefixes())


def free_port(start=NOVNC_PORT + 10, tries=200):
    """a free TCP port on the host's 127.0.0.1 (6090 upwards; 6080 is left to an operator's own noVNC)"""
    for port in range(start, start + tries):
        s = socket.socket()
        try: s.bind(("127.0.0.1", port)); return port
        except OSError: continue
        finally: s.close()
    raise RuntimeError(f"no free port on 127.0.0.1 in {start}..{start + tries - 1}")


def instructions(port, password, host_hint=None):
    url = f"http://localhost:{port}/vnc.html?autoconnect=1&resize=scale&password={password}"
    ssh = f"ssh -L {port}:localhost:{port} {host_hint or '<you>@<this server>'}"
    return "\n".join([
        "",
        "=" * 100,
        "Cities: Skylines needs Paradox's in-game Legal Disclaimer accepted ONCE, by you (gab never clicks it).",
        f"  1. Forward port {port}: VS Code -> Ports -> Forward a Port -> {port}   (or from your laptop: {ssh})",
        f"  2. Open {url}",
        f"     (noVNC; the password, if asked: {password} -- one-time, valid only while this step runs)",
        "  3. Read the documents (Show) and click Agree on BOTH (privacy policy, then terms of use).",
        "     Dismiss nothing else; gab notices the acceptance by itself and closes the screen.",
        "=" * 100,
        ""])


def seat_script(lib_dir, display, password, port=NOVNC_PORT):
    """the commands run in the acceptance seat: x11vnc on localhost with a password file, websockify/noVNC on the container port
    (published on host 127.0.0.1 only), then the game from the Steam library folder with cs-start.sh"""
    q = shlex.quote
    return (
        "set -e; mkdir -p /tmp/gab-legal /tmp/xdg-runtime && chmod 700 /tmp/gab-legal /tmp/xdg-runtime; "
        f"printf '%s\\n' {q(password)} > /tmp/gab-legal/pass; chmod 600 /tmp/gab-legal/pass; "
        f"setsid nohup x11vnc -display {q(display)} -localhost -rfbport {VNC_PORT} -forever -shared -noxdamage "
        "-passwdfile /tmp/gab-legal/pass > /tmp/gab-legal/x11vnc.log 2>&1 < /dev/null & "
        f"setsid nohup websockify --web {NOVNC_WEB} 0.0.0.0:{port} localhost:{VNC_PORT} > /tmp/gab-legal/websockify.log 2>&1 < /dev/null & "
        f"cd {q(lib_dir)} && (exec env HOME=$HOME GAME_DIR={q(lib_dir)} VGL=1 DISPLAY={q(display)} XDG_RUNTIME_DIR=/tmp/xdg-runtime "
        "CS_RETRIES=2 setsid nohup bash /tmp/gab-legal/cs-start.sh) > /tmp/gab-legal/game.out 2>&1 < /dev/null & "
        "echo started")


def image_has_novnc(backend, ref):
    r = backend._run(["run", "--rm", "--network", "none", "--entrypoint", "sh", ref, "-c",
                      f"command -v websockify >/dev/null && test -f {NOVNC_WEB}/vnc.html && command -v x11vnc >/dev/null && echo ok"],
                     timeout=120)
    return r.returncode == 0 and "ok" in (r.stdout or "")


class _env:
    """temporary GAB_* settings for the one acceptance seat (network, rw library, the 127.0.0.1 port publish)"""
    def __init__(self, port):
        prev = os.environ.get("GAB_DOCKER_RUN_ARGS") or ""
        self.new = {"GAB_DOCKER_NETWORK": "bridge", "GAB_STEAM_LIBRARY_RW": "1",
                    "GAB_DOCKER_RUN_ARGS": (prev + f" -p 127.0.0.1:{port}:{NOVNC_PORT}").strip()}
    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.new}; os.environ.update(self.new); return self
    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


def run(hub, out=print, timeout_s=1800, poll_s=5, acquire=None, sleep=time.sleep, port=None, password=None, rebuild=None):
    """-> 'accepted before' | 'accepted' ; raises SystemExit on timeout (the container is removed in every case)"""
    path = cgs_path(hub.st)
    if accepted(path): return "accepted before"
    if not path.parent.is_dir():
        raise SystemExit(f"{GAME}: not installed in the Steam library ({path.parent} missing): gab steam install {GAME}")
    ref = S.hub_image(hub.b)
    if not image_has_novnc(hub.b, ref):
        if rebuild is None:
            raise SystemExit(f"the Steam image {ref} predates noVNC (packages novnc + websockify): rebuild it with "
                             "`STEAM_ACCEPT_SSA=yes docker/build.sh steam`, then re-run gab get-game cities_skylines")
        rebuild()
    port = port or free_port(); password = password or secrets.token_urlsafe(6)[:8]
    if acquire is None:
        from ..host import base as hbase
        acquire = lambda: hbase.get("docker").acquire({"game": GAME}, "gab-cities-legal")
    out(f"{GAME}: starting the one-time Legal Disclaimer screen (a container with a network, the Steam library writable)")
    with _env(port):
        seat = acquire()
    try:
        lib = f"{seat.home}/{S.LIB}/{APP_DIR}"
        seat.put("/tmp/gab-legal/cs-start.sh", (HERE / "bridge" / "cs-start.sh").read_text())
        seat.exec("chmod +x /tmp/gab-legal/cs-start.sh", check=True)   # put() loses the exec bit; cs-start.sh execs itself under vglrun
        r = seat.exec(seat_script(lib, seat.display, password), timeout=120)
        if r.returncode or "started" not in (r.stdout or ""):
            raise SystemExit(f"{GAME}: the disclaimer screen did not start: {(r.stdout or '')[-400:]} {(r.stderr or '')[-400:]}")
        out(instructions(port, password))
        t0 = time.time(); last = 0
        while time.time() - t0 < timeout_s:
            if accepted(path):
                out(f"{GAME}: Legal Disclaimer accepted ({', '.join(accepted_keys(path))})"); return "accepted"
            if time.time() - last > 60:
                out(f"  waiting for Agree on both documents ({int(timeout_s - (time.time() - t0)) // 60} min left; "
                    f"http://localhost:{port}/vnc.html)"); last = time.time()
            sleep(poll_s)
        raise SystemExit(f"{GAME}: the Legal Disclaimer was not accepted within {timeout_s // 60} min; re-run "
                         f"`gab get-game {GAME}` to show it again (nothing else needs redoing)")
    finally:
        seat.release(); out(f"{GAME}: disclaimer screen closed (container {seat.sandbox} removed)")

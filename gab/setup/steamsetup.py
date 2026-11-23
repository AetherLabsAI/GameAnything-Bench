"""Steam games in ONE command (`gab get-game terraria | valheim | cities_skylines`): the Steam image, the hub's user-namespace
permission, the hub, the login IN THE TERMINAL, the game's app ids, a start check. The user is asked only for: the Steam Subscriber
Agreement (once, before the image is built), the sudo password (once per machine, only if the hub's AppArmor profile is not
installed yet), a game EULA where Steam would show one (tModLoader), and the Steam account (name, password, Steam Guard) -- unless
the hub is already logged on. Every step whose result is in place is skipped, so re-running is safe.

Login without VNC (terminal_login): the hub's Steam shows its login window on the hub's own X screen. The account name and the
password are read here (the password hidden, getpass) and typed into that window by xdotool in the hub reading STDIN
(`docker exec -i ... xdotool type --file -`): never an argv, a file or a log. Steam Guard: the user approves on the phone (the
Steam app) or types the code here (hidden), which is typed into the Guard window the same way. If the Python packages zxing-cpp and
segno are installed, the login window's QR code is also printed in the terminal (scan it with the Steam app instead of typing).
"Remember me" is Steam's default; after the logon gab checks loginusers.vdf and warns if auto-login is off. VNC stays a fallback:
`gab steam login --vnc`.

Game files (install_apps): Steam's own install path per app id (steam://install/<id>); the dialog's primary button (Install, then
a licence's Accept only after the user said yes here) is found on a screenshot of the hub (primary_button: Steam's blue gradient)
and clicked with xdotool; confirmed by the app's manifest appearing. Fallback: an appmanifest marked "update required" + a Steam
restart. Done = every app's StateFlags 4. Terraria: one first start with the library writable (tModLoader fixes file casings).
Cities: Skylines: Paradox's in-game Legal Disclaimer, accepted by the user in a browser (noVNC) in one container with a network
(gab/setup/citieslegal.py); skipped once the game has saved the acceptance.
"""
import getpass, os, re, select, shlex, shutil, subprocess, sys, tempfile, termios, time
from pathlib import Path

from .. import config
from ..host import steam as S

APPS = {"terraria": [(105600, "Terraria", "Terraria"), (1281930, "tModLoader", "tModLoader")],
        "valheim": [(892970, "Valheim", "Valheim")],
        "cities_skylines": [(255710, "Cities_Skylines", "Cities: Skylines")]}
EULA = {1281930: ("tModLoader EULA", "https://store.steampowered.com/eula/1281930_eula_0"),
        255710: ("Paradox Interactive EULA (Cities: Skylines)", "https://store.steampowered.com/eula/255710_eula_0")}
SSA = ("The Steam image installs Valve's Steam client. Do you accept the Steam Subscriber Agreement "
       "(https://store.steampowered.com/subscriber_agreement/)? [y/N] ")
LOGIN_WIN = re.compile(r"sign in|log ?in|steam", re.I)
SIGNIN_WIN = re.compile(r"sign in|log ?in", re.I)            # Steam's login window ("Sign in to Steam"); the main window is "Steam"
GUARD_WIN = re.compile(r"guard|code|confirm|verif", re.I)


def yes(ask, q, default=False):
    a = (ask(q) or "").strip().lower()
    return a in ("y", "yes") if a else default


def set_local(key, value):
    from .getgame import set_local as sl
    sl(key, value)


# ---- the image
_build = subprocess.run                                         # (tests replace it)


def ensure_image(hub, ask=input, out=print, force=False):
    """the Steam image present (built when missing; force=True: rebuilt, e.g. an image from before the noVNC packages)"""
    ref = S.hub_image(hub.b)
    if hub.b.has_image(ref) and not force:
        hub.check_image_uid(ref); out(f"steam image {ref}: present"); return ref
    if not yes(ask, SSA): raise SystemExit("not building the Steam image: the Steam Subscriber Agreement was not accepted")
    out("building the Steam image (docker/build.sh steam: apt from a normal Ubuntu mirror; 10-30 min the first time)")
    _build(["bash", str(config.REPO / "docker" / "build.sh"), "steam"], check=True,
                   env={**os.environ, "STEAM_ACCEPT_SSA": "yes", "TAG": ref.rsplit(":", 1)[1] if "@" not in ref and ":" in ref.split("/")[-1] else "local"})
    hub.check_image_uid(ref); return ref


# ---- the hub's permission to create user namespaces (Steam refuses to start without)
PROFILE = "gab-steam-hub"
ETC = Path("/etc/apparmor.d")
SECCOMP = "docker/steam/seccomp-steam.json"


def apparmor_enabled():
    try: return Path("/sys/module/apparmor/parameters/enabled").read_text().strip() == "Y"
    except OSError: return False


def ensure_userns(ask=input, out=print, run=subprocess.run):
    """steam_hub_seccomp / steam_hub_apparmor: the AppArmor profile docker/steam/gab-steam-hub.apparmor installed with sudo (asks
    first, shows the commands), else (no sudo) apparmor=unconfined after asking. Only the hub gets these, never a trial."""
    if config.get("steam_hub_seccomp") and (config.get("steam_hub_apparmor") or not apparmor_enabled()):
        return "configured"
    set_local("steam_hub_seccomp", SECCOMP)
    if not apparmor_enabled(): return "seccomp only (no AppArmor on this host)"
    src = config.REPO / "docker" / "steam" / "gab-steam-hub.apparmor"
    if (ETC / PROFILE).exists() and (ETC / PROFILE).read_bytes() == src.read_bytes():
        set_local("steam_hub_apparmor", PROFILE); return "profile already installed"
    cmds = [["sudo", "install", "-m", "644", str(src), str(ETC / PROFILE)],
            ["sudo", "apparmor_parser", "-r", "-W", str(ETC / PROFILE)]]
    out("Steam needs user namespaces in its container. gab installs an AppArmor profile for the Steam hub container ONLY "
        "(Docker's default profile + userns / mount; trials keep Docker's default):")
    for c in cmds: out("   " + " ".join(shlex.quote(x) for x in c))
    if yes(ask, "Run these two commands with sudo now (sudo asks for your password)? [Y/n] ", default=True):
        ok = all(run(c).returncode == 0 for c in cmds)
        if ok: set_local("steam_hub_apparmor", PROFILE); return "profile installed"
        out("sudo failed.")
    if yes(ask, "Without the profile the Steam hub can run with AppArmor 'unconfined' (no AppArmor confinement for the hub; "
                "the host's /etc/apparmor.d/steam profile then applies to Steam). Use that? [y/N] "):
        set_local("steam_hub_apparmor", "unconfined"); return "unconfined"
    raise SystemExit("the Steam hub cannot start without user namespaces (docker/README.md, Steam games)")


# ---- the hub
def ensure_hub(hub, out=print, wait_s=900):
    """state dir, hub running (no VNC), Steam client up (the first start downloads it) -> status"""
    if not config.get("steam_state"):
        from .getgame import commercial_dir
        set_local("steam_state", str(commercial_dir() / "steam")); hub.st = S.state_dir()
    hub.init_state()
    out(f"steam hub {hub.name}: {hub.up()}")
    t0 = time.time(); last = None
    while time.time() - t0 < wait_s:
        st = hub.status()
        if st.get("needs_userns"): raise SystemExit("Steam refuses to start in the hub: user namespaces denied (steam_hub_seccomp / "
                                                    "steam_hub_apparmor; docker/README.md, Steam games)")
        if st.get("logged_on") or (st.get("running") and any(LOGIN_WIN.search(w) for w in st.get("windows") or [])): return st
        k = (st.get("running"), st.get("client"))
        if k != last: out(f"  waiting for the Steam client (running={k[0]}, client={k[1]}; the first start downloads ~500 MB)"); last = k
        time.sleep(5)
    raise SystemExit("the Steam client did not come up in the hub (gab steam shot hub.png to look)")


# ---- login in the terminal
def _windows(st):
    out = []
    for w in st.get("windows") or []:
        m = re.match(r"(\d+) (\d+)x(\d+)\+(-?\d+)\+(-?\d+) ?(.*)", w)
        if m: out.append({"id": m.group(1), "w": int(m.group(2)), "h": int(m.group(3)), "name": m.group(6)})
    return out


def login_window(st):
    """the biggest visible window whose name looks like Steam's sign-in window"""
    ws = [w for w in _windows(st) if w["w"] >= 300 and w["h"] >= 200 and LOGIN_WIN.search(w["name"])]
    named = [w for w in ws if SIGNIN_WIN.search(w["name"])]
    ws = named or ws
    return max(ws, key=lambda w: w["w"] * w["h"])["id"] if ws else None


def type_secret(hub, wid, text, then=None):
    """type text into window wid: xdotool in the hub reads it from STDIN (docker exec -i), never from an argv"""
    focus = f"xdotool windowfocus --sync {int(wid)} 2>/dev/null; " if wid else ""
    r = hub.exec_input(f"{focus}xdotool type --delay 30 --clearmodifiers --file -", text)
    if then: hub.exec(f"xdotool key --clearmodifiers {then}")
    return r.returncode == 0


def read_hidden(prompt, timeout, out=print, echo=False):
    """a (hidden) line from the terminal, or None after `timeout` s without Enter (the caller keeps polling Steam meanwhile)"""
    if prompt: sys.stdout.write(prompt); sys.stdout.flush()
    fd = sys.stdin.fileno(); old = termios.tcgetattr(fd); new = termios.tcgetattr(fd)
    if not echo: new[3] &= ~termios.ECHO
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r: return None
        line = sys.stdin.readline().rstrip("\n"); sys.stdout.write("\n"); return line
    finally: termios.tcsetattr(fd, termios.TCSADRAIN, old)


def qr_lines(hub):
    """the login window's QR code as terminal text (needs the optional packages zxing-cpp + segno) -> list of lines or []"""
    try: import zxingcpp, segno; from PIL import Image
    except ImportError:
        if getattr(qr_lines, "tried", False): return []
        qr_lines.tried = True
        print("(installing zxing-cpp + segno + pillow into this Python to show Steam's QR code here: pip install zxing-cpp segno pillow)")
        if subprocess.run([sys.executable, "-m", "pip", "install", "-q", "zxing-cpp", "segno", "pillow"]).returncode: return []
        try: import zxingcpp, segno; from PIL import Image
        except ImportError: return []
    with tempfile.TemporaryDirectory() as d:
        os.chmod(d, 0o700)
        hub.helper("shot /tmp/gab-login.png", timeout=60)
        if hub.b._run(["cp", f"{hub.name}:/tmp/gab-login.png", f"{d}/s.png"], timeout=60).returncode: return []
        hub.exec("rm -f /tmp/gab-login.png")
        res = [r for r in zxingcpp.read_barcodes(Image.open(f"{d}/s.png")) if "QR" in str(r.format)]
    if not res: return []
    q = segno.make(res[0].text, error="l"); m = [[bool(x) for x in row] for row in q.matrix]
    n = len(m); pad = [[False] * (n + 4)] * 2; m = pad + [[False, False] + r + [False, False] for r in m] + pad
    lines = []
    for y in range(0, len(m), 2):                              # two module rows per text line, dark = black on a white terminal block
        a, b = m[y], (m[y + 1] if y + 1 < len(m) else [False] * len(m[y]))
        lines.append("".join(" " if (p and q2) else "▀" if (not p and q2) else "▄" if (p and not q2) else "█"
                             for p, q2 in zip(a, b)))
    return lines


def choose(hub, ask, out, hidden, wait_s=600):
    """the QR code (refreshed when Steam rotates it) + the account-name prompt at the same time -> the name typed, "" (nothing
    typed), or None when Steam logged on meanwhile (the QR code was scanned). Without a terminal / QR packages: just ask."""
    qr = qr_lines(hub)
    if not qr or not sys.stdin.isatty() or hidden is not read_hidden:
        return (ask("Steam account name: ") or "").strip()
    shown = None; t0 = time.time(); prompted = False
    while time.time() - t0 < wait_s:
        if qr and qr != shown:
            out(("\nSteam rotated the QR code:" if shown else "Scan this QR code with the Steam app (Steam Guard tab, "
                 "scan a QR code) -- or type your account name below and press Enter:")); out("\n".join(qr)); shown = qr; prompted = False
        if not prompted: sys.stdout.write("Steam account name: "); sys.stdout.flush(); prompted = True
        line = read_hidden("", 3, echo=True)
        if line is not None: return line.strip()
        if hub.status().get("logged_on"): out("\nlogged on with the QR code"); return None
        if int(time.time() - t0) % 9 < 3: qr = qr_lines(hub) or qr
    return ""


def terminal_login(hub, ask=input, askpass=getpass.getpass, out=print, hidden=read_hidden, tries=3, wait_s=300):
    st = hub.status()
    if not st.get("logged_on") and st.get("account") and st.get("remember"):       # a saved login: Steam logs on by itself
        out(f"Steam: saved login of {st['account']}, waiting for the automatic logon ...")
        for _ in range(24):
            time.sleep(5); st = hub.status()
            if st.get("logged_on"): break
    if st.get("logged_on"):
        out(f"Steam: already logged on as {st.get('account')}"); return st
    for attempt in range(1, tries + 1):
        st = hub.status(); wid = login_window(st)
        if st.get("logged_on"): break
        if not wid:
            for _ in range(36):                                     # the window may need a moment after a (re)start
                time.sleep(5); st = hub.status(); wid = login_window(st)
                if wid or st.get("logged_on"): break
        if st.get("logged_on"): break
        if not wid: raise SystemExit("no Steam login window in the hub (gab steam shot hub.png; fallback: gab steam login --vnc)")
        name = choose(hub, ask, out, hidden)
        if name is None: st = hub.status(); break                  # logged on with the QR code
        if name:
            pw = askpass("Steam password (hidden): ")
            hub.exec(f"xdotool windowfocus --sync {int(wid)} 2>/dev/null; xdotool key --clearmodifiers ctrl+a BackSpace")
            type_secret(hub, wid, name, then="Tab")
            hub.exec("xdotool key --clearmodifiers ctrl+a BackSpace")
            type_secret(hub, None, pw, then="Return"); pw = None
            out("signing in ... if Steam asks for a Steam Guard confirmation, approve it in the Steam app on your phone, or type the "
                "code (from the app or the e-mail) at the prompt below.")
        t0 = time.time(); guard_sent = False; asked = False
        while time.time() - t0 < wait_s:
            st = hub.status()
            if st.get("logged_on"): break
            if st.get("login_error") and time.time() - t0 > 5:
                out(f"Steam: {st['login_error']}"); break
            code = None
            if name and not guard_sent:
                code = hidden("" if asked else "Steam Guard code (type it and press Enter; or approve on your phone and just wait): ", 10)
                asked = True
            if code:
                gw = next((w["id"] for w in _windows(st) if GUARD_WIN.search(w["name"])), None) or login_window(st)
                type_secret(hub, gw, code.strip(), then="Return"); guard_sent = True; out("code sent, waiting for the logon ...")
            elif not name or guard_sent: time.sleep(5)
        if st.get("logged_on"): break
        shot = hub.st / "login-failed.png"
        hub.helper("shot /tmp/gab-login.png", timeout=60); hub.b._run(["cp", f"{hub.name}:/tmp/gab-login.png", str(shot)], timeout=60)
        hub.exec("rm -f /tmp/gab-login.png")
        out(f"not logged on (attempt {attempt}/{tries}); the hub's screen is in {shot} (only you can read it)")
    if not st.get("logged_on"):
        raise SystemExit("Steam login did not finish. Fallback: gab steam login --vnc (Steam's window over an ssh tunnel)")
    out(f"Steam: logged on as {st.get('account')}")
    if st.get("remember") is False:
        out("WARNING: Steam did not keep 'Remember me' (auto-login off): after a hub restart you would log in again. "
            "Run `gab steam login --vnc` once and tick 'Remember me'.")
    return st


# ---- the game's apps
def _shot(hub, d):
    hub.helper("shot /tmp/gab-dialog.png", timeout=60)
    ok = hub.b._run(["cp", f"{hub.name}:/tmp/gab-dialog.png", f"{d}/s.png"], timeout=60).returncode == 0
    hub.exec("rm -f /tmp/gab-dialog.png")
    return f"{d}/s.png" if ok else None


def click_dialog(hub, before, timeout=40, finder=None):
    """wait for a NEW visible window (not in `before`), click its primary button (found on a screenshot: primary_button; else
    Enter), wait until it closes -> the window dict, or None when no dialog appeared"""
    finder = finder or primary_button
    t0 = time.time(); new = []
    while time.time() - t0 < timeout:
        new = [w for w in _windows(hub.status()) if w["id"] not in before and w["w"] >= 250 and w["h"] >= 120]
        if new: break
        time.sleep(2)
    if not new: return None
    time.sleep(2)                                                  # the dialog finishes drawing
    win = max(new, key=lambda w: w["w"] * w["h"])
    st = hub.status(); win = next((w for w in _windows(st) if w["id"] == win["id"]), win)
    m = re.match(r"\d+ \d+x\d+\+(-?\d+)\+(-?\d+)", next((x for x in st.get("windows") or [] if x.startswith(win["id"] + " ")), ""))
    rect = (int(m.group(1)), int(m.group(2)), win["w"], win["h"]) if m else None
    with tempfile.TemporaryDirectory() as d:
        os.chmod(d, 0o700); png = _shot(hub, d); btn = finder(png, rect) if png else None
    if btn: hub.exec(f"xdotool mousemove {int(btn[0])} {int(btn[1])} click 1")
    else: hub.exec(f"xdotool windowfocus --sync {int(win['id'])} 2>/dev/null; xdotool key --clearmodifiers Return")
    for _ in range(15):
        time.sleep(1)
        if win["id"] not in {w["id"] for w in _windows(hub.status())}: break
    return dict(win, button=btn)


def install_apps(hub, game, ask=input, out=print, timeout_s=4 * 3600):
    """each app id of the game: Steam's own install path (steam://install/<id>), its Install button clicked on the hub's screen,
    a licence dialog accepted only after the user said yes here; confirmed by the app's manifest appearing in the library.
    Fallback per app: an appmanifest marked "update required" + a Steam restart. Then wait until every app is installed."""
    apps = APPS.get(game) or []
    st = hub.status(); have = st.get("apps") or {}
    todo = [a for a in apps if not (have.get(str(a[0])) or {}).get("installed")]
    if not todo: out(f"{game}: Steam apps {', '.join(str(a[0]) for a in apps)} installed"); return st
    if not st.get("logged_on"): raise SystemExit("Steam is not logged on in the hub (gab steam login)")
    fallback = []
    for appid, d, name in todo:
        if str(appid) in have:                                     # already queued / downloading: nothing to click
            out(f"{name} ({appid}): already queued (state {have[str(appid)].get('state')})"); continue
        if appid in EULA:
            ename, url = EULA[appid]
            if not yes(ask, f"{name} needs the {ename} ({url}). Do you accept it? [y/N] "): raise SystemExit(f"{name}: EULA not accepted")
        before = {w["id"] for w in _windows(hub.status())}
        hub.helper(f"install {appid}")
        w = click_dialog(hub, before)
        out(f"{name} ({appid}): " + (f"Install clicked in Steam's dialog" if w else "no install dialog appeared"))
        if w:                                                      # a licence dialog after Install (tModLoader, Cities)
            before2 = {x["id"] for x in _windows(hub.status())}
            new = [x for x in _windows(hub.status()) if x["id"] not in before2 and x["w"] >= 250]
            if not new:
                for _ in range(6):
                    time.sleep(2); new = [x for x in _windows(hub.status()) if x["id"] not in before2 and x["w"] >= 250]
                    if new: break
            if new:
                if appid not in EULA and not yes(ask, f"Steam shows a licence agreement for {name} ({new[0]['name'] or 'dialog'}). Accept it? [y/N] "):
                    raise SystemExit(f"{name}: licence not accepted")
                click_dialog(hub, before2, timeout=5); out(f"{name}: licence accepted in Steam's dialog")
        for _ in range(30):                                        # confirmed: Steam wrote the app's manifest
            time.sleep(2)
            if str(appid) in (hub.status().get("apps") or {}): break
        else:
            out(f"{name}: no manifest from Steam's dialog; falling back to a manifest file"); fallback.append((appid, d, name))
    for appid, d, name in fallback:
        r = hub.helper(f"manifest {appid} {shlex.quote(d)} {shlex.quote(name)}"); out(f"{name} ({appid}): {r.stdout.strip()}")
    if fallback:
        out("restarting Steam so it picks the manifests up (it logs on again from the saved login) ..."); hub.helper("restart"); time.sleep(15)
    t0 = time.time(); last = None; moved = t0
    while time.time() - t0 < timeout_s:
        st = hub.status(); have = st.get("apps") or {}
        left = [a for a in todo if not (have.get(str(a[0])) or {}).get("installed")]
        if not left: out(f"{game}: installed"); return st
        prog = ", ".join(f"{a[2]} {_mb(have.get(str(a[0])))}" for a in left)
        if prog != last: out(f"  downloading: {prog}"); last = prog; moved = time.time()
        if time.time() - moved > 900:
            raise SystemExit(f"{game}: Steam does not download ({prog}) for 15 min. Look: gab steam shot hub.png; fallback: "
                             f"gab steam login --vnc and click Install for app(s) {', '.join(str(a[0]) for a in left)}")
        time.sleep(15)
    raise SystemExit(f"{game}: download not finished after {timeout_s // 3600} h")


def prep_game(hub, game, out=print):
    """one-time first start of a game with the library WRITABLE (trials mount it read only): tModLoader renames Terraria's Content
    files to their canonical case on its first start (TMLContentManager.TryFixFileCasings) and crashes on a read-only library
    ("Read-only file system"). Done by gab itself (no agent, no mod); marker <steam_state>/prep-<game>.<buildid>."""
    if game != "terraria": return "not needed"
    bid = (hub.status().get("apps") or {}).get("105600", {}).get("buildid") or "0"
    mark = hub.st / f"prep-{game}.{bid}"
    if mark.exists(): return "done before"
    from ..game import base as gbase
    from .selfcheck import _seat
    out(f"{game}: first start with a writable library (tModLoader fixes Terraria's file-name casing once) ...")
    os.environ["GAB_STEAM_LIBRARY_RW"] = "1"
    try:
        seat = _seat(game, "prep")
        try:
            ad = gbase.get(game, seat); ad.provision("prep"); ad.attach("prep", None)
            ok, lg = ad.start("prep"); ad.stop("prep")
        finally: seat.release()
    finally: os.environ.pop("GAB_STEAM_LIBRARY_RW", None)
    if not ok: raise SystemExit(f"{game}: the first start failed: {str(lg)[-500:]}")
    mark.write_text("ok\n"); return "done"


def _mb(a):
    a = a or {}
    try: return f"{int(a.get('bytes_done') or 0) // 2**20}/{int(a.get('bytes_total') or 0) // 2**20} MB"
    except ValueError: return "?"


def ensure_pydeps(out=print):
    """Pillow (the dialog buttons on the hub's screenshots) + zxing-cpp / segno (the login QR code in the terminal): pip-installed
    into this Python when missing (pyproject extra `steam`)"""
    try: import PIL, zxingcpp, segno; return "present"
    except ImportError: pass
    out("installing the Python packages for the Steam setup into this Python: pip install pillow zxing-cpp segno")
    pk = ["pillow", "zxing-cpp", "segno"]
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pk], capture_output=True, text=True)
    if r.returncode and shutil.which("uv"):                       # a uv-made venv has no pip
        r = subprocess.run(["uv", "pip", "install", "-q", "--python", sys.executable, *pk])
    if r.returncode: raise SystemExit("installing pillow zxing-cpp segno failed (pip install -e '.[steam]' by hand)")
    return "installed"


def setup_game(game, do_check=True, ask=input, askpass=getpass.getpass, out=print):
    """the whole chain for one Steam game; each step skips when its result is in place"""
    from ..host.docker import Backend
    if game not in APPS: raise SystemExit(f"{game}: not a Steam game ({', '.join(APPS)})")
    ensure_pydeps(out)
    hub = S.Hub(Backend())
    ensure_image(hub, ask, out)
    out(f"user namespaces for the hub: {ensure_userns(ask, out)}")
    ensure_hub(hub, out)
    if not hub.exec(f"test -x {S.SCRIPTS}/gab-steam && echo ok").stdout.strip():
        # a hub started by an older gab (its helper scripts from the image lack fields this flow reads): restart it the current way
        out("restarting the hub with this checkout's scripts (the login stays: Steam logs on again from its saved token)")
        hub.down(); ensure_hub(hub, out)
        st0 = hub.status()
        if st0.get("account") and st0.get("remember") and not st0.get("logged_on"): hub.wait(240)
    terminal_login(hub, ask, askpass, out)
    install_apps(hub, game, ask, out)
    out(f"{game}: first-start prep: {prep_game(hub, game, out)}")
    if game == "cities_skylines":                                 # Paradox's in-game Legal Disclaimer: the user clicks Agree herself
        from . import citieslegal
        out(f"{game}: Legal Disclaimer: {citieslegal.run(hub, out, rebuild=lambda: ensure_image(hub, ask, out, force=True))}")
    if hub.has_vnc():                                              # a hub started for a VNC login: restart it without VNC
        out("restarting the hub without VNC (the login stays: Steam logs on again from its saved token)")
        hub.down(); ensure_hub(hub, out)
        if not hub.wait(240): raise SystemExit("the hub did not log on again after the restart (gab steam status)")
    out(f"steam hub: logged on as {hub.status().get('account')}, {game} apps installed")
    if do_check:
        from .getgame import check
        check(game)


# ---- Steam's dialogs: the primary button (Install, Accept, Sign in) = the largest blue-gradient button in the dialog
def primary_button(png, rect=None, min_w=60, min_h=20):
    """-> (x, y) screen centre of the largest saturated-blue blob (Steam's primary-button gradient, cyan #0bbfff .. blue #2d6bff)
    inside rect (x, y, w, h), or None. Found on the screenshot, so it follows the dialog's real layout (no fixed coordinates)."""
    from PIL import Image
    im = Image.open(png).convert("RGB"); W, H = im.size
    x0, y0, w, h = rect or (0, 0, W, H)
    x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(W, x0 + w), min(H, y0 + h)
    px = im.load(); step = 2
    blue = {(x, y) for y in range(y0, y1, step) for x in range(x0, x1, step)
            if (lambda r, g, b: b >= 200 and r <= 100 and 70 <= g <= 230 and b - r >= 120)(*px[x, y])}
    best, seen = None, set()
    for p in blue:
        if p in seen: continue
        stack, comp = [p], []
        seen.add(p)
        while stack:
            q = stack.pop(); comp.append(q)
            for d in ((step, 0), (-step, 0), (0, step), (0, -step)):
                n = (q[0] + d[0], q[1] + d[1])
                if n in blue and n not in seen: seen.add(n); stack.append(n)
        xs, ys = [c[0] for c in comp], [c[1] for c in comp]
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        if bw >= min_w and bh >= min_h and len(comp) >= 0.5 * (bw * bh) / step ** 2 and (not best or len(comp) > best[0]):
            best = (len(comp), (min(xs) + bw // 2, min(ys) + bh // 2))
    return best[1] if best else None

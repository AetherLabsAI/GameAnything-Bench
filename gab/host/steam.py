"""Steam for the `docker` backend: games whose docker/images.yaml entry has `steam: true` (Terraria / tModLoader, Valheim / BepInEx)
need a running Steam client that is logged on with an account owning them. Only ONE account owns the games and Steam allows one
login at a time, so the machine runs ONE Steam client -- the hub -- and every Steam-game trial container uses it.

  hub         container `gab-steam-hub` (config steam_hub), image gameanything-bench-steam (docker/steam), main process
              gab-steam-hub: its own Xvfb, dbus, the Steam client and the IPC relay (gab-steam-relay hub), as the image user player;
              HOME = the operator's Steam state dir <steam_state>/home (default <games_dir>/steam). The hub alone has network
              (steam_hub_network, default bridge); --ipc shareable.
  ownership   the steam image is built with PLAYER_UID = the operator's host uid (docker/build.sh steam does it), so everything
              Steam writes on the host (client, login token, library) belongs to the operator and to no other host account.
              <steam_state>, home/, vnc/, relay/: mode 700. Hub.up / init refuse an image whose player uid (label
              org.gameanything-bench.player-uid) is not the caller's uid, and check after every start that home/ holds nothing of
              another uid (check_owner). Trials of Steam games run the same image, so the game's uid = the hub's (Steam's IPC shm
              names carry the uid: /dev/shm/u<uid>-...).
  trial       a Steam-game trial container keeps `--network none` and gets (trial_args), all measured with `gab steam probe`:
                --ipc container:<hub>           Steam's IPC uses POSIX shm (/dev/shm/u<uid>-ValveIPCSharedObj-Steam, u<uid>-Shm_*)
                <relay/> -> /run/gab-steam-relay (ro), and `gab-steam-relay trial` inside: the game's steamclient.so ALSO needs a TCP
                                                connection to the client on 127.0.0.1:57343; the trial's 127.0.0.1:<port> is bridged to
                                                the hub's over unix sockets -- only the ports the main Steam process listens on
                                                (without it "Cannot create IPC pipe to Steam client process"; with it the
                                                same answer as inside the hub)
                <library>/steamapps/common      -> ~/.steam/debian-installation/steamapps/common (read only; the adapters' path)
                <client>/linux64/steamclient.so -> ~/.steam/sdk64/steamclient.so (read only: what libsteam_api loads)
                --pid container:<hub>           the Steam client watches each game process by pid: without it the game is
                                                logged on for a few seconds, then Steam drops its pipe and the game exits
                                                ("fatal stalled cross-thread pipe"). Measured on a logged-on hub.
                ~/.steam/steam.pid = 1          libsteam_api's "is Steam running" check (pid 1 = the hub's init, alive)
              Never in a trial: config/ (login token), userdata/ (cloud saves), logs/, the rest of the client, the hub's network.

Login (the operator, in their own terminal, never through gab or an agent): `gab steam login` starts the hub with x11vnc on the unix
socket <steam_state>/vnc/vnc.sock (dir 700, socket 600, both the operator's) and prints the ssh -L line; the operator types the
password and Steam Guard code in Steam's own window (or scans the QR code with the Steam app) and ticks "Remember me"; Steam keeps
its refresh token in home/.

The hub needs unprivileged user namespaces (Steam refuses to start without them since 2024: its web helper runs in the Steam Linux
Runtime through bwrap). Docker's default seccomp / AppArmor deny that; steam_hub_seccomp / steam_hub_apparmor (default "": Docker's
defaults, i.e. Steam does NOT start) are the operator's decision, e.g. docker/steam/seccomp-steam.json + a loaded AppArmor profile
docker/steam/gab-steam-hub.apparmor. Only the hub gets them; trials never.

Config keys: docker_steam (hub | off), steam_state, steam_hub, steam_hub_network, steam_hub_image ("" = the manifest's steam_hub),
steam_hub_seccomp, steam_hub_apparmor, steam_require_logon (default true: a Steam-game trial refuses to start while the hub is not
logged on).
"""
import json, os, shlex, stat, time
from pathlib import Path

from .. import config

CPATH = "/home/player/.steam-state"                       # the state dir's home/ inside the hub (= the hub's HOME)
VNC_DIR = "/home/player/.steam-vnc"                       # the state dir's vnc/ (mode 700, the operator's: x11vnc runs as the
                                                          # operator's uid, so only the operator's ssh -L reaches its socket)
LIB = ".steam/debian-installation/steamapps/common"       # relative to the state dir's home/ (the client the bootstrapper makes)
CLIENT_SO = ".steam/debian-installation/linux64/steamclient.so"
TRIAL_HOME = "/home/player"
RELAY_HUB = "/home/player/.steam-relay"                   # the state dir's relay/ in the hub
RELAY_TRIAL = "/run/gab-steam-relay"                      # ... in a trial (read only)
SUBDIRS = ("home", "vnc", "relay")
SCRIPTS = "/opt/gab-steam"                                # docker/steam/ of this checkout, mounted read only into the hub (the relay
                                                          # also into trials): the hub scripts always match the code; a hub started
                                                          # without the mount (older gab) uses the image's own copies
HELPER = f"G={SCRIPTS}/gab-steam; [ -x $G ] || G=gab-steam; $G"
LABEL = "org.gameanything-bench.steam-hub"


def state_dir() -> Path: return Path(config.get("steam_state") or (config.path("games_dir") / "steam"))
def hub_name(): return config.get("steam_hub") or "gab-steam-hub"


def hub_image(backend):
    if config.get("steam_hub_image"): return config.get("steam_hub_image")
    from .docker import manifest, image_ref
    m = manifest(); h = m.get("steam_hub") or {}
    if not h: return image_ref("terraria")
    repo = h["repo"] if "/" in h["repo"] else f"{m['registry']}/{h['repo']}"
    if config.get("docker_tag"): return f"{repo}:{config.get('docker_tag')}"
    return f"{repo}@{h['digest']}" if h.get("digest") else f"{repo}:{h.get('tag') or m.get('tag') or 'latest'}"


def security_args():
    """the hub only, and only what the operator configured (see the module docstring): steam_hub_seccomp = a seccomp profile file
    (relative paths from the repo), steam_hub_apparmor = an AppArmor profile name. Default: none = Docker's defaults."""
    a = []
    sec = config.get("steam_hub_seccomp")
    if sec: a += ["--security-opt", f"seccomp={sec if os.path.isabs(sec) else config.REPO / sec}"]
    if config.get("steam_hub_apparmor"): a += ["--security-opt", f"apparmor={config.get('steam_hub_apparmor')}"]
    return a


def needs_steam(game):
    from .docker import manifest
    return bool(((manifest().get("games") or {}).get(game) or {}).get("steam"))


def check_state_dir(p=None):
    """-> list of problems with the host state dir (exists, owned by me, mode 700: the login token lives below it)"""
    p = Path(p or state_dir()); out = []
    if not p.is_dir(): return [f"{p} does not exist (gab steam init makes it)"]
    s = p.stat()
    if s.st_uid != os.getuid(): out.append(f"{p} is owned by uid {s.st_uid}, not by you ({os.getuid()})")
    if stat.S_IMODE(s.st_mode) & 0o077: out.append(f"{p} is mode {oct(stat.S_IMODE(s.st_mode))}: must be 700 (chmod 700 {p})")
    return out


class Hub:
    def __init__(self, backend):
        self.b = backend; self.name = hub_name(); self.st = state_dir()

    # ---- container
    def inspect(self):
        r = self.b._run(["inspect", "--format", "{{json .State}}|{{json .Config.Labels}}|{{.HostConfig.IpcMode}}", self.name], timeout=60)
        if r.returncode: return None
        s, l, ipc = r.stdout.strip().split("|", 2)
        return {"state": json.loads(s), "labels": json.loads(l) or {}, "ipc": ipc}

    def running(self):
        i = self.inspect(); return bool(i and i["state"].get("Running"))

    def image_uid(self, image):
        r = self.b._run(["image", "inspect", "--format", '{{index .Config.Labels "org.gameanything-bench.player-uid"}}', image], timeout=60)
        return r.stdout.strip() if r.returncode == 0 else ""

    def check_image_uid(self, image):
        """the image's game user must be the caller: else Steam's files on the host would belong to another host account"""
        u = self.image_uid(image)
        if u != str(os.getuid()):
            raise RuntimeError(f"{image}: its player uid is {u or '?'}, yours is {os.getuid()}: Steam's files (login token, library) "
                               f"would belong to host uid {u or '?'}. Build it as yourself: STEAM_ACCEPT_SSA=yes docker/build.sh steam "
                               f"(PLAYER_UID defaults to your uid for the steam image)")

    def init_state(self, image=None):
        """the host state dir and its home/ (Steam) and vnc/ (the login display's socket): mine, mode 700"""
        self.st.mkdir(parents=True, exist_ok=True); os.chmod(self.st, 0o700)
        for d in SUBDIRS: (self.st / d).mkdir(exist_ok=True); os.chmod(self.st / d, 0o700)
        bad = check_state_dir(self.st) + [x for d in SUBDIRS for x in check_state_dir(self.st / d)]
        if bad: raise RuntimeError("; ".join(bad))
        image = image or hub_image(self.b); self.b.ensure_image(image); self.check_image_uid(image)
        return self.st

    def check_owner(self):
        """after a start: home/ and the library are owned by the caller (no other host account may read Steam's login token)"""
        r = self.exec(f"stat -c '%u %a' {CPATH} {CPATH}/{LIB} 2>/dev/null; find {CPATH} -maxdepth 3 ! -uid $(id -u) -print -quit", timeout=60)
        lines = r.stdout.split("\n"); first = lines[0].split() if lines and lines[0] else []
        bad = []
        if first and first[0] != str(os.getuid()): bad.append(f"{self.st}/home is owned by uid {first[0]}")
        if first and first[1] != "700": bad.append(f"{self.st}/home is mode {first[1]}")
        stray = [l for l in lines[1:] if l.startswith("/")]
        if stray: bad.append(f"files not owned by you under {self.st}/home, e.g. {stray[0]}")
        return bad

    def run_args(self, image, vnc=False):
        return ["run", "-d", "--name", self.name, "--init", "--ipc", "shareable",
                "--network", config.get("steam_hub_network") or "bridge", "--shm-size", "1g", "--pids-limit", "8192",
                "--security-opt", "no-new-privileges", *security_args(), "--restart", "unless-stopped",
                "--label", f"{LABEL}=1", "--label", f"org.gameanything-bench.owner={os.environ.get('USER') or config.get('user')}",
                "-e", f"HOME={CPATH}", "-e", "DISPLAY=:101", "-e", f"GAB_STEAM_VNC={1 if vnc else 0}",
                "-v", f"{self.st}/home:{CPATH}", "-v", f"{self.st}/vnc:{VNC_DIR}",
                "-v", f"{self.st}/relay:{RELAY_HUB}", "-e", f"GAB_STEAM_RELAY_DIR={RELAY_HUB}",
                "-v", f"{config.REPO / 'docker' / 'steam'}:{SCRIPTS}:ro", "--entrypoint", f"{SCRIPTS}/gab-steam-hub", image]

    def up(self, vnc=False, image=None):
        if self.running():
            if vnc and not self.has_vnc():
                raise RuntimeError(f"{self.name} runs without VNC: `gab steam down` first, then `gab steam login`")
            return "already running"
        bad = check_state_dir(self.st)
        if bad: raise RuntimeError("Steam state dir: " + "; ".join(bad) + " (gab steam init)")
        if self.inspect(): self.b._run(["rm", "-f", self.name], timeout=120)        # a stopped hub of mine: start a fresh one
        for d in SUBDIRS:
            bad += check_state_dir(self.st / d)
        if bad: raise RuntimeError("Steam state dir: " + "; ".join(bad) + " (gab steam init)")
        image = image or hub_image(self.b); self.b.ensure_image(image); self.check_image_uid(image)
        r = self.b._run(self.run_args(image, vnc), timeout=300)
        if r.returncode: raise RuntimeError(f"starting the Steam hub failed: {r.stderr.strip()[-600:]}")
        bad = self.check_owner()
        if bad: self.b._run(["rm", "-f", self.name], timeout=120); raise RuntimeError("Steam hub stopped: " + "; ".join(bad))
        return "started"

    def down(self):
        if not self.inspect(): return "not running"
        self.helper("stop", timeout=60)
        for _ in range(30):
            if not self.running(): break
            time.sleep(1)
        self.b._run(["rm", "-f", self.name], timeout=120)
        return "stopped"

    def exec(self, cmd, timeout=120):
        return self.b._run(["exec", self.name, "bash", "-c", cmd], timeout=timeout)

    def exec_input(self, cmd, data, timeout=120):
        """cmd in the hub with `data` on its stdin (docker exec -i): how a password / Steam Guard code reaches xdotool -- never in an
        argv, a file or a log"""
        return self.b._run(["exec", "-i", self.name, "bash", "-c", cmd], timeout=timeout, input=data)

    def helper(self, args, timeout=120): return self.exec(f"{HELPER} {args}", timeout=timeout)

    def has_vnc(self):
        """the hub was started for a VNC login (its env; a vnc.sock file may be a stale leftover)"""
        r = self.b._run(["inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", self.name], timeout=60)
        return "GAB_STEAM_VNC=1" in (r.stdout or "").split()

    def status(self):
        if not self.running(): return {"hub": self.name, "running": False}
        r = self.helper("status", timeout=60)
        try: s = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError): s = {"error": (r.stdout + r.stderr)[-300:]}
        return {"hub": self.name, "container": True, **s}

    def wait(self, s=120):
        r = self.helper(f"wait {int(s)}", timeout=s + 60)
        return r.returncode == 0

    def await_logon(self, game, poll=60):
        """before the trial's container is created and OUTSIDE docker.py's machine-wide GPU lock: wait for the hub to be logged on
        (waiting inside trial_args would hold that lock, so no container of any game could start meanwhile)"""
        if not self.running() or config.get("steam_require_logon") in (False, "0", "false", "no"): return
        st = self.status()
        if not st.get("logged_on"): self.wait_logon(game, st, poll)

    def wait_logon(self, game, st, poll=60):
        """the hub fell off its login (one Steam account: a login elsewhere drops this one): wait for the
        user to log in again (steam_logon_wait_min, default 240 min) instead of failing every queued trial in seconds --
        a run that loses Steam then holds its slots and goes on by itself after `gab steam login`."""
        import time
        from ..util import log
        end = time.time() + 60 * float(config.get("steam_logon_wait_min") or 0)
        if time.time() < end: log(f"{game}: the Steam hub is not logged on; waiting up to {config.get('steam_logon_wait_min')} min for `gab steam login`")
        while time.time() < end and not st.get("logged_on"):
            time.sleep(poll); st = self.status()
        return st

    # ---- trials
    def trial_args(self, game):
        """docker run arguments of a Steam-game trial container (after --network none ...). Raises when the hub is not usable."""
        if not self.running():
            raise RuntimeError(f"{game} needs the Steam hub, which is not running: the operator starts it (gab steam up; first time: "
                               "gab steam login, docs in docker/README.md)")
        st = self.status()
        if config.get("steam_require_logon") not in (False, "0", "false", "no") and not st.get("logged_on"):
            raise RuntimeError(f"{game} needs the Steam hub logged on (gab steam status: {json.dumps(st)[:300]}); the operator logs in: gab steam login")
        if not st.get("client"):
            raise RuntimeError("the Steam hub has no client yet (first start downloads it; gab steam status)")
        h = f"{self.st}/home"
        lib = "rw" if os.environ.get("GAB_STEAM_LIBRARY_RW") == "1" else "ro"   # rw only for gab's own one-time prep (steamsetup)
        a = ["--ipc", f"container:{self.name}",
             # the hub's pid namespace: the Steam client watches every game process by its pid and drops the IPC pipe ("fatal stalled
             # cross-thread pipe (pipe is disconnected)") when it cannot see it -- measured on a logged-on hub: own pid
             # namespace -> SteamAPI_Init ok, then the game dies within seconds; --pid container:<hub> -> stays logged on
             "--pid", f"container:{self.name}",
             "-v", f"{h}/{LIB}:{TRIAL_HOME}/{LIB}:{lib}",
             # ... and at the hub's own path: Steam answers GetAppInstallDir() with ITS path (tModLoader finds Terraria that way;
             # without it: "Terraria Content folder not found")
             "-v", f"{h}/{LIB}:{CPATH}/{LIB}:{lib}",
             "-v", f"{h}/{CLIENT_SO}:{TRIAL_HOME}/.steam/sdk64/steamclient.so:ro",
             "--label", f"org.gameanything-bench.steam={self.name}",
             # every bash in the trial (docker exec ... bash -c: `game sh`, gab's bridge scripts) gets the kill builtin limited to
             # this container (OWN_PROC_TOOLS; written by trial_setup_cmd, bash skips a BASH_ENV that does not exist yet)
             "-e", f"BASH_ENV={OWN_PROC_BASH_ENV}"]
        a += ["-v", f"{self.st}/relay:{RELAY_TRIAL}:ro", "-e", f"GAB_STEAM_RELAY_DIR={RELAY_TRIAL}",
              "-v", f"{config.REPO / 'docker' / 'steam' / 'gab-steam-relay'}:{SCRIPTS}/gab-steam-relay:ro"]
        return a

    @staticmethod
    def trial_setup_cmd():
        """run as root in a Steam-game trial container right after it started: ~/.steam (docker made it for the read-only bind mounts,
        owned by root) handed to player, ~/.steam/steam.pid = 1 for libsteam_api, pkill / pgrep / killall / ps / pidof / kill limited to this container
        (own_proc_tools), and the IPC relay started as player"""
        return (own_proc_tools_cmd() + ' && '
                'S=/home/player/.steam; chown player:player $S && echo 1 > $S/steam.pid && chown player:player $S/steam.pid && '
                'R=/opt/gab-steam/gab-steam-relay; [ -x $R ] || R=gab-steam-relay; runuser -u player -- setsid nohup $R trial "$GAB_STEAM_RELAY_DIR" > /tmp/gab-steam-relay.log 2>&1 < /dev/null & '
                'for _ in $(seq 1 20); do grep -q 57343 "$GAB_STEAM_RELAY_DIR/ports" 2>/dev/null && '
                '(exec 3<>/dev/tcp/127.0.0.1/57343) 2>/dev/null && echo relay-up && break; sleep 0.5; done; true')


# Every Steam-game trial shares the hub's pid namespace (--pid container:<hub>), so a trial sees the game processes of all the
# other trials, same uid: an agent's `game sh 'pkill -f valheim.x86_64'` killed the games of the other running Valheim trials.
# These wrappers come first on PATH (/usr/local/bin) and match only processes of this container: pgrep / pkill /
# killall by the mount namespace (procps --ns/--nslist; the pid namespace is the shared one, the mount namespace is per container).
# Agents then went on with `ps -eo pid,cmd | grep Cities.x64 | ... kill -9 $p` and `kill -9 <pids from ps>` (the bash
# builtin, no PATH lookup), still killing every Cities game on the host (other trials' dev games and evaluations). So also:
#   ps, pidof   show only this container's processes (cgroup: /proc/<pid>/cgroup is world-readable and is "0::/" for this
#               container's processes, "0::/../docker-<other>.scope" for the hub's and the other trials')
#   kill        the builtin (a function in OWN_PROC_BASH_ENV; the trial gets BASH_ENV, which every `docker exec ... bash -c`, i.e.
#               `game sh` and gab's own bridge scripts, sources) and /usr/bin/kill (xargs kill) refuse pids of other containers
#               and kill -1; own pids / own process groups (gtr.sh: kill -- -$p) go through unchanged.
# /usr/bin/pkill, `builtin kill`, dash's kill and /proc stay reachable: this stops the common mistake, not a determined agent. gab's
# own scripts (gvh.sh, gtr.sh, gcs.sh, csp.sh) kill explicit pids of their own processes; nothing changes for them.
OWN_PROC_BASH_ENV = "/etc/gab-own-proc.bash"

# mawk-compatible: own(pid) = same cgroup as this awk process (nothing filtered when /proc/self/cgroup cannot be read)
_OWN_AWK = r"""function own(p,  f, l, s) { if (nf) return 1; if (p in m) return m[p]; f = "/proc/" p "/cgroup"; s = ""
  while ((getline l < f) > 0) s = s l "\n"; close(f); m[p] = (s != "" && s == me); return m[p] }
BEGIN { f = "/proc/self/cgroup"; while ((getline l < f) > 0) me = me l "\n"; close(f); nf = (me == "") }
"""

OWN_PROC_TOOLS = {
    "pgrep": '#!/bin/sh\nexec /usr/bin/pgrep --ns $$ --nslist mnt "$@"\n',
    "pkill": '#!/bin/sh\nexec /usr/bin/pkill --ns $$ --nslist mnt "$@"\n',
    "killall": r'''#!/bin/sh
# killall NAME...: only this container's processes (see gab/host/steam.py OWN_PROC_TOOLS)
sig=TERM; q=; rc=1; n=
while [ $# -gt 0 ]; do
  case "$1" in
    -s|--signal) sig=$2; shift;;
    --signal=*) sig=${1#*=};;
    -q|--quiet) q=1;;
    -[0-9]*|-[A-Z]*) sig=${1#-};;
    -*) ;;
    *) n=$1   # by process name, else by the program in the command line (names over 15 chars, argv[0])
       { [ ${#n} -le 15 ] && /usr/bin/pkill --ns $$ --nslist mnt -"$sig" -x "$n"; } ||
         /usr/bin/pkill --ns $$ --nslist mnt -"$sig" -f "(^|/)$n( |\$)" && rc=0;;
  esac; shift
done
[ $rc = 0 ] || [ -n "$q" ] || echo "$n: no process found" >&2
exit $rc
''',
    "ps": r'''#!/bin/sh
# ps: only this container's processes (see gab/host/steam.py OWN_PROC_TOOLS)
case " $* " in *" --help"*|*" -V "*|*" V "*|*" --version "*|*" --info "*|*" L "*) exec /usr/bin/ps "$@";; esac
F='__OWN_AWK__'
out=$(/usr/bin/ps "$@"); rc=$?
[ -n "$out" ] || exit $rc
# the PID column by the header (ps, ps aux, -ef, -eo pid,args ...); exit 3: no usable PID column
r=$(printf '%s\n' "$out" | awk "$F"'
NR == 1 { for (i = 1; i <= NF; i++) if ($i == "PID") c = i; if (!c) { bad = 1; exit } o[++k] = $0; next }
{ n++; if ($c !~ /^[0-9]+$/) { bad = 1; exit } if (own($c)) o[++k] = $0 }
END { if (bad) exit 3; for (i = 1; i <= k; i++) print o[i]; if (n && k == 1) exit 1 }'); s=$?
if [ $s = 3 ]; then   # no header (pid=,comm=) or columns with blanks before PID: our own pid column last, then cut off again
  out=$(/usr/bin/ps "$@" -ww -o pid= 2>/dev/null) ||
    { echo "ps: this container shows only its own processes; use a format with a PID column (ps -eo pid,args)" >&2; exit 1; }
  r=$(printf '%s\n' "$out" | awk "$F"'
{ if ($NF !~ /^[0-9]+$/) { if (NR == 1) print; next } n++; if (own($NF)) { sub(/[ \t]*[0-9]+[ \t]*$/, ""); print; k++ } }
END { if (n && !k) exit 1 }'); s=$?
fi
[ -z "$r" ] || printf '%s\n' "$r"
[ $s = 0 ] && exit $rc; exit $s
''',
    "pidof": r'''#!/bin/sh
# pidof: only this container's processes (see gab/host/steam.py OWN_PROC_TOOLS)
F='__OWN_AWK__'
out=$(/usr/bin/pidof "$@") || exit $?
case "$out" in *[!0-9\ ]*) printf '%s\n' "$out"; exit 0;; esac   # -h / -V
r=$(printf '%s\n' $out | awk "$F"'/^[0-9]+$/ && own($1) { printf "%s%s", (k++ ? " " : ""), $1 } END { if (k) print "" }')
[ -n "$r" ] || exit 1
printf '%s\n' "$r"
''',
    "kill": '#!/bin/bash\n# /usr/bin/kill (xargs kill, find -exec kill): the same rule as the kill builtin\n'
            f'. {OWN_PROC_BASH_ENV}\nkill "$@"\n',
    OWN_PROC_BASH_ENV: r'''# sourced by every bash in this Steam trial (BASH_ENV): kill only this container's processes (see gab/host/steam.py OWN_PROC_TOOLS)
__gab_own() {   # pid of this container? (same cgroup; anything goes when /proc/self/cgroup cannot be read)
  local me c
  { IFS= read -r me < /proc/self/cgroup; } 2>/dev/null || return 0
  [ -n "$me" ] || return 0
  { IFS= read -r c < "/proc/$1/cgroup"; } 2>/dev/null || return 1
  [ "$c" = "$me" ]
}
kill() {
  local a p rc=0 n=0 sig=() keep=()
  case "$1" in ""|-l|-L|-l*|--help) builtin kill "$@"; return;; esac
  case "$1" in
    -s|-n) sig=("$1" "$2"); shift 2;;
    --) ;;
    -*) sig=("$1"); shift;;
  esac
  [ "$1" = -- ] && shift
  for a in "$@"; do
    n=$((n + 1)); p=${a#-}
    if [[ $p =~ ^[0-9]+$ ]] && { [ "$a" = -1 ] || [ "$p" = 0 ]; }; then   # every process of the uid / the caller's own group
      if [ "$a" = -1 ]; then echo "bash: kill: (-1) - Operation not permitted (only this container's processes)" >&2; rc=1
      else keep+=("$a"); fi
    elif [[ $p =~ ^[0-9]+$ ]]; then
      if __gab_own "$p"; then keep+=("$a"); else echo "bash: kill: ($a) - No such process" >&2; rc=1; fi
    else keep+=("$a")   # job specs (%1), and what the builtin rejects anyway
    fi
  done
  [ $n -gt 0 ] && [ ${#keep[@]} -eq 0 ] && return $rc
  builtin kill "${sig[@]}" -- "${keep[@]}" || rc=$?
  return $rc
}
''',
}
for _k in ("ps", "pidof"): OWN_PROC_TOOLS[_k] = OWN_PROC_TOOLS[_k].replace("__OWN_AWK__", _OWN_AWK)


def own_proc_tools_cmd():
    """shell (as root): write OWN_PROC_TOOLS (bare names to /usr/local/bin, absolute paths as they are)"""
    import base64
    return " && ".join(f"echo {base64.b64encode(body.encode()).decode()} | base64 -d > {p} && chmod 755 {p}"
                       for name, body in OWN_PROC_TOOLS.items() for p in [name if name.startswith("/") else f"/usr/local/bin/{name}"])


def vnc_hint(st):
    sock = f"{st}/vnc/vnc.sock"
    return (f"The hub's screen is on the VNC unix socket {sock} (only you can reach it: {st} is yours, mode 700).\n"
            f"  on your laptop:  ssh -N -L 5901:{sock} {os.environ.get('USER')}@<this server>\n"
            f"  then a VNC viewer on localhost:5901 (no VNC password: the socket inside your 700 dir is the gate)")

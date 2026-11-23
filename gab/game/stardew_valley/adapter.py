"""Stardew Valley 1.6.15 + SMAPI 4.5.2 adapter (commercial game: never redistributed; the user brings their own copy).

Install (`install()`, once per sandbox, under a lock): <root> = <seat home>/gab-stardew
  base/game        a private copy of the user's Linux game folder WITH SMAPI installed (source: config stardew_game_dir /
                   $GAB_STARDEW_GAME_DIR, default /lab/game = the pool lab's copy; host `docker`: the user's folder bind-mounted
                   read-only at /lab/game, see docker_mounts(); sha256 of Stardew Valley.dll checked against 1.6.15), Content/ a link into it,
                   Mods/ = the SMAPI-bundled Console Commands + Save Backup + Content Patcher (below), smapi-internal/config.user.json:
                   no update check
  base/cp          Content Patcher 2.9.0 (Pathoschild, MIT): the source of the official GitHub tag content-patcher/2.9.0 (tarball
                   from codeload.github.com, sha256 pinned; Pathoschild publishes no binaries on GitHub, Nexus needs a login), built
                   here with ModBuildConfig from the local feed and put into base/game/Mods/ContentPatcher with its LICENSE, so every
                   instance has it the way it has SMAPI's bundled mods
  base/bridge      the harness mod GabBridge (bridge/GabBridge, C#), built here with ModBuildConfig
  base/save        the start save, made by the game itself: a vanilla run creates a new game the way the title menu does with
                   "skip intro" (farmer "Gab", Bench Farm, standard farm, fixed seed); the game saves it at once (new day 1)
  base/vanilla.json  the vanilla vocabulary the bridge compares against (item ids, villagers, locations, data entries, ...)
No EULA / ToS / account dialog: the game runs without the Steam client (one vanilla log line "Steam isn't loaded"), in a network
namespace without network (`unshare -rn`), sound off, no SMAPI update check, multiplayer server off.

Instance <root>/gab/<inst>: game/ (hard links of base/game, real Mods/ copy), HOME/.config/StardewValley (startup_preferences:
borderless window 1280x720, muted, no pause when unfocused; Saves/ = a fresh copy of the start save), gab/ (bridge requests,
bridge.json), session/ (pids, stdout). The game runs on a private Xvfb :200+seat (probe / gate runs :210+seat), GL on the GPU through VirtualGL when the
container has one (vglrun -d egl), else Mesa software GL.
A fresh start loads the start save: spring 1, year 1, 6:00, the farmer in bed in the farmhouse (as a new player after the intro).
mod/start.yaml {screen: title} starts at the title screen instead.

Deliverable mod/ (devkit/README.md): one SMAPI mod per folder (mod/<Name>/manifest.json; C# source = a .csproj built here with
Pathoschild.Stardew.ModBuildConfig, net6.0; or a ready mod folder; or a Content Patcher pack = manifest.json with ContentPackFor
Pathoschild.ContentPatcher + content.json), or a single mod at mod/ itself. Built in the sandbox (offline, NuGet from the lab's
local feed) and copied into the instance's Mods/ -- what a player does. The load test fails on Content Patcher's own error /
warning lines (a content.json it cannot read, patches it ignores or cannot apply).
"""
import hashlib, io, json, os, re, shlex, tarfile, time
from pathlib import Path

import yaml

from ... import config
from .. import base as gbase
from ..base import GameAdapter
from . import probes as P

HERE = Path(__file__).resolve().parent
LAB_GAME, LAB_SOURCES = "/lab/game", "/lab/sd-sources"      # in the sandbox (pool lab; docker: bind mounts, read-only)
GAME_SHA = {"f3e97f01d3fd2b1e6094fc8d2b59950aa6cb9d6cd1bf1b39d72d58edda8aad12": "1.6.15"}       # Stardew Valley.dll (Linux, Steam)
SMAPI_SHA = {"d0d40338afce227c7c69af555d7abd4a7077a4c9cf5fe2c2a94ae685cc79d88a": "4.5.2"}
START_SAVE = {"name": "Gab", "farm": "Bench", "favorite": "Games", "seed": 20261031}
STOCK_MODS = ("ConsoleCommands", "SaveBackup")
# Content Patcher: built from the official GitHub source tag (Pathoschild/StardewMods publishes no release binaries on GitHub)
CP_VER = "2.9.0"      # 2.9.1 (functionally identical: only its release pipeline changed) needs a C# 14 compiler (.NET 10 SDK); the lab has 8.0
CP_TAG = f"content-patcher/{CP_VER}"
CP_COMMIT = "807ca18be9291f301119a3fa01f49b6a59ac625f"
CP_URL = f"https://codeload.github.com/Pathoschild/StardewMods/tar.gz/refs/tags/{CP_TAG}"
CP_SRC_SHA256 = "02320a7b9f54b4ce60c74a3faf2d158c24e0d0c0191774275172c7005cd0b42f"
CP_DLL_SHA256 = "357475abd494818b9168427a35badf57fb03f20feaf77bf71035263de1900bbb"   # ContentPatcher.dll as built here (.NET SDK 8.0.132, ModBuildConfig 4.3.0)
CP_ID, CP_FOLDER = "Pathoschild.ContentPatcher", "ContentPatcher"
HARNESS_MODS = STOCK_MODS + ("GabBridge", CP_FOLDER)
DL_UA = "Mozilla/5.0 (X11; Linux x86_64)"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
BUILD_ENV = ("export HOME={root}/buildhome NUGET_PACKAGES={root}/nuget-packages DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 "
             "DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 MSBUILDDISABLENODEREUSE=1 DOTNET_CLI_UI_LANGUAGE=en; mkdir -p $HOME $NUGET_PACKAGES; ")
NUGET_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<!-- offline: packages from the lab's local feed only (gab) -->
<configuration><packageSources><clear /><add key="local" value="{feed}" /></packageSources></configuration>
"""
PREFS = """<?xml version="1.0" encoding="utf-8"?>
<StartupPreferences xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <startMuted>true</startMuted>
  <skipWindowPreparation>false</skipWindowPreparation>
  <sawAdvancedCharacterCreationIndicator>true</sawAdvancedCharacterCreationIndicator>
  <timesPlayed>1</timesPlayed>
  <windowMode>0</windowMode>
  <displayIndex>0</displayIndex>
  <gamepadMode>ForceOff</gamepadMode>
  <playerLimit>-1</playerLimit>
  <fullscreenResolutionX>1280</fullscreenResolutionX>
  <fullscreenResolutionY>720</fullscreenResolutionY>
  <languageCode>en</languageCode>
  <clientOptions>
    <pauseWhenOutOfFocus>false</pauseWhenOutOfFocus>
    <showMenuBackground>false</showMenuBackground>
    <ipConnectionsEnabled>false</ipConnectionsEnabled>
    <enableServer>false</enableServer>
    <enableFarmhandCreation>false</enableFarmhandCreation>
    <musicVolumeLevel>0</musicVolumeLevel>
    <soundVolumeLevel>0</soundVolumeLevel>
    <footstepVolumeLevel>0</footstepVolumeLevel>
    <ambientVolumeLevel>0</ambientVolumeLevel>
    <fullscreen>false</fullscreen>
    <windowedBorderlessFullscreen>true</windowedBorderlessFullscreen>
    <zoomLevel>1</zoomLevel>
    <uiScale>1</uiScale>
    <preferredResolutionX>1280</preferredResolutionX>
    <preferredResolutionY>720</preferredResolutionY>
  </clientOptions>
</StartupPreferences>
"""
SESSION_SH = r'''#!/bin/bash
# sd-session.sh INSTDIR DISPLAYNUM start|stop     Xvfb + SMAPI in the background; exact pids in INSTDIR/session
I=$1; DN=$2; ACT=$3; S=$I/session; mkdir -p $S
alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }
stop() {
  w=$(cat $S/wrap.pid 2>/dev/null); g=$(cat $S/smapi.pid 2>/dev/null); x=$(cat $S/xvfb.pid 2>/dev/null)
  [ -n "$w" ] && kill -- -$w 2>/dev/null; alive "$g" && kill $g 2>/dev/null
  for i in $(seq 1 40); do alive "$g" || break; sleep 0.25; done
  [ -n "$w" ] && kill -9 -- -$w 2>/dev/null; alive "$g" && kill -9 $g 2>/dev/null
  alive "$x" && kill $x 2>/dev/null; sleep 0.3; alive "$x" && kill -9 $x 2>/dev/null
  rm -f $S/wrap.pid $S/smapi.pid $S/xvfb.pid
}
if [ "$ACT" = stop ]; then stop; exit 0; fi
stop
lk=/tmp/.X$DN-lock; if [ -f $lk ]; then o=$(tr -dc 0-9 < $lk); alive "$o" || rm -f $lk /tmp/.X11-unix/X$DN; fi
Xvfb :$DN -screen 0 1280x720x24 -nolisten tcp > $S/xvfb.log 2>&1 < /dev/null & echo $! > $S/xvfb.pid
for i in $(seq 1 40); do [ -e /tmp/.X11-unix/X$DN ] && break; sleep 0.25; done
export HOME=$I/HOME XDG_CONFIG_HOME=$I/HOME/.config XDG_DATA_HOME=$I/HOME/.local/share DISPLAY=:$DN SDL_AUDIODRIVER=dummy \
  GAB_BRIDGE_DIR=$I/gab TERM=xterm SMAPI_NO_TERMINAL=true DOTNET_CLI_TELEMETRY_OPTOUT=1
: > $S/console.in; rm -f $I/gab/req/* $I/gab/ans/*
cd $I/game
# SMAPI's console reads stdin (fed from session/console.in); the wrapper ends when SMAPI ends. No network inside (unshare -rn).
NET="unshare -rn"; $NET true 2>/dev/null || NET=""
# GPU: VirtualGL renders on the container's GPU (EGL) and hands the frames to Xvfb; without a GPU vglrun is not used (Mesa
# software GL, llvmpipe: ~4 of the ~6 cores an instance used)
VGL=""; if command -v vglrun >/dev/null && ls /dev/nvidia[0-9]* >/dev/null 2>&1; then VGL="vglrun -d egl"; fi; echo "gl: ${VGL:-software}" > $S/gl.txt
setsid nohup $NET bash -c "tail -n0 -F $S/console.in 2>/dev/null | $VGL ./StardewModdingAPI & echo \$! > $S/smapi.pid; wait \$!; kill 0" \
  > $S/stdout.log 2>&1 < /dev/null & echo $! > $S/wrap.pid
for i in $(seq 1 20); do [ -s $S/smapi.pid ] && break; sleep 0.1; done
echo started $(cat $S/smapi.pid 2>/dev/null)
'''


def tree_hash(d):
    """sha256 over the relative paths + contents of a host directory (build cache key)"""
    h = hashlib.sha256()
    for p in sorted(Path(d).rglob("*")):
        if p.is_file() and not ({"bin", "obj", ".vs"} & set(p.relative_to(d).parts)):
            h.update(str(p.relative_to(d)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def read_manifest(p):
    try: return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception as e: return {"_error": f"{type(e).__name__}: {str(e)[:120]}"}


def layout(mod_dir):
    """-> [mod folders]: mod/ itself when it holds manifest.json, else its sub folders that do (start.yaml etc. ignored)"""
    m = Path(mod_dir)
    if not m.exists(): return []
    if (m / "manifest.json").exists(): return [m]
    return sorted(p for p in m.iterdir() if p.is_dir() and (p / "manifest.json").exists())


def start_screen(mod_dir):
    try: s = yaml.safe_load((Path(mod_dir) / "start.yaml").read_text()) or {}
    except Exception: s = {}
    return "title" if str(s.get("screen", "game")).lower() == "title" else "game"


class Adapter(GameAdapter):
    name = "stardew_valley"
    title = "Stardew Valley 1.6 (SMAPI)"
    screen = (1280, 720)
    extra_help = ("  console 'COMMAND'            a SMAPI console command in the running dev game (Console Commands: `help`, e.g.\n"
                  "                               console 'world_settime 1800', console 'debug warp Town 20 60'; testing only, players cannot)\n")

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/gab-stardew"
        # docker: the host folder (config stardew_game_dir) is mounted at LAB_GAME; pool / local: the path in the sandbox itself
        self.docker = getattr(getattr(seat, "backend", None), "name", "") == "docker"
        self.src = LAB_GAME if self.docker else (config.get("stardew_game_dir") or LAB_GAME)
        self.feed = os.environ.get("GAB_STARDEW_NUGET", "/lab/nuget")
        n = int(seat.seat) if str(seat.seat).isdigit() else 1
        self.dn, self.probe_dn = 200 + n, 210 + n
        self._mods = None

    def requirements(self):
        return {"game": "stardew_valley", "gpu": True, "mem_gb": 1.5, "steam": False, "own_copy": True,
                "store": "Stardew Valley 1.6.15, Linux build (Steam app 413150 / GOG); the Steam client is not needed at run time",
                "license": "commercial: not redistributed, bring your own copy ($GAB_STARDEW_GAME_DIR, SMAPI 4.5.2 installed in it)",
                "mod_loader": "SMAPI 4.5.2 (+ .NET 8 SDK, Pathoschild.Stardew.ModBuildConfig 4.3.0 in a local NuGet feed)",
                "frameworks": f"Content Patcher {CP_VER} (MIT, built by install() from the GitHub tag {CP_TAG}, source sha256 pinned)",
                "hosts": ["pool", "docker", "local"], "eula": None}

    @classmethod
    def docker_mounts(cls):
        """host `docker`: the user's own game folder (config stardew_game_dir / $GAB_STARDEW_GAME_DIR: Stardew Valley 1.6.15 Linux
        with SMAPI 4.5.2, docker/stardew_valley/get-game.sh) read-only at /lab/game, and the decompiled sources (config
        stardew_sources_dir, default <game dir>/../sd-sources; docker/stardew_valley/decompile.sh) at /lab/sd-sources when they
        exist. Nothing of the game is in the image. -> [(host, container, mode)]"""
        return docker_mounts(config.get("stardew_game_dir"), config.get("stardew_sources_dir"))

    def input(self, inst, s):
        """the game polls keyboard / mouse once per frame: a press and release in the same frame is lost, so a key or click is
        held 0.1 s (base.input uses xdotool key / click, which release at once)"""
        k, q = s["do"], shlex.quote
        if k == "key": return self._x(inst, " ; ".join(f"xdotool keydown {q(x)} sleep 0.1 keyup {q(x)}" for x in str(s["keys"]).split()))
        if k == "click":
            b = gbase._button(s.get("button", 1))
            return self._x(inst, f"xdotool mousemove {int(s['x'])} {int(s['y'])} sleep 0.1 mousedown {b} sleep 0.1 mouseup {b}")
        return super().input(inst, s)

    # ---- paths
    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def udir(self, inst): return f"{self.idir(inst)}/HOME/.config/StardewValley"
    def smapi_log(self, inst): return f"{self.udir(inst)}/ErrorLogs/SMAPI-latest.txt"
    def display(self, inst): return f":{self._dn(inst)}"
    def _dn(self, inst):
        """probe runs, the base maker and the agent's gate check (`game build` while the dev game runs) use the second display"""
        return self.probe_dn if inst.startswith(("probe-", "base-")) or inst.endswith("-gate") else self.dn
    def paths(self, inst):
        return {"instance_dir": self.idir(inst), "game_dir": f"{self.idir(inst)}/game", "mods": f"{self.idir(inst)}/game/Mods",
                "saves": f"{self.udir(inst)}/Saves", "smapi_log": self.smapi_log(inst), "game_stdout": f"{self.idir(inst)}/session/stdout.log",
                "decompiled_sources": LAB_SOURCES if self.seat.exec(f"test -d {LAB_SOURCES} && echo y").stdout.strip() == "y" else None,
                "nuget_feed": self.feed}

    def _br(self, inst, req, timeout=5.0):
        """one bridge request (dict) or a list -> answer(s)"""
        r = self.seat.exec(f"python3 {self.root}/bin/sdbr.py req {self.idir(inst)}/gab {shlex.quote(json.dumps(req))} --timeout {timeout} "
                           f"--pid {self.idir(inst)}/session/smapi.pid", timeout=timeout + 30)
        try: return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError): return {"error": f"bridge client: {(r.stdout + r.stderr).strip()[-300:]}"}

    def _wait(self, inst, cond, timeout):
        r = self.seat.exec(f"python3 {self.root}/bin/sdbr.py wait {self.idir(inst)}/gab {shlex.quote(cond)} --timeout {timeout} "
                           f"--pid {self.idir(inst)}/session/smapi.pid", timeout=timeout + 40)
        try: return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError): return {"ok": False, "error": (r.stdout + r.stderr).strip()[-300:]}

    # ---- install (base copy, bridge, start save, vanilla vocabulary)
    def _put_tree(self, src, dst):
        """host dir -> sandbox dir (tar, base64 chunks; small trees only)"""
        self.seat.copy_in(src, dst)

    def bridge_hash(self):
        return hashlib.sha256((HERE / "bridge" / "GabBridge" / "ModEntry.cs").read_bytes() + (HERE / "bridge" / "GabBridge" / "GabBridge.csproj").read_bytes()
                              + (HERE / "bridge" / "GabBridge" / "manifest.json").read_bytes()).hexdigest()[:12]

    def _tools(self):
        files = {f"{self.root}/bin/sdbr.py": (HERE / "sdbr.py").read_text(), f"{self.root}/bin/sd-session.sh": SESSION_SH}
        have = self.seat.exec("md5sum " + " ".join(files) + " 2>/dev/null").stdout
        for p, t in files.items():
            if hashlib.md5(t.encode()).hexdigest() not in have: self.seat.put(p, t)

    def install(self, log=lambda s: None):
        """base copy + bridge + start save + vanilla.json; idempotent; one installer at a time (seats provision in parallel)"""
        self._tools()
        bh = self.bridge_hash()
        done = lambda i: i.get("bridge") == bh and i.get("save") and i.get("ok") and i.get("cp") == CP_VER
        info = self._info()
        if done(info): return info
        lockd, t0 = f"{self.root}/locks/install.lock", time.time()
        while self.seat.exec(f"mkdir -p {self.root}/locks && mkdir {lockd} 2>/dev/null && echo got").stdout.strip() != "got":
            if time.time() - t0 > 2400: raise RuntimeError(f"stardew install: {lockd} held for 40 min")
            self.seat.exec(f"find {lockd} -maxdepth 0 -mmin +45 -exec rmdir {{}} \\; 2>/dev/null")   # a crashed installer's lock
            time.sleep(10); info = self._info()
            if done(info): return info
        try: return self._install(info, bh, log)
        finally: self.seat.exec(f"rmdir {lockd}")

    def _install(self, info, bh, log):
        b = f"{self.root}/base"
        # 1. game copy (once): everything but Mods / backups; Content linked to the source (read only, ~650 MB)
        if not info.get("game_sha") or self.seat.exec(f"test -f '{b}/game/Stardew Valley.dll' && echo y").stdout.strip() != "y":
            log("stardew: copying the game into the sandbox base")
            sh = (f"set -e; S={shlex.quote(self.src)}; test -f \"$S/Stardew Valley.dll\" || {{ echo 'no Stardew Valley.dll in '$S; exit 3; }}; "
                  f"test -f \"$S/StardewModdingAPI.dll\" || {{ echo 'SMAPI is not installed in '$S' (run the SMAPI 4.5.2 installer on your game folder)'; exit 3; }}; "
                  f"rm -rf {b}/game.tmp; mkdir -p {b}/game.tmp/Mods; cd \"$S\"; "
                  f"for e in * .[!.]*; do case \"$e\" in Mods|save-backups|Content|'.[!.]*') ;; *) cp -a \"$e\" {b}/game.tmp/;; esac; done; "
                  f"ln -s \"$(readlink -f \"$S/Content\")\" {b}/game.tmp/Content; "
                  f"for m in {' '.join(STOCK_MODS)}; do [ -d \"$S/Mods/$m\" ] && cp -a \"$S/Mods/$m\" {b}/game.tmp/Mods/; done; "
                  f"rm -rf {b}/game.tmp/Mods/*/backups; "
                  f"echo '{{\"CheckForUpdates\": false, \"ListenForConsoleInput\": true}}' > {b}/game.tmp/smapi-internal/config.user.json; "
                  f"rm -rf {b}/game; mv {b}/game.tmp {b}/game; sha256sum \"{b}/game/Stardew Valley.dll\" {b}/game/StardewModdingAPI.dll")
            r = self.seat.exec(f"mkdir -p {b} && bash -c {shlex.quote(sh)}", timeout=1800)
            if r.returncode: raise RuntimeError(f"stardew install: {(r.stdout + r.stderr)[-600:]}")
            sums = [l.split()[0] for l in r.stdout.strip().splitlines()[-2:]]
            info.update(game_sha=sums[0], smapi_sha=sums[1], game=GAME_SHA.get(sums[0], "unknown build"), smapi=SMAPI_SHA.get(sums[1], "unknown"))
        # 1b. Content Patcher into base/game/Mods (so every instance has it, like SMAPI's bundled mods)
        if info.get("cp") != CP_VER or self.seat.exec(f"test -f {b}/game/Mods/{CP_FOLDER}/ContentPatcher.dll && echo y").stdout.strip() != "y":
            log(f"stardew: Content Patcher {CP_VER} (GitHub source tag, sha256 pinned) -> build -> Mods/{CP_FOLDER}")
            info.update(self._install_cp())
        # 2. bridge build
        if info.get("bridge") != bh or self.seat.exec(f"test -f {b}/bridge/GabBridge/GabBridge.dll && echo y").stdout.strip() != "y":
            log("stardew: building the bridge")
            self.seat.exec(f"rm -rf {b}/bridge_src && mkdir -p {b}/bridge_src")
            self._put_tree(HERE / "bridge" / "GabBridge", f"{b}/bridge_src/GabBridge")
            self.seat.put(f"{b}/bridge_src/nuget.config", NUGET_CONFIG.format(feed=self.feed))
            ok, lg = self._dotnet(f"{b}/bridge_src/GabBridge", f"{b}/bridge.tmp")
            if not ok: raise RuntimeError(f"stardew: the bridge did not build:\n{lg[-1500:]}")
            self.seat.exec(f"rm -rf {b}/bridge && mv {b}/bridge.tmp {b}/bridge", check=True)
            info["bridge"] = bh; info.pop("ok", None)
        # 3. start save + vanilla vocabulary, made by the game itself on a vanilla instance
        if not info.get("ok"):
            log("stardew: creating the start save (new game, skip intro) and the vanilla baseline")
            info.update(self._make_base_save())
            info["ok"] = True
        info["made"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.seat.put(f"{b}/info.json", json.dumps(info, indent=1))
        return info

    def _install_cp(self):
        """Content Patcher CP_VER: download the official source tarball of the GitHub tag (generic User-Agent), check its sha256,
        unpack ContentPatcher/ + Common/ + the root build props + LICENSE, build with ModBuildConfig from the local feed (the
        tag asks for 4.4.0; the lab feed has 4.3.0: build tooling only; the manifest's %ProjectVersion% placeholder, which 4.4.0
        fills from the .csproj, is filled here), add LICENSE + SOURCE.txt, -> base/game/Mods/ContentPatcher"""
        b = f"{self.root}/base"; dl = f"{self.root}/dl"; tgz = f"{dl}/StardewMods-{CP_TAG.replace('/', '-')}.tar.gz"; src = f"{b}/cp_src"
        sh = (f"set -e; mkdir -p {dl}; cd {dl}; if ! echo '{CP_SRC_SHA256}  {tgz}' | sha256sum -c - >/dev/null 2>&1; then "
              f"curl -fsSL --max-time 300 -A {shlex.quote(DL_UA)} -o cp.part {CP_URL}; "
              f"echo '{CP_SRC_SHA256}  cp.part' | sha256sum -c - || {{ echo 'CP-SHA-MISMATCH'; exit 4; }}; mv -f cp.part {tgz}; fi; "
              f"rm -rf {src}; mkdir -p {src}; "
              f"python3 -I - {tgz} {src} <<'PY'\nimport sys, tarfile, os\n"
              "keep = ('ContentPatcher/', 'Common/', 'Directory.Build.props', 'Directory.Packages.props', 'LICENSE')\n"
              "dst = os.path.realpath(sys.argv[2]); t = tarfile.open(sys.argv[1])\n"
              "for m in t.getmembers():\n    rel = m.name.split('/', 1)[1] if '/' in m.name else ''\n"
              "    if not rel.startswith(keep) or not (m.isfile() or m.isdir()): continue\n"
              "    out = os.path.realpath(os.path.join(dst, rel))\n"
              "    if not out.startswith(dst + os.sep): sys.exit('unsafe path in tarball: ' + m.name)\n"
              "    if m.isdir(): os.makedirs(out, exist_ok=True); continue\n"
              "    os.makedirs(os.path.dirname(out), exist_ok=True); open(out, 'wb').write(t.extractfile(m).read())\nPY\n"
              f"sed -i 's#\"Pathoschild.Stardew.ModBuildConfig\" Version=\"[0-9.]*\"#\"Pathoschild.Stardew.ModBuildConfig\" Version=\"4.3.0\"#' {src}/Directory.Packages.props; "
              f"grep -q 'ModBuildConfig\" Version=\"4.3.0' {src}/Directory.Packages.props; "
              # ModBuildConfig 4.4.0 fills the manifest's %ProjectVersion% from the .csproj <Version>; 4.3.0 does not: same substitution here
              f"sed -i 's#%ProjectVersion%#{CP_VER}#' {src}/ContentPatcher/manifest.json; grep -q '\"Version\": \"{CP_VER}\"' {src}/ContentPatcher/manifest.json; "
              f"echo CP-SRC-OK")
        r = self.seat.exec(f"bash -c {shlex.quote(sh)}", timeout=420)
        if "CP-SRC-OK" not in r.stdout: raise RuntimeError(f"Content Patcher {CP_VER} download / unpack failed: {(r.stdout + r.stderr)[-800:]}")
        self.seat.put(f"{src}/nuget.config", NUGET_CONFIG.format(feed=self.feed))
        ok, lg = self._dotnet(f"{src}/ContentPatcher", f"{b}/cp.tmp")
        if not ok: raise RuntimeError(f"Content Patcher {CP_VER} did not build:\n{lg[-1500:]}")
        note = (f"Content Patcher {CP_VER} by Pathoschild (Jesse Plamondon-Willard), MIT licence (LICENSE.txt in this folder).\n"
                f"Source: https://github.com/Pathoschild/StardewMods tag {CP_TAG} (commit {CP_COMMIT}),\n  {CP_URL}\n"
                f"  sha256 {CP_SRC_SHA256}\nBuilt unmodified by GameAnything-Bench (gab/game/stardew_valley/adapter.py) with "
                f"Pathoschild.Stardew.ModBuildConfig 4.3.0 (the tag pins 4.4.0; build tooling only;\n  manifest.json %ProjectVersion% filled with {CP_VER} as 4.4.0 does).\n")
        self.seat.put(f"{b}/cp.tmp/{CP_FOLDER}/SOURCE.txt", note)
        r = self.seat.exec(f"set -e; cp {src}/LICENSE {b}/cp.tmp/{CP_FOLDER}/LICENSE.txt; rm -rf {b}/cp; mv {b}/cp.tmp {b}/cp; "
                           f"rm -rf {b}/game/Mods/{CP_FOLDER}; cp -a {b}/cp/{CP_FOLDER} {b}/game/Mods/{CP_FOLDER}; "
                           f"sha256sum {b}/game/Mods/{CP_FOLDER}/ContentPatcher.dll", timeout=120)
        if r.returncode: raise RuntimeError(f"Content Patcher install: {(r.stdout + r.stderr)[-600:]}")
        dsha = r.stdout.split()[0]
        if CP_DLL_SHA256 and dsha != CP_DLL_SHA256: print(f"stardew: note: ContentPatcher.dll sha256 {dsha} differs from the reference build {CP_DLL_SHA256} (other .NET SDK?)")
        return {"cp": CP_VER, "cp_src_sha256": CP_SRC_SHA256, "cp_dll_sha256": dsha}

    def _dotnet(self, proj_dir, out_dir):
        """dotnet build of one mod project; deploys (ModBuildConfig) into out_dir/<ModFolder>. -> (ok, log)"""
        b = f"{self.root}/base"
        sh = (BUILD_ENV.format(root=self.root) + f"cd {shlex.quote(proj_dir)} && rm -rf bin obj && mkdir -p {out_dir} && "
              f"timeout 600 dotnet build -c Release -nologo /p:GamePath={b}/game /p:GameModsPath={out_dir} "
              f"/p:EnableModDeploy=true /p:EnableModZip=false /p:CopyModReferencesToBuildOutput=false 2>&1")
        r = self.seat.exec(sh, timeout=700)
        lines = [l for l in r.stdout.splitlines() if "CS9057" not in l]
        return r.returncode == 0, "\n".join(lines)

    def _make_base_save(self):
        inst = "base-maker"; d = self.idir(inst); b = f"{self.root}/base"
        self._fresh_instance(inst, save=False, bridge_cfg={"newgame": START_SAVE, "vanilla": None})
        try:
            self._session(inst, "start")
            w = self._wait(inst, "screen=in game", 240)
            if not w.get("ok"): raise RuntimeError(f"start save: the new game did not come up: {w} {self._fails(inst)}")
            save = ""
            for _ in range(60):                                      # the game saves the new day by itself (SaveGameMenu)
                save = self.seat.exec(f"ls {self.udir(inst)}/Saves 2>/dev/null | head -1").stdout.strip()
                ok = save and self.seat.exec(f"test -f {self.udir(inst)}/Saves/{save}/SaveGameInfo && echo y").stdout.strip() == "y"
                if ok and self._wait(inst, "screen=in game", 30).get("ok"): break
                time.sleep(2)
            else: raise RuntimeError("start save: no save written")
            time.sleep(3)
            # the vanilla vocabulary from the save as every start loads it (loading adds e.g. the Cellar2..8 locations)
            self._br(inst, {"cmd": "exit_title"})
            if not self._wait(inst, "screen=title", 90).get("ok"): raise RuntimeError("start save: exit to title failed")
            time.sleep(2); self._br(inst, {"cmd": "load", "save": save})
            if not self._wait(inst, "screen=in game", 150).get("ok"): raise RuntimeError(f"start save: reload failed {self._fails(inst)}")
            time.sleep(3)
            dump = self._br(inst, {"cmd": "dump_vanilla", "path": f"{b}/vanilla.json"}, timeout=120)
            if not dump.get("ok"): raise RuntimeError(f"vanilla dump failed: {dump}")
        finally: self._session(inst, "stop")
        self.seat.exec(f"rm -rf {b}/save && mkdir -p {b}/save && cp -a {self.udir(inst)}/Saves/{save} {b}/save/ && rm -rf {d}", check=True)
        return {"save": save, "vanilla": {k: dump.get(k) for k in ("items", "npcs", "locations", "assets", "vocab", "seconds")}}

    def _info(self):
        try: return json.loads(self.seat.exec(f"cat {self.root}/base/info.json 2>/dev/null").stdout or "{}")
        except ValueError: return {}

    # ---- instances
    def _fresh_instance(self, inst, save=True, bridge_cfg=None):
        d, b = self.idir(inst), f"{self.root}/base"; self.stop(inst)
        u = self.udir(inst)
        sh = (f"set -e; rm -rf {d}; mkdir -p {d}/gab/req {d}/gab/ans {d}/session {u}/Saves; cp -al {b}/game {d}/game; "
              f"rm -rf {d}/game/Mods {d}/game/smapi-internal/config.user.json; cp -a {b}/game/Mods {d}/game/Mods; "
              f"cp {b}/game/smapi-internal/config.user.json {d}/game/smapi-internal/config.user.json; "
              f"cp -a {b}/bridge/GabBridge {d}/game/Mods/GabBridge")
        if save: sh += f"; cp -a {b}/save/* {u}/Saves/"
        self.seat.exec(sh, check=True, timeout=300)
        self.seat.put(f"{u}/startup_preferences", PREFS)
        cfg = {"autoload": None, "vanilla": f"{b}/vanilla.json", **(bridge_cfg or {})}
        if save and "autoload" not in (bridge_cfg or {}): cfg["autoload"] = self._info().get("save")
        self.seat.put(f"{d}/gab/bridge.json", json.dumps(cfg))
        self.seat.put(f"{d}/mods.json", "[]")

    def provision(self, inst):
        self.install()
        self._fresh_instance(inst); self._mods = []
        return {"instance": inst, "dir": self.idir(inst), "display": self.display(inst), "save": self._info().get("save")}

    def build(self, inst, mod_dir=None):
        """the gate's build: structure check of mod/, then every C# project is compiled in the sandbox (cached by content
        hash). -> (ok, log): the error text (compiler errors, manifest problems), not just a status"""
        r = self._build(mod_dir)
        return (True, r["log"]) if r["ok"] else (False, gbase.gate_log(r.get("errors") or [], r.get("reason") or "build failed", r["log"]))

    def _build(self, mod_dir):
        mods = layout(mod_dir) if mod_dir else []
        L = []
        if mod_dir is not None and not mods: L.append("mod/ holds no SMAPI mod: put each mod in mod/<Name>/ with a manifest.json (or manifest.json at mod/ itself)")
        ids = {}
        for m in mods:
            mf = read_manifest(m / "manifest.json")
            if "_error" in mf: L.append(f"{m.name}/manifest.json: not valid JSON ({mf['_error']})"); continue
            if not NAME_RE.match(m.name): L.append(f"{m.name}: folder name must match [A-Za-z0-9_.-]+")
            if m.name in HARNESS_MODS: L.append(f"{m.name}: name taken by SMAPI / the harness / Content Patcher (installed already)")
            uid = mf.get("UniqueID") or mf.get("UniqueId")
            if not uid: L.append(f"{m.name}/manifest.json: no UniqueID")
            elif uid.lower() in (CP_ID.lower(), "gab.gabbridge"): L.append(f"{m.name}: UniqueID {uid} is taken (Content Patcher {CP_VER} is installed already)")
            elif uid in ids: L.append(f"{m.name}: UniqueID {uid} also used by {ids[uid]}")
            else: ids[uid] = m.name
            projs = sorted(m.glob("*.csproj"))
            cp = mf.get("ContentPackFor") or {}
            if not projs and not mf.get("EntryDll") and not cp: L.append(f"{m.name}/manifest.json: neither EntryDll (C# mod) nor ContentPackFor (content pack)")
            if cp and (projs or mf.get("EntryDll")): L.append(f"{m.name}/manifest.json: a mod is either a C# mod (EntryDll) or a content pack (ContentPackFor), not both")
            if cp and not isinstance(cp, dict): L.append(f"{m.name}/manifest.json: ContentPackFor must be an object {{\"UniqueID\": \"{CP_ID}\"}}"); cp = {}
            if projs and not mf.get("EntryDll"): L.append(f"{m.name}/manifest.json: a C# project needs EntryDll")
            if not projs and mf.get("EntryDll") and not (m / mf["EntryDll"]).exists(): L.append(f"{m.name}: no .csproj to build and no {mf['EntryDll']} in the folder")
            fw = str(cp.get("UniqueID") or cp.get("UniqueId") or "") if cp else ""
            if cp and fw.lower() == CP_ID.lower():
                if not (m / "content.json").exists(): L.append(f"{m.name}: a Content Patcher pack needs content.json next to manifest.json")
            elif cp and fw not in _all_ids(mods):
                L.append(f"{m.name}: a content pack for {fw or '(no UniqueID)'}, which is not installed (installed frameworks: "
                         f"Content Patcher {CP_VER}; or hand in that framework mod too)")
        if L: return {"ok": False, "log": "\n".join(L), "mods": [], "reason": "mod/ is not a valid SMAPI mod folder layout", "errors": L}
        if not mods: return {"ok": True, "log": "ok: no mods", "mods": []}
        self.install()
        h = tree_hash(mod_dir); bd = f"{self.root}/builds/{h}"
        done = self.seat.exec(f"cat {bd}/result.json 2>/dev/null").stdout
        try: res = json.loads(done)
        except ValueError: res = None
        if res: return res
        self.seat.exec(f"rm -rf {bd} && mkdir -p {bd}/src {bd}/out", check=True)
        logs, ok, errs, failed = [], True, [], []
        for m in mods:
            self._put_tree(m, f"{bd}/src/{m.name}")
            if not sorted(m.glob("*.csproj")):
                self.seat.exec(f"cp -a {bd}/src/{m.name} {bd}/out/{m.name}", check=True); logs.append(f"{m.name}: copied as is (no .csproj)"); continue
        self.seat.put(f"{bd}/src/nuget.config", NUGET_CONFIG.format(feed=self.feed))
        for m in mods:
            projs = sorted(m.glob("*.csproj"))
            if not projs: continue
            if len(projs) > 1: logs.append(f"{m.name}: several .csproj, building {projs[0].name}")
            b_ok, lg = self._dotnet(f"{bd}/src/{m.name}", f"{bd}/out")
            ok &= b_ok
            if not b_ok: failed.append(m.name); errs += [f"{m.name}: {x}" if len(mods) > 1 else x for x in build_errors(lg)]
            logs.append(f"== {m.name}: {'built' if b_ok else 'BUILD FAILED'}\n" + ("\n".join(l for l in lg.splitlines() if "warning CS" in l)[-800:] if b_ok else lg[-2500:]))
            if b_ok:   # ModBuildConfig deploys to out/<project name>: rename to the deliverable's folder name
                self.seat.exec(f"cd {bd}/out && P={shlex.quote(projs[0].stem)}; [ -d \"$P\" ] && [ \"$P\" != {shlex.quote(m.name)} ] && "
                               f"rm -rf {shlex.quote(m.name)} && mv \"$P\" {shlex.quote(m.name)}; ls {shlex.quote(m.name)}/manifest.json")
        built = self.seat.exec(f"ls {bd}/out").stdout.split()
        miss = [m.name for m in mods if m.name not in built]
        if ok and miss:
            ok = False; errs.append(f"no deployed folder for {miss} (ModBuildConfig deploys the build to Mods/<project name>)")
        res = {"ok": ok, "log": "\n".join(logs)[-6000:], "mods": [m.name for m in mods], "dir": f"{bd}/out", "hash": h,
               "reason": None if ok else (f"C# build failed: {', '.join(failed)}" if failed else "the build deployed no mod folder"), "errors": errs[:gbase.ERR_N]}
        if ok: self.seat.put(f"{bd}/result.json", json.dumps(res))
        self.seat.exec(f"ls -dt {self.root}/builds/*/ 2>/dev/null | tail -n +40 | xargs -r rm -rf")        # keep the last 40 builds
        return res

    def install_mod(self, inst, mod_dir):
        """build (cached) and copy every mod folder into the instance's Mods/ -- what a player does with a downloaded mod"""
        r = self._build(mod_dir) if mod_dir else {"ok": True, "mods": []}
        if not r["ok"]: raise RuntimeError(f"mod does not build:\n{r['log'][-2000:]}")
        d = self.idir(inst)
        keep = " ".join(HARNESS_MODS)
        self.seat.exec(f"cd {d}/game/Mods && for m in *; do case \" {keep} \" in *\" $m \"*) ;; *) rm -rf \"$m\";; esac; done")
        for m in r["mods"]: self.seat.exec(f"cp -a {r['dir']}/{shlex.quote(m)} {d}/game/Mods/", check=True)
        self._mods = list(r["mods"])
        self.seat.put(f"{d}/mods.json", json.dumps({"mods": self._mods, "screen": start_screen(mod_dir) if mod_dir else "game"}))
        return {"mods": self._mods}

    def attach(self, inst, mod_dir): self._mods = None

    def _installed(self, inst):
        try: j = json.loads(self.seat.exec(f"cat {self.idir(inst)}/mods.json 2>/dev/null").stdout or "{}")
        except ValueError: j = {}
        return j if isinstance(j, dict) else {"mods": j}

    # ---- session
    def _session(self, inst, act, dn=None):
        self._tools()
        return self.seat.exec(f"bash {self.root}/bin/sd-session.sh {self.idir(inst)} {dn or self._dn(inst)} {act}", timeout=60)

    def _alive(self, inst):
        return self.seat.exec(f"p=$(cat {self.idir(inst)}/session/smapi.pid 2>/dev/null); [ -n \"$p\" ] && kill -0 $p && echo up").stdout.strip() == "up"

    def _reset_save(self, inst):
        u = self.udir(inst)
        self.seat.exec(f"rm -rf {u}/Saves && mkdir -p {u}/Saves && cp -a {self.root}/base/save/* {u}/Saves/ && rm -rf {u}/ErrorLogs")

    def start(self, inst, timeout=240):
        """fresh start: the start save (or the title screen with mod/start.yaml screen: title); ok when the bridge reports the
        farmer in game (or the title), the window is up and the game still runs"""
        self.stop(inst); self._reset_save(inst)
        scr = self._installed(inst).get("screen", "game")
        cfg = {"autoload": None if scr == "title" else self._info().get("save"), "vanilla": f"{self.root}/base/vanilla.json"}
        self.seat.put(f"{self.idir(inst)}/gab/bridge.json", json.dumps(cfg))
        self._session(inst, "start")
        w = self._wait(inst, "screen=title" if scr == "title" else "screen=in game", timeout)
        if not w.get("ok"):
            why = ("the game exited during start-up" if w.get("error") == "game not running"
                   else f"timed out after {timeout} s; last bridge answer: {json.dumps({k: w.get(k) for k in ('screen', 'menu', 'error')})}")
            return False, f"start failed: {why}\n" + self._fails(inst)
        self._x(inst, "for w in $(xdotool search --name '^Stardew' 2>/dev/null); do xdotool windowmove $w 0 0; done; xdotool mousemove 1270 10")
        self._br(inst, {"cmd": "state"}, 10)             # warm-up: the first state read JIT-compiles the bridge's readers
        time.sleep(1.5)
        if not self._alive(inst): return False, "the game exited right after start\n" + self._fails(inst)
        return True, f"{scr}: {w.get('location') or ''} {w.get('time') or ''} (waited {w.get('waited_s')} s)"

    def _fails(self, inst, n=40):
        """the actual error text of a failed start / load: SMAPI ERROR / skipped-mod lines + exception heads + stdout tail, deduplicated"""
        lg = self.seat.exec(f"cat {self.smapi_log(inst)} 2>/dev/null").stdout
        out = self.seat.exec(f"tail -c 20000 {self.idir(inst)}/session/stdout.log 2>/dev/null").stdout
        return "\n".join(relevant_errors(lg, out, n)) or "(no error lines in the SMAPI log / game output)"

    def stop(self, inst):
        self.seat.exec(f"test -f {self.root}/bin/sd-session.sh && bash {self.root}/bin/sd-session.sh {self.idir(inst)} {self._dn(inst)} stop")

    def load_check(self, inst, out_dir=None):
        """the gate's load test: start with the mods (SMAPI loads each of them, none skipped / failed), the start save comes up
        and the game is still running after 10 s in game. -> (ok, log with the actual error lines)"""
        mods = self._installed(inst).get("mods") or []
        ok, l = self.start(inst)
        rep = {"ok": False, "start": ok, ("start_log" if ok else "reason"): l.split("\n")[0][:400]}
        if ok:
            time.sleep(10)
            rep.update(alive_after_10s=self._alive(inst), screen=self._br(inst, {"cmd": "ping"}).get("screen"))
            if not rep["alive_after_10s"]: rep["reason"] = "the game exited within 10 s of loading the save"
        lg = self.seat.exec(f"cat {self.smapi_log(inst)} 2>/dev/null").stdout
        rep["mods"] = mod_load_status(lg, [read_manifest_remote(self, inst, m) for m in mods])
        bad = {m: s for m, s in rep["mods"].items() if s != "loaded"}
        cpe = cp_errors(lg)                    # Content Patcher's own ERROR / WARN lines: a pack it could not read, patches ignored / failing
        if cpe: rep["content_patcher"] = cpe
        if ok and rep.get("alive_after_10s") and bad: rep["reason"] = f"mods not loaded: {bad}"
        elif ok and rep.get("alive_after_10s") and cpe: rep["reason"] = f"Content Patcher reported {len(cpe)} error / warning line(s) for the content pack(s)"
        rep["ok"] = bool(ok and rep.get("alive_after_10s") and not bad and not cpe)
        E = relevant_errors(lg, self.seat.exec(f"tail -c 20000 {self.idir(inst)}/session/stdout.log 2>/dev/null").stdout)
        if cpe:                                # CP's lines first; the same lines again from the generic ERROR scan left out
            E = (cpe + [x for x in E if not any(x.strip() in c or c.strip() in x for c in cpe)])[:gbase.ERR_N]
        self.stop(inst)
        if rep["ok"]: return True, gbase.gate_log([], None, json.dumps(rep, indent=1))
        return False, gbase.gate_log(E, rep["reason"], json.dumps(rep, indent=1))      # REASON / ERRORS first (gate.log, `game build`)

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.smapi_log(inst)} 2>/dev/null").stdout

    def log_errors(self, inst):
        r = self.seat.exec(f"python3 {self.root}/bin/sdbr.py errors {self.smapi_log(inst)}")
        try: return json.loads(r.stdout)
        except ValueError: return {}

    def state(self, inst):
        """STATE_SPEC snapshot, live from the running game through the bridge (GabBridge, on the game thread):
        {"running", "engine", "mod", "screen_text"}.
        engine (fixed keys, null when not in game):
          screen     title | loading | in game | menu | dialogue | event | day end | minigame;  menu  active menu type
          location, location_name, tile {x, y}, facing, health, max_health, stamina, max_stamina, money,
          date {year, season, day, weekday}, time (600..2600), weather, inventory {item name: count}, held, skills {farming, ...},
          riding, event (id), paused, player, farm, quests [titles], friendship {villager: points}, npcs_here {name: {x, y}}
          (nearest 15 in this location), flags_new [mail / event / world flags set since the save loaded], mailbox [letters]
        mod (only what is not vanilla; vanilla = base/vanilla.json, dumped from the unmodded game):
          smapi_mods  mods SMAPI loaded besides its own, the harness and Content Patcher;  content_packs  content packs SMAPI
          loaded ("Name version (id, for <framework id>)");  npcs  {name: {location, x, y, display, kind}} characters
          that are not vanilla villagers / monsters (nearest 20);  objects  {"name@x,y": {id, mod_data}} non-vanilla objects /
          furniture here or ones carrying mod data;  items  same for the inventory;  locations  non-vanilla locations;
          mod_data  the engine's mod storage: player / location modData, save CustomData (SMAPI WriteSaveData);  flags  set
          flags no vanilla data or code mentions;  stats  non-vanilla stat counters;  quests  non-vanilla quests {id: title};
          event  a running non-vanilla event;  data  {asset: {added, changed, removed}} game data entries a mod added or edited
          (all Data/* assets, the current location's events, the dialogue of every villager; Content Patcher edits show here)
        screen_text: "hud: ..." HUD messages, "dialog: speaker: text", "choice: ..." answers, "letter: ...", "shop: <id>",
          "chat: ..." (last 8), "drawn: ..." other text drawn on the last frame (any mod's own HUD text included), max 30"""
        d = self.idir(inst)
        q = self._br(inst, {"cmd": "state"}, timeout=2.5)
        if isinstance(q, dict) and isinstance(q.get("engine"), dict):
            return {"running": True, "engine": q["engine"], "mod": q.get("mod") or {}, "screen_text": list(q.get("screen_text") or [])}
        run = self._alive(inst)
        E = dict.fromkeys(P.ENGINE_KEYS); E["screen"] = "starting" if run else None
        out = {"running": run, "engine": E, "mod": {}, "screen_text": []}
        if run and q.get("error"): out["error"] = str(q["error"])[:300]
        return out

    # ---- dev tools beyond the common ones
    def dev_command(self, inst, k, v, mod, wd):
        if k != "console": return None
        if not inst.startswith("dev-"): return {"error": "the console is on only in the dev instance"}
        if not self._alive(inst): return {"error": "the game is not running: game start"}
        n = self.seat.exec(f"wc -c < {self.smapi_log(inst)}").stdout.strip() or "0"
        self.seat.exec(f"printf '%s\\n' {shlex.quote(' '.join(v))} >> {self.idir(inst)}/session/console.in")
        time.sleep(1.5)
        return {"ok": True, "sent": " ".join(v), "output": self.seat.exec(f"tail -c +{int(n) + 1} {self.smapi_log(inst)} | tail -30").stdout}

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """all probes in ONE fresh probe instance (probe display), in the pool's order; vanilla is the oracle"""
        inst = f"probe-{re.sub(r'[^A-Za-z0-9_.-]', '_', label)[:40]}"
        self.provision(inst)
        self.install_mod(inst, mod_dir) if mod_dir else None
        pr = P.SDProbes(self, inst)
        try: res = pr.run_all(names)
        finally: self.stop(inst)
        res["_run_dir"] = self.idir(inst)
        self.seat.exec(f"cd {self.idir(inst)} && rm -rf game HOME/.config/StardewValley/Saves")       # keep logs, bridge io
        return res

    def compare(self, spec, base, got): return P.compare_item(spec, base, got)

    def version(self):
        i = self._info() if self.seat.exec(f"test -f {self.root}/base/info.json && echo y").stdout.strip() == "y" else {}
        return {"game": "stardew_valley", "build": f"{i.get('game', '1.6.15')} (SMAPI {i.get('smapi', '4.5.2')})",
                "mod_loader": f"SMAPI {i.get('smapi', '4.5.2')}", "content_patcher": i.get("cp"), "content_patcher_dll_sha256": i.get("cp_dll_sha256"),
                "bridge": i.get("bridge"), "image": self.seat.sandbox,
                "game_sha256": i.get("game_sha")}


def docker_mounts(game_dir, sources_dir=""):
    """(host, container, mode) bind mounts of the user's game folder (+ decompiled sources) for the docker backend. Checks what
    the host user can see: the folder exists and holds the game and SMAPI (a folder this user cannot read, e.g. another
    person's private copy, is passed on unchecked: the docker daemon mounts it; install() checks it inside)"""
    hint = ("set the folder of your own Stardew Valley 1.6.15 Linux copy with SMAPI 4.5.2 installed: "
            "export GAB_STARDEW_GAME_DIR=/path/to/game (or gab setup --set stardew_game_dir PATH); docker/stardew_valley/get-game.sh downloads it")
    if not game_dir: raise RuntimeError(f"stardew_valley on host docker: no game folder configured: {hint}")
    g = Path(game_dir).expanduser().absolute()
    try:
        if not g.is_dir(): raise RuntimeError(f"stardew_valley: game folder {g} does not exist: {hint}")
        if not (g / "Stardew Valley.dll").is_file(): raise RuntimeError(f"stardew_valley: no 'Stardew Valley.dll' in {g}: {hint}")
        if not (g / "StardewModdingAPI.dll").is_file():
            raise RuntimeError(f"stardew_valley: SMAPI is not installed in {g}: docker/stardew_valley/install-smapi.sh {g}")
    except PermissionError: pass
    out = [(str(g), LAB_GAME, "ro")]
    s = Path(sources_dir).expanduser().absolute() if sources_dir else g.parent / "sd-sources"
    try:
        if s.is_dir() and any(s.iterdir()): out.append((str(s), LAB_SOURCES, "ro"))
    except PermissionError: pass
    return out


# ---------------------------------------------------------------------------------------------------- log helpers (pure)
SMAPI_ERR = r"^ERROR "                     # SMAPI ERROR lines (skipped / crashed mods too) + the 3 lines after (exception, frames)
SMAPI_NOISE = (r"Steam isn't loaded|initializing XACT|^ERROR [^:]*:\s*-{5,}\s*$"       # vanilla here: no Steam client, sound off
               r"|^(?:TRACE|DEBUG|INFO|WARN|ALERT) ")                                   # other log levels after an ERROR line
SMAPI_LINE = re.compile(r"^\[\d\d:\d\d:\d\d (ERROR|WARN|ALERT|INFO|DEBUG|TRACE)\s*([^\]]*)\] ", re.M)
STDOUT_ERR = r"Unhandled exception|Exception:|[Ff]atal|Segmentation|core dumped|Aborted"
BUILD_ERR = r"\berror\b|: error |MSB\d{4}|NU\d{4}"


def relevant_errors(smapi_log, stdout="", n=gbase.ERR_N):
    """the first relevant error lines of a SMAPI log (ERROR / skipped / failed-on-entry lines + exception frames) and of the
    game's stdout, deduplicated (gab.game.base.error_lines); vanilla noise (Steam not loaded, no audio device) left out"""
    lg = SMAPI_LINE.sub(lambda m: f"{m.group(1)} {m.group(2).strip()}: ", smapi_log or "")     # keep the source (SMAPI / mod name)
    lg = re.sub(r"/\S*/(?:builds/[0-9a-f]+/src|game/Mods)/", "mod/", lg)
    E = gbase.error_lines(lg, SMAPI_ERR, SMAPI_NOISE, n=n, after=3)
    return E + ["stdout: " + x for x in gbase.error_lines(stdout, STDOUT_ERR, r"Steam", n=max(0, n - len(E)))]


def build_errors(log, n=gbase.ERR_N):
    """compiler / MSBuild / NuGet error lines of a dotnet build log (deduplicated, first n; sandbox build paths shortened to
    mod/<Name>/..., the trailing [project] dropped)"""
    log = re.sub(r"\s+\[[^\]\n]*\.csproj\]$", "", re.sub(r"/\S*/builds/[0-9a-f]+/src/", "mod/", log or ""), flags=re.M)
    return gbase.error_lines(log, BUILD_ERR, r"CS9057|warning", n=n) or (log.strip().splitlines()[-15:] if log.strip() else [])


CP_LINE = re.compile(r"^\[\d\d:\d\d:\d\d (ERROR|WARN)\s+Content Patcher\] (.*)$")


def cp_errors(smapi_log, n=gbase.ERR_N):
    """Content Patcher's own ERROR / WARN lines (+ up to 2 continuation lines, e.g. the exception of 'Error loading content pack'),
    as `ERROR Content Patcher: ...`; deduplicated, sandbox paths shortened"""
    out, lines = [], (smapi_log or "").splitlines()
    for i, l in enumerate(lines):
        m = CP_LINE.match(l)
        if not m: continue
        x = re.sub(r"/\S*/(?:builds/[0-9a-f]+/(?:src|out)|game/Mods)/", "mod/", f"{m.group(1)} Content Patcher: {m.group(2).strip()}")[:400]
        if x not in out: out.append(x)
        for c in lines[i + 1:i + 3]:
            if c.startswith("[") or not c.strip(): break
            c = "  " + re.sub(r"/\S*/(?:builds/[0-9a-f]+/(?:src|out)|game/Mods)/", "mod/", c.strip())[:300]
            if c not in out: out.append(c)
        if len(out) >= n: break
    return out[:n]


def _all_ids(mod_folders):
    ids = set()
    for m in mod_folders:
        mf = read_manifest(Path(m) / "manifest.json")
        if "_error" not in mf: ids.add(str(mf.get("UniqueID") or mf.get("UniqueId") or ""))
    return ids


def read_manifest_remote(ad, inst, folder):
    r = ad.seat.exec(f"cat {ad.idir(inst)}/game/Mods/{shlex.quote(folder)}/manifest.json 2>/dev/null").stdout
    try: mf = json.loads(r.lstrip("\ufeff"))
    except ValueError: mf = {}
    return {"folder": folder, "id": mf.get("UniqueID") or mf.get("UniqueId") or folder, "name": mf.get("Name") or folder}


def mod_load_status(smapi_log, mods):
    """{folder: loaded | skipped: reason | failed: reason | not listed} from the SMAPI log (TRACE '(from Mods/<folder>/...)' lines,
    the 'Skipped mods' section, ERROR lines naming the mod)"""
    res = {}
    skipped = re.split(r"\n\n|Loaded \d+ mods", smapi_log.split("Skipped mods", 1)[1])[0][:6000] if "Skipped mods" in smapi_log else ""
    blocks = re.findall(r"Loaded \d+ (?:mods|content packs):\n(.*?)\n\n", smapi_log, re.S)
    loaded_block = re.search(r"(.*)", "\n".join(blocks), re.S) if blocks else None
    for m in mods:
        f, name = m["folder"], m["name"]
        sk = [l for l in skipped.splitlines() if name in l or f in l]
        if sk: res[f] = "skipped: " + re.sub(r"^\[[^\]]*\]\s*", "", sk[0]).strip()[:200]; continue
        fe = re.search(rf"ERROR {re.escape(name)}\] Mod (?:crashed|failed) on entry[^\n]*|\] {re.escape(name)} (?:crashed|failed) on entry[^\n]*", smapi_log)
        if fe: res[f] = "failed: crashed in Entry (" + fe.group(0)[:160] + ")"; continue
        if loaded_block and re.search(rf"^\[[^\]]*\]\s+{re.escape(name)} ", loaded_block.group(1), re.M): res[f] = "loaded"
        elif f"Mods/{f}/" in smapi_log or f"(from Mods/{f}" in smapi_log: res[f] = "loaded" if "Launching mods" in smapi_log else "not listed"
        else: res[f] = "not listed"
    return res

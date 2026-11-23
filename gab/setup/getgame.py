"""`gab get-game GAME[,GAME...] | all | open | commercial`: set a game up on the docker host in ONE command (an
outside evaluator can configure a game with a single command line).

Per game, in order: (1) the image: pulled (open-source games, pinned digests) or built locally (commercial games: runtime only,
docker/build.sh); (2) the game files of a commercial game, from the owner's own account: Minecraft from the public Mojang /
Fabric endpoints (no login), Stardew Valley with SteamCMD (the owner types the Steam password + Steam Guard code into SteamCMD's
own prompt), the Steam games (Terraria, Valheim, Cities: Skylines) through the machine's Steam hub container (gab/setup/steamsetup.py:
image, the hub's user-namespace permission, the login in the terminal, the app ids, all skipped when done); (3) the config key that points gab at
the files (gab.local.json); (4) a start check (`gab game check GAME`: provision, start, screenshot, state).
Re-running is safe: a step whose result is already in place is skipped. Never stores a password; commercial files never go
into an image (they are mounted read only into each trial container).

    gab get-game open                     the ten open-source games (pull only, about 25 GB)
    gab get-game minecraft,stardew_valley commercial games without Steam
    gab get-game all --no-check
"""
import hashlib, json, os, subprocess, sys
from pathlib import Path

from .. import config
from ..util import log

OPEN = ["voxelibre", "xonotic", "godot-tps", "flare", "tuxemon", "tof2", "colobot", "celeste64", "supertuxkart"]
COMMERCIAL = ["minecraft", "stardew_valley", "terraria", "valheim", "cities_skylines"]
STEAM = {"terraria", "valheim", "cities_skylines"}
STARDEW_DLL_SHA = "f3e97f01d3fd2b1e6094fc8d2b59950aa6cb9d6cd1bf1b39d72d58edda8aad12"     # Stardew Valley.dll 1.6.15


def commercial_dir():
    d = Path(config.get("commercial_dir")).expanduser(); d.mkdir(parents=True, exist_ok=True); os.chmod(d, 0o700); return d


def set_local(key, value):
    """write one config key into gab.local.json (what `gab setup --set` does)"""
    p = config.REPO / "gab.local.json"
    cur = json.loads(p.read_text()) if p.exists() else {}
    if cur.get(key) != value:
        cur[key] = value; p.write_text(json.dumps(cur, indent=1) + "\n"); log(f"config {key} = {value} (gab.local.json)")
    os.environ.pop("GAB_" + key.upper(), None)                 # the new value wins in this process too


def image(game):
    from ..host.docker import Backend, image_ref, manifest
    b, ref = Backend(), image_ref(game)
    if b.has_image(ref): log(f"{game}: image {ref} present"); return
    spec = (manifest().get("games") or {}).get(game) or {}
    if spec.get("digest"):                                        # published: pull the pinned digest
        log(f"{game}: pulling {ref}"); b.pull(ref); return
    log(f"{game}: building the image locally (docker/build.sh {game}; runtime only, no game files)")
    subprocess.run(["bash", str(config.REPO / "docker" / "build.sh"), game], check=True,
                   env={**os.environ, "TAG": spec.get("tag") or "local"})


def files_minecraft():
    d = Path(config.get("minecraft_dir") or commercial_dir() / "minecraft")
    log(f"minecraft: game files from the public Mojang / Fabric endpoints into {d} (no login; skips files already correct)")
    subprocess.run([sys.executable, "-I", str(config.REPO / "docker" / "minecraft" / "fetch_game.py"), str(d)], check=True)
    set_local("minecraft_dir", str(d))


def files_stardew():
    d = Path(config.get("stardew_game_dir") or commercial_dir() / "stardew_valley" / "game")
    dll = d / "Stardew Valley.dll"
    smapi = (d / "StardewModdingAPI.dll").exists() or (d / "StardewModdingAPI").exists()
    if dll.exists() and hashlib.sha256(dll.read_bytes()).hexdigest() == STARDEW_DLL_SHA and smapi:
        log(f"stardew_valley: 1.6.15 + SMAPI already in {d}")
    else:
        if not sys.stdin.isatty(): sys.exit("stardew_valley: the download needs your Steam login: run `gab get-game stardew_valley` "
                                            "in an interactive terminal (SteamCMD asks for the password and the Steam Guard code)")
        log("stardew_valley: downloading YOUR copy with SteamCMD (it asks for your Steam password + Steam Guard code itself)")
        subprocess.run(["bash", str(config.REPO / "docker" / "stardew_valley" / "get-game.sh")], check=True,
                       env={**os.environ, "GAB_COMMERCIAL_DIR": str(d.parent.parent)})
    set_local("stardew_game_dir", str(d))


FILES = {"minecraft": files_minecraft, "stardew_valley": files_stardew}


def check(game):
    """provision a fresh instance, start it, screenshot, state -> raises when the game does not start"""
    from ..game import base as gbase
    from .selfcheck import _seat
    log(f"{game}: start check (provision, start, screenshot, state)")
    seat = _seat(game, "get-game")
    try:
        ad = gbase.get(game, seat); ad.provision("get-game"); ad.attach("get-game", None)
        ok, lg = ad.start("get-game")
        shot = ad.screenshot("get-game", config.path("runs_dir") / "_selfcheck" / game / "get-game.png") if ok else None
        ad.stop("get-game")
    finally: seat.release()
    if not ok: raise RuntimeError(f"{game}: the game did not start: {str(lg)[-2000:]}")
    log(f"{game}: starts ok (screenshot {shot})")


def get_game(games, do_check=True):
    if config.get("host") != "docker": set_local("host", "docker")
    sel = {"all": OPEN + COMMERCIAL, "open": OPEN, "commercial": COMMERCIAL}
    want = [g for x in games.split(",") for g in sel.get(x, [x])]
    bad = [g for g in want if g not in OPEN + COMMERCIAL]
    if bad: sys.exit(f"unknown game(s): {', '.join(bad)} (one of {', '.join(OPEN + COMMERCIAL)}, or all / open / commercial)")
    done, skipped = [], []
    failed = []
    for g in want:
        try:
            if g in STEAM:
                from .steamsetup import setup_game, APPS
                if g not in APPS: skipped.append(f"{g}: no Steam setup for it yet"); continue
                setup_game(g, do_check=do_check); done.append(g); continue
            image(g)
            if g in FILES: FILES[g]()
            if do_check: check(g)
            done.append(g)
        except (Exception, SystemExit) as e: failed.append(f"{g}: {str(e)[:2000]}"); log(f"{g}: FAILED: {str(e)[:2000]}")
    print(f"\nready: {', '.join(done) or '-'}" + "".join(f"\nnot yet: {s}" for s in skipped) + "".join(f"\nFAILED: {s}" for s in failed))
    if failed: sys.exit(1)
    print("next: gab selfcheck <game>   (full A/A + negative-mod check, 20-90 min)")

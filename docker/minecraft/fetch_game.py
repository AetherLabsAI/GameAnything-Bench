#!/usr/bin/env python3
"""Lay out the Minecraft game files the `minecraft` adapter needs, from the PUBLIC download endpoints (no login, no account):

  python3 -I docker/minecraft/fetch_game.py TARGET          e.g. TARGET = /home/<you>/gab-data/commercial/minecraft

TARGET becomes a .minecraft-style folder (versions/26.3/, versions/fabric-26.3-0.19.5/, libraries/, assets/), the layout the
adapter reads (GAB_MINECRAFT_DIR / config minecraft_dir; mounted read only at /mc/main in the docker image). Sources:
  - Mojang piston-meta version manifest -> 26.3.json -> client jar, libraries, asset index + objects (each sha1-checked)
  - Fabric meta (loader 0.19.5 profile for 26.3) -> its libraries from maven.fabricmc.net (each sha256 / sha1-checked)
The client jar is also checked against the adapter's pinned sha256. Only for a person who owns Minecraft: Java Edition (the
Minecraft EULA; these files are never put in an image or redistributed). Re-running skips files that are already correct.
stdlib only."""
import concurrent.futures as cf, hashlib, json, os, sys, urllib.request
from pathlib import Path

MC = "26.3"
LOADER = "0.19.5"
VERSION_ID = f"fabric-{MC}-{LOADER}"
GAME_JAR_SHA256 = "4508d006323f24fa02876310c192d739af56516eb259000ac50f0909a68c9a2d"     # = gab/game/minecraft/adapter.py
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC = f"https://meta.fabricmc.net/v2/versions/loader/{MC}/{LOADER}/profile/json"
ASSETS = "https://resources.download.minecraft.net"


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "gab-fetch/1"}), timeout=120) as r: return r.read()
        except Exception:
            if i == tries - 1: raise


def digest(data, algo): return hashlib.new(algo, data).hexdigest()


def fetch(url, dst, algo=None, want=None):
    """url -> dst (atomic), checked by algo/want when given; skipped when dst already matches"""
    dst = Path(dst)
    if dst.is_file() and (not want or digest(dst.read_bytes(), algo) == want): return False
    data = get(url)
    if want and digest(data, algo) != want: raise RuntimeError(f"{url}: {algo} mismatch")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part"); tmp.write_bytes(data); os.replace(tmp, dst)
    return True


def maven_path(name):
    g, a, v, *c = name.split(":")
    return f"{g.replace('.', '/')}/{a}/{v}/{a}-{v}{'-' + c[0] if c else ''}.jar"


def main(target):
    T = Path(target); T.mkdir(parents=True, exist_ok=True)
    man = json.loads(get(MANIFEST))
    ent = next((v for v in man["versions"] if v["id"] == MC), None)
    if not ent: raise SystemExit(f"version {MC} is not in the Mojang manifest")
    vjs = get(ent["url"])
    if digest(vjs, "sha1") != ent["sha1"]: raise SystemExit(f"{MC}.json sha1 mismatch")
    v = json.loads(vjs)
    vd = T / "versions" / MC; vd.mkdir(parents=True, exist_ok=True); (vd / f"{MC}.json").write_bytes(vjs)
    c = v["downloads"]["client"]
    fetch(c["url"], vd / f"{MC}.jar", "sha1", c["sha1"])
    got = digest((vd / f"{MC}.jar").read_bytes(), "sha256")
    if got != GAME_JAR_SHA256: raise SystemExit(f"client jar sha256 {got} != the adapter's pin {GAME_JAR_SHA256}")
    jobs = []                                                     # (url, dst, algo, want)
    for lib in v["libraries"]:
        a = (lib.get("downloads") or {}).get("artifact")
        if a: jobs.append((a["url"], T / "libraries" / a["path"], "sha1", a["sha1"]))
    fjs = get(FABRIC); f = json.loads(fjs)
    f["id"] = VERSION_ID                                          # the profile name the Fabric installer gives it
    fd = T / "versions" / VERSION_ID; fd.mkdir(parents=True, exist_ok=True)
    (fd / f"{VERSION_ID}.json").write_text(json.dumps(f, indent=1))
    for lib in f["libraries"]:
        p = maven_path(lib["name"]); base = (lib.get("url") or "https://maven.fabricmc.net/").rstrip("/")
        algo, want = ("sha256", lib["sha256"]) if lib.get("sha256") else ("sha1", lib["sha1"]) if lib.get("sha1") else (None, None)
        jobs.append((f"{base}/{p}", T / "libraries" / p, algo, want))
    ai = v["assetIndex"]; ij = get(ai["url"])
    if digest(ij, "sha1") != ai["sha1"]: raise SystemExit("asset index sha1 mismatch")
    (T / "assets" / "indexes").mkdir(parents=True, exist_ok=True); (T / "assets" / "indexes" / f"{ai['id']}.json").write_bytes(ij)
    for o in {o["hash"] for o in json.loads(ij)["objects"].values()}:
        jobs.append((f"{ASSETS}/{o[:2]}/{o}", T / "assets" / "objects" / o[:2] / o, "sha1", o))
    with cf.ThreadPoolExecutor(16) as ex:
        res = list(ex.map(lambda j: fetch(*j), jobs))
    print(json.dumps({"ok": True, "target": str(T), "version": VERSION_ID, "files": len(jobs) + 1, "downloaded": sum(res)}))


if __name__ == "__main__":
    if len(sys.argv) != 2: raise SystemExit(__doc__)
    main(sys.argv[1])

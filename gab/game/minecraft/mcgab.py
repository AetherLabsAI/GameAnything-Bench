#!/usr/bin/env python3
"""gab Minecraft tool INSIDE the game sandbox (python3 stdlib only; copied to <root>/bin/mcgab.py by the adapter's install).

  mcgab.py classpath MAIN VERSION                       the game's classpath (from MAIN/versions/VERSION/VERSION.json)
  mcgab.py build SRC OUT_JAR MAIN VERSION API_JAR       javac a Fabric mod source project into a jar (no Gradle: 26.x is
                                                        unobfuscated) -> JSON {ok, jar, modid, log}
  mcgab.py launch INST MAIN VERSION DISPLAY PORT NAME   start the game detached on DISPLAY (pid in INST/gab/game.pid)
  mcgab.py call PORT 'JSON'                             one bridge request -> JSON answer
  mcgab.py wait INST PORT SECONDS                       until the bridge reports in_world (or the game died / failed)
  mcgab.py state INST PORT                              STATE_SPEC snapshot (running: false when the game is gone)
  mcgab.py stop INST [DISPLAY]                          kill the game's process group (exact pid from the pid file) [+ its Xvfb]
  mcgab.py check MOD MAIN VERSION API_JAR CACHE         the deliverable's layout + builds of its source projects -> JSON
  mcgab.py install MOD INST MAIN VERSION API_JAR CACHE [--probe]   install it into the instance (--probe: default world)
  mcgab.py errors INST                                  error lines of the game log (deduplicated, offline noise left out)
  mcgab.py probes INST PORT DISPLAY NAMES OUT_JSON      regression probes (gab/game/minecraft/probes.py docstring)

MAIN = the player's own .minecraft (versions/, libraries/, assets/, a Java runtime); never written to.
Instance INST = the game dir (mods/, config/, options.txt, saves/, logs/) + INST/gab (start.json, pid, launcher log).
"""
import hashlib, json, os, re, shutil, signal, socket, subprocess, sys, time, zipfile
from pathlib import Path

GAME_JAVA_MIN = 25


# ---------------------------------------------------------------- version files, classpath, java
def load_version(main, vid):
    v = json.loads(Path(f"{main}/versions/{vid}/{vid}.json").read_text())
    if not v.get("inheritsFrom"):
        v["jar_version"] = v["id"]; return v
    p = load_version(main, v["inheritsFrom"])
    a, b = p.get("arguments") or {}, v.get("arguments") or {}
    return {**p, "id": v["id"], "mainClass": v.get("mainClass") or p.get("mainClass"),
            "libraries": (v.get("libraries") or []) + (p.get("libraries") or []),
            "arguments": {"jvm": (a.get("jvm") or []) + (b.get("jvm") or []), "game": (a.get("game") or []) + (b.get("game") or [])},
            "jar_version": p["jar_version"]}


def allowed(rules, features=None):
    if not rules: return True
    ok = False
    for r in rules:
        m = True; o = r.get("os") or {}
        if o.get("name") and o["name"] != "linux": m = False
        if o.get("arch") and o["arch"] not in ("x86_64", "amd64"): m = False
        for k, val in (r.get("features") or {}).items():
            if bool((features or {}).get(k)) != bool(val): m = False
        if m: ok = r.get("action") == "allow"
    return ok


def lib_path(lib):
    a = (lib.get("downloads") or {}).get("artifact") or {}
    if a.get("path"): return a["path"]
    g, n, ver, *cl = lib["name"].split(":")
    return f"{g.replace('.', '/')}/{n}/{ver}/{n}-{ver}{'-' + cl[0] if cl else ''}.jar"


def classpath(main, vid):
    v = load_version(main, vid); seen, out = set(), []
    for lib in v["libraries"]:
        if not allowed(lib.get("rules")): continue
        parts = lib["name"].split(":"); key = (parts[0], parts[1], parts[3] if len(parts) > 3 else "")
        if key in seen: continue                       # the child profile (Fabric) wins over the parent's copy
        seen.add(key); out.append(f"{main}/libraries/{lib_path(lib)}")
    out.append(f"{main}/versions/{v['jar_version']}/{v['jar_version']}.jar")
    return v, out


def java_major(exe):
    try: r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30)
    except Exception: return 0
    m = re.search(r'version "(\d+)', r.stderr + r.stdout)
    return int(m.group(1)) if m else 0


def find_java(main, tool="java"):
    """the Java of the player's launcher (MAIN/jvm/*, MAIN/runtime/*/*/*), else /opt/jdk, else PATH; major >= 25"""
    cands = sorted(Path(main).glob(f"jvm/*/bin/{tool}")) + sorted(Path(main).glob(f"runtime/*/*/*/bin/{tool}"))
    cands += [Path(f"/opt/jdk/bin/{tool}")] + ([Path(shutil.which(tool))] if shutil.which(tool) else [])
    for c in cands:
        if c.is_file() and java_major(str(c.with_name("java")) if tool != "java" else str(c)) >= GAME_JAVA_MIN: return str(c)
    return None


# ---------------------------------------------------------------- build (javac)
def build(src, out_jar, main, vid, api_jar):
    """a Fabric mod project: fabric.mod.json at SRC/ or SRC/src/main/resources/; Java under SRC/src/main/java (or SRC/src);
    resources = SRC/src/main/resources (else SRC/resources). Compiled against the game, Fabric loader + libraries and Fabric API."""
    src = Path(src); out_jar = Path(out_jar); log = []
    ress = [d for d in (src / "src" / "main" / "resources", src / "src" / "client" / "resources", src / "resources") if d.is_dir()]
    fj = fabric_json(src)
    if not fj: return {"ok": False, "log": f"{src.name}: no fabric.mod.json (at the top or in src/main/resources/)"}
    try: meta = json.loads(fj.read_text(encoding="utf-8-sig")); modid = meta["id"]
    except Exception as e: return {"ok": False, "log": f"{src.name}: fabric.mod.json unreadable or without id: {e}"}
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", str(modid)): return {"ok": False, "log": f"{src.name}: mod id {modid!r} must match [a-z][a-z0-9_-]{{1,63}}"}
    jroots = [d for d in (src / "src" / "main" / "java", src / "src" / "client" / "java") if d.is_dir()] or \
             [d for d in (src / "src", src / "java") if d.is_dir()][:1]          # Loom's split main / client source sets, or one tree
    javas = sorted({str(p) for r in jroots for p in r.rglob("*.java")})
    w = out_jar.parent / (out_jar.stem + ".work"); shutil.rmtree(w, ignore_errors=True); (w / "classes").mkdir(parents=True)
    if javas:
        javac = find_java(main, "javac")
        if not javac: return {"ok": False, "log": "no javac (JDK >= 25) in the sandbox"}
        v, cp = classpath(main, vid)
        cp.append(api_jar)
        with zipfile.ZipFile(api_jar) as z:                # Fabric API is a jar of jars: javac needs the inner ones
            for n in z.namelist():
                if n.startswith("META-INF/jars/") and n.endswith(".jar"):
                    d = w / "deps" / Path(n).name; d.parent.mkdir(parents=True, exist_ok=True); d.write_bytes(z.read(n)); cp.append(str(d))
        cp += sorted(str(p) for p in (src / "libs").glob("*.jar")) if (src / "libs").is_dir() else []
        (w / "sources.txt").write_text("".join(f'"{s}"\n' for s in javas))
        r = subprocess.run([javac, "--release", str(GAME_JAVA_MIN), "-nowarn", "-proc:none", "-encoding", "UTF-8", "-cp", ":".join(cp),
                            "-d", str(w / "classes"), f"@{w / 'sources.txt'}"], capture_output=True, text=True, timeout=900)
        log.append(f"javac --release {GAME_JAVA_MIN} ({len(javas)} files) against {vid}: rc {r.returncode}")
        out = (r.stdout + r.stderr).strip()
        if out: log.append(out[-6000:])
        if r.returncode: return {"ok": False, "modid": modid, "log": "\n".join(log)}
    else: log.append("no .java files: a resources-only mod")
    tmp = out_jar.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n\n")
        for f in sorted((w / "classes").rglob("*")):
            if f.is_file(): z.write(f, f.relative_to(w / "classes").as_posix())
        done = {"fabric.mod.json", "META-INF/MANIFEST.MF"}
        for res in ress:
            for f in sorted(res.rglob("*")):
                rel = f.relative_to(res).as_posix()
                if f.is_file() and rel not in done: z.write(f, rel); done.add(rel)
        z.writestr("fabric.mod.json", fj.read_text(encoding="utf-8-sig").replace("${version}", "1.0.0"))   # Gradle's placeholder
    os.replace(tmp, out_jar); shutil.rmtree(w, ignore_errors=True)
    return {"ok": True, "jar": str(out_jar), "modid": modid, "log": "\n".join(log)}


# ---------------------------------------------------------------- the deliverable mod/ (devkit/README.md)
START_KEYS = {"world": str, "seed": int, "game_mode": str, "difficulty": str, "world_type": str, "cheats": bool, "hardcore": bool,
              "structures": bool, "name": str}
CHOICES = {"game_mode": ("survival", "creative", "adventure", "spectator"), "difficulty": ("peaceful", "easy", "normal", "hard"),
           "world_type": ("normal", "flat", "large_biomes", "amplified", "single_biome_surface")}
DEFAULT_START = {"mode": "fresh", "world": "gab_world", "name": "New World", "seed": 2024, "game_mode": "survival", "difficulty": "normal",
                 "world_type": "normal", "cheats": False}
BASE_IDS = {"fabric-api", "gabbridge", "minecraft", "fabricloader", "java"}
SKIP = {"build", ".gradle", "out", ".idea", "__pycache__"}


def fabric_json(p):
    """a Fabric project dir -> its fabric.mod.json path (top or src/main/resources/), else None"""
    for f in (p / "fabric.mod.json", p / "src" / "main" / "resources" / "fabric.mod.json", p / "resources" / "fabric.mod.json"):
        if f.is_file(): return f
    return None


def zip_names(f):
    try:
        with zipfile.ZipFile(f) as z: return z.namelist()
    except Exception: return None


def pack_kind(p):
    """dir or zip with pack.mcmeta -> {"data", "assets"} subset (datapack / resource pack), else None"""
    if p.is_dir():
        if not (p / "pack.mcmeta").is_file(): return None
        return {k for k in ("data", "assets") if (p / k).is_dir()}
    n = zip_names(p) if p.suffix == ".zip" else None
    if not n or "pack.mcmeta" not in n: return None
    return {k for k in ("data", "assets") if any(x.startswith(k + "/") for x in n)}


def read_start(f):
    """start.yaml (a flat `key: value` file; no YAML library in the sandbox) -> (dict, problems)"""
    st, P = {}, []
    for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line: continue
        if ":" not in line: P.append(f"start.yaml line {i}: expected `key: value`"); continue
        k, v = (x.strip() for x in line.split(":", 1)); v = v.strip("'\"")
        if k not in START_KEYS: P.append(f"start.yaml: unknown key {k} (have: {', '.join(sorted(START_KEYS))})"); continue
        t = START_KEYS[k]
        if t is bool:
            if v.lower() not in ("true", "false", "yes", "no"): P.append(f"start.yaml: {k} must be true or false"); continue
            st[k] = v.lower() in ("true", "yes")
        elif t is int:
            try: st[k] = int(v)
            except ValueError: P.append(f"start.yaml: {k} must be an integer"); continue
        else: st[k] = v
        if k in CHOICES and st[k] not in CHOICES[k]: P.append(f"start.yaml: {k} must be one of {', '.join(CHOICES[k])}")
    return st, P


def layout(mod):
    """mod/ -> {projects, jars, datapacks, resourcepacks, saves, config, start, problems, warnings}. Accepted (README):
    Fabric source projects (fabric.mod.json at the top or in src/main/resources), *.jar (built Fabric mods), datapacks and
    resource packs (dirs / zips with pack.mcmeta; also under datapacks/ and resourcepacks/), saves/<world>/ (level.dat),
    config/, start.yaml. mod/ itself may be one project or one pack."""
    m = Path(mod); L = {"projects": [], "jars": [], "datapacks": [], "resourcepacks": [], "saves": [], "config": [], "start": {}, "problems": [], "warnings": []}
    if not m.is_dir(): L["problems"].append(f"{m} is not a directory"); return L
    def add_pack(p, where):
        k = pack_kind(p)
        if k is None: return False
        if not k: L["warnings"].append(f"{where}{p.name}: pack.mcmeta but neither data/ nor assets/")
        if "data" in k: L["datapacks"].append(p)
        if "assets" in k: L["resourcepacks"].append(p)
        return True
    def add_jar(f, where):
        n = zip_names(f)
        if n is None: L["problems"].append(f"{where}{f.name}: not a jar (zip) file"); return
        if "fabric.mod.json" not in n: L["problems"].append(f"{where}{f.name}: no fabric.mod.json inside (not a Fabric mod; Forge / NeoForge mods do not load)"); return
        if f.name.endswith("-sources.jar"): L["warnings"].append(f"{where}{f.name}: a sources jar, skipped"); return
        L["jars"].append(f)
    if fabric_json(m): L["projects"].append(m)
    elif add_pack(m, ""): pass
    for p in sorted(m.iterdir()):
        if p.name.startswith(".") or p.name in SKIP: continue
        if p.name == "start.yaml":
            L["start"], P = read_start(p); L["problems"] += P; continue
        if m in L["projects"] and p.name in ("src", "resources", "java", "libs", "fabric.mod.json", "README.md", "build.gradle",
                                             "settings.gradle", "gradle.properties", "gradlew", "gradle"): continue
        if p.is_file():
            if p.suffix == ".jar": add_jar(p, "")
            elif p.suffix == ".zip":
                if not add_pack(p, ""): L["problems"].append(f"{p.name}: a zip without pack.mcmeta (datapacks / resource packs) is not installable")
            elif p.name not in ("pack.mcmeta", "pack.png") and p.suffix not in (".md", ".txt"): L["warnings"].append(f"{p.name}: ignored (not part of an install)")
            continue
        if p.name in ("datapacks", "resourcepacks"):
            for q in sorted(p.iterdir()):
                if q.name.startswith("."): continue
                k = pack_kind(q)
                if k is None: L["problems"].append(f"{p.name}/{q.name}: no pack.mcmeta"); continue
                (L["datapacks"] if p.name == "datapacks" else L["resourcepacks"]).append(q)
        elif p.name == "saves":
            for q in sorted(p.iterdir()):
                if q.is_dir() and not q.name.startswith("."):
                    try: has_level = (q / "level.dat").is_file()
                    except OSError as e: L["problems"].append(f"saves/{q.name}: {e}"); continue
                    if has_level: L["saves"].append(q)
                    else: L["problems"].append(f"saves/{q.name}: no level.dat (not a Minecraft world)")
        elif p.name == "config": L["config"].append(p)
        elif p.name in ("mods", "jars"):
            for q in sorted(p.iterdir()):
                if q.suffix == ".jar": add_jar(q, p.name + "/")
                elif q.is_dir() and fabric_json(q): L["projects"].append(q)
        elif fabric_json(p): L["projects"].append(p)
        elif add_pack(p, ""): pass
        elif m not in L["projects"] and not (m / "pack.mcmeta").exists(): L["warnings"].append(f"{p.name}/: ignored (no fabric.mod.json, pack.mcmeta or level.dat)")
    st = L["start"]
    if st.get("world") and st["world"] not in [s.name for s in L["saves"]]:
        L["problems"].append(f"start.yaml: world {st['world']!r} is not in saves/ ({', '.join(s.name for s in L['saves']) or 'no saves'})")
    if not (L["projects"] or L["jars"] or L["datapacks"] or L["resourcepacks"] or L["saves"] or L["config"]):
        L["problems"].append("mod/ holds nothing installable (a Fabric project or jar, a datapack, a resource pack, a save or config/)")
    return L


def tree_hash(p):
    h = hashlib.sha256()
    for f in sorted(x for x in Path(p).rglob("*") if x.is_file() and not (set(x.relative_to(p).parts[:1]) & SKIP)):
        h.update(str(f.relative_to(p)).encode()); h.update(f.read_bytes())
    return h.hexdigest()[:16]


def build_all(mod, main, vid, api_jar, cache):
    """every source project of mod/ -> cached jar (cache/<hash>.jar) -> (ok, {project: jar}, log lines)"""
    L = layout(mod); out, log, ok = {}, [], True
    Path(cache).mkdir(parents=True, exist_ok=True)
    for p in L["projects"]:
        jar = Path(cache) / f"{tree_hash(p)}.jar"
        if jar.is_file(): log.append(f"{p.name}: built before ({jar.name})"); out[str(p)] = str(jar); continue
        r = build(p, jar, main, vid, api_jar)
        log.append(f"{p.name}: {'ok' if r['ok'] else 'FAILED'}\n{r['log']}".strip())
        if r["ok"]: out[str(p)] = str(jar)
        else: ok = False
    return ok, out, log


def check(mod, main, vid, api_jar, cache):
    L = layout(mod)
    ok, jars, log = build_all(mod, main, vid, api_jar, cache) if not L["problems"] else (False, {}, [])
    ids = []
    for j in [*jars.values(), *map(str, L["jars"])]:
        try:
            with zipfile.ZipFile(j) as z: i = json.loads(z.read("fabric.mod.json").decode("utf-8-sig")).get("id")
        except Exception as e: L["problems"].append(f"{Path(j).name}: fabric.mod.json unreadable ({e})"); continue
        if i in BASE_IDS: L["warnings"].append(f"{Path(j).name}: mod id {i} is part of the base install, skipped")
        elif i in ids: L["problems"].append(f"two mods with the id {i}")
        else: ids.append(i)
    summary = {k: [Path(x).name for x in L[k]] for k in ("projects", "jars", "datapacks", "resourcepacks", "saves", "config")}
    return {"ok": ok and not L["problems"], "problems": L["problems"], "warnings": L["warnings"], "build": log, "mod_ids": ids,
            "layout": summary, "start": L["start"]}


def install(mod, inst, main, vid, api_jar, cache, probe=False):
    """the deliverable into the instance, as a player installs it: jars -> mods/, resource packs -> resourcepacks/ (enabled in
    options.txt), config/ -> config/; datapacks and saves are kept in INST/gab/ and put into the world at every start (launch)"""
    inst = Path(inst); g = inst / "gab"; L = layout(mod)
    L["problems"] = [p for p in L["problems"] if "holds nothing installable" not in p]   # an empty mod/ = the vanilla game
    if L["problems"]: return {"ok": False, "problems": L["problems"]}
    ok, built, log = build_all(mod, main, vid, api_jar, cache)
    if not ok: return {"ok": False, "problems": ["build failed"], "build": log}
    for d in (g / "datapacks", g / "saves", inst / "resourcepacks", inst / "config"): shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    for j in (inst / "mods").glob("*.jar"):              # a re-install replaces the previous deliverable's jars
        if not (j.name.startswith("fabric-api-") or j.name == "gabbridge.jar"): j.unlink()
    names = []
    for j in [*built.values(), *map(str, L["jars"])]:
        with zipfile.ZipFile(j) as z: i = json.loads(z.read("fabric.mod.json").decode("utf-8-sig")).get("id")
        if i in BASE_IDS: continue
        dst = inst / "mods" / f"{i}.jar"; shutil.copy(j, dst); names.append(dst.name)
    cp = lambda s, d: shutil.copytree(s, d) if Path(s).is_dir() else shutil.copy(s, d)
    for p in L["datapacks"]: cp(p, g / "datapacks" / p.name)
    rp = []
    for p in L["resourcepacks"]: cp(p, inst / "resourcepacks" / p.name); rp.append(f"file/{p.name}")
    o = inst / "options.txt"; lines = [l for l in o.read_text().splitlines() if not l.startswith(("resourcePacks:", "incompatibleResourcePacks:"))]
    o.write_text("\n".join(lines + ["resourcePacks:" + json.dumps(["vanilla", *rp]), "incompatibleResourcePacks:[]"]) + "\n")
    for c in L["config"]: shutil.copytree(c, inst / "config", dirs_exist_ok=True)
    for s in L["saves"]: shutil.copytree(s, g / "saves" / s.name)
    st = {} if probe else L["start"]
    start = {**DEFAULT_START, **{k: v for k, v in st.items()}, "mode": "save" if st.get("world") else "fresh"}
    (g / "start.json").write_text(json.dumps(start))
    res = {"ok": True, "mods": names, "datapacks": [p.name for p in L["datapacks"]], "resourcepacks": rp, "saves": [s.name for s in L["saves"]],
           "config": bool(L["config"]), "start": start, "build": log, "warnings": L["warnings"]}
    (g / "install.json").write_text(json.dumps(res))
    return res


def fresh_world(inst):
    """every start is a fresh game state: the world (a fresh one with the datapacks, or a copy of the shipped save), new logs"""
    inst = Path(inst); g = inst / "gab"
    st = json.loads((g / "start.json").read_text()) if (g / "start.json").exists() else dict(DEFAULT_START)
    for d in ("saves", "logs", "crash-reports"): shutil.rmtree(inst / d, ignore_errors=True)
    for f in inst.glob("hs_err_pid*.log"): f.unlink()
    (inst / "saves").mkdir()
    w = inst / "saves" / st.get("world", "gab_world")
    if st.get("mode") == "save": shutil.copytree(g / "saves" / st["world"], w)
    (w / "datapacks").mkdir(parents=True, exist_ok=True)
    for p in sorted((g / "datapacks").iterdir()) if (g / "datapacks").is_dir() else []:   # the deliverable's datapacks, also into a save
        d = w / "datapacks" / p.name
        if d.is_dir(): shutil.rmtree(d)
        (shutil.copytree if p.is_dir() else shutil.copy)(p, d)
    return st


# ---------------------------------------------------------------- errors in the game log (gate, MC-STABLE)
OFFLINE_NOISE = re.compile(r"realms|minecraftservices|Services are unavailable|authlib|user properties|profile properties|Connection refused|"
                           r"chat session|getPublicKeys|getCertificates|telemetry|end_of_frame|Failed to connect|ConnectException|SocketException",
                           re.I)
ERR_LINE = re.compile(r"\[[^\]]*/(ERROR|FATAL)\]|^(Caused by: |Exception in thread|java\.lang\.\w*(Error|Exception)|net\.fabricmc\.loader\.\w*Exception|"
                      r"\w+(\.\w+)+(Exception|Error)\b)|Mixin apply .*failed|Could not execute entrypoint|Incompatible mods? found|"
                      r"Failed to (load|parse|read)|Couldn't (load|parse)|Unknown (function|key|item|block)|Errors in currently selected datapacks|"
                      r"crash", re.I)


def errors(inst, limit=40):
    """the game's error lines (offline-network noise left out, deduplicated, first `limit`) + crash reports -> dict"""
    inst = Path(inst); src = inst / "logs" / "latest.log"
    if not src.exists(): src = inst / "gab" / "launcher.log"
    lines = src.read_text(errors="replace").splitlines() if src.exists() else []
    out, seen, n = [], set(), 0
    TS = re.compile(r"^\[\d\d:\d\d:\d\d\] \[")
    def add(l):
        k = TS.sub("[", l).strip()[:300]
        if k and k not in seen and len(out) < limit: seen.add(k); out.append(k)
    if not any(TS.match(l) for l in lines):              # no log4j output (the JVM / launcher failed): every error-looking line
        for l in lines:
            if ERR_LINE.search(l) or l.startswith("Error"): n += 1; add(l)
    else:
        cur, cont = False, 0
        for l in lines:
            if TS.match(l):                              # an entry: only ERROR / FATAL ones count, the offline-network noise never
                cur = bool(re.search(r"/(ERROR|FATAL)\]", l)) and not OFFLINE_NOISE.search(l); cont = 0
                if cur: n += 1; add(l)
            elif cur and l.strip() and not OFFLINE_NOISE.search(l):
                s = l.strip()                            # its exception message and causes, not the stack frames
                if s.startswith("Caused by:") or (cont < 2 and not s.startswith("at ") and not s.startswith("...")): add(l); cont += 1
    crash = sorted((inst / "crash-reports").glob("*.txt")) if (inst / "crash-reports").is_dir() else []
    hs = sorted(inst.glob("hs_err_pid*.log"))
    det = []
    for f in crash[:1]:
        t = f.read_text(errors="replace").splitlines()
        det += [x for x in t[:80] if x.strip() and not x.startswith("//") and not x.lstrip().startswith(("at ", "..."))][:15]
    for f in hs[:1]: det += f.read_text(errors="replace").splitlines()[:8]
    return {"errors": n, "lines": out, "crash": bool(crash or hs), "crash_report": det, "log": str(src)}


def xvfb(display):
    """a private X display for the game (started once, pid in /tmp/gab-mc-xvfb-N.pid)"""
    dn = display.lstrip(":")
    if subprocess.run(["xdpyinfo", "-display", display], capture_output=True).returncode == 0: return {"ok": True, "display": display, "started": False}
    p = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x720x24", "-nolisten", "tcp"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    Path(f"/tmp/gab-mc-xvfb-{dn}.pid").write_text(str(p.pid))
    for _ in range(40):
        if subprocess.run(["xdpyinfo", "-display", display], capture_output=True).returncode == 0: return {"ok": True, "display": display, "started": True}
        time.sleep(0.25)
    return {"ok": False, "error": f"Xvfb {display} did not come up"}


def xvfb_stop(display):
    f = Path(f"/tmp/gab-mc-xvfb-{display.lstrip(':')}.pid")
    try: os.kill(int(f.read_text()), signal.SIGTERM); f.unlink()
    except Exception: pass


# ---------------------------------------------------------------- launch / stop
def offline_uuid(name):
    h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode()).digest()); h[6] = h[6] & 0x0F | 0x30; h[8] = h[8] & 0x3F | 0x80
    return h.hex()


def launch(inst, main, vid, display, port, name):
    inst = Path(inst); g = inst / "gab"; g.mkdir(parents=True, exist_ok=True)
    stop(inst)
    x = xvfb(display)
    if not x["ok"]: print(json.dumps(x)); return
    fresh_world(inst)
    java = find_java(main)
    if not java: print(json.dumps({"ok": False, "error": "no Java >= 25 (the launcher's runtime in MAIN/jvm or /opt/jdk)"})); return
    v, cp = classpath(main, vid)
    missing = [p for p in cp if not Path(p).is_file()]
    if missing: print(json.dumps({"ok": False, "error": f"{len(missing)} game files missing, e.g. {missing[0]}"})); return
    nat = inst / "natives"; nat.mkdir(exist_ok=True)
    R = {"natives_directory": str(nat), "launcher_name": "gab", "launcher_version": "1", "classpath": ":".join(cp), "classpath_separator": ":",
         "auth_player_name": name, "version_name": v["id"], "game_directory": str(inst), "assets_root": f"{main}/assets",
         "assets_index_name": v.get("assets") or (v.get("assetIndex") or {}).get("id", ""), "auth_uuid": offline_uuid(name),
         "auth_access_token": "0", "clientid": "", "auth_xuid": "", "user_type": "legacy", "version_type": v.get("type", "release"),
         "resolution_width": "1280", "resolution_height": "720"}
    feats = {"has_custom_resolution": True}
    sub = lambda s: re.sub(r"\$\{(\w+)\}", lambda m: R.get(m.group(1), ""), s)
    def walk(items):
        out = []
        for it in items or []:
            if isinstance(it, str): out.append(sub(it)); continue
            if not allowed(it.get("rules"), feats): continue
            val = it.get("value"); out += [sub(x) for x in ([val] if isinstance(val, str) else val or [])]
        return out
    jvm = ["-Xms512M", f"-Xmx{os.environ.get('GAB_MC_XMX', '2G')}", "-XX:+UseG1GC", "-Djava.net.useSystemProxies=false"] + walk(v["arguments"]["jvm"])
    game = walk(v["arguments"]["game"]) + ["--proxyHost", "127.0.0.1", "--proxyPort", "9", "--disableMultiplayer"]   # no network: a dead proxy
    cmd = [java, *jvm, v["mainClass"], *game]
    gl = os.environ.get("GAB_MC_GL", "vglrun -d egl").split()
    env = {**os.environ, "DISPLAY": display, "GAB_MC_PORT": str(port), "ALSOFT_DRIVERS": "null", "__GL_SHADER_DISK_CACHE_PATH": str(g)}
    for k in ("M2W_LEASE", "GAMEANYTHING_LEASE"): env.pop(k, None)
    (g / "launch.json").write_text(json.dumps({"cmd": cmd[:3] + ["..."], "display": display, "port": port, "gl": gl}))
    lg = open(g / "launcher.log", "w")
    p = subprocess.Popen(gl + cmd, cwd=str(inst), env=env, stdin=subprocess.DEVNULL, stdout=lg, stderr=subprocess.STDOUT, start_new_session=True)
    (g / "game.pid").write_text(str(p.pid))
    print(json.dumps({"ok": True, "pid": p.pid}))


def pid_of(inst):
    try: return int((Path(inst) / "gab" / "game.pid").read_text().strip())
    except Exception: return None


def alive(inst):
    p = pid_of(inst)
    if not p: return False
    try: return open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"
    except Exception: return False


def stop(inst):
    p = pid_of(inst)
    if p:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try: os.killpg(p, sig)
            except Exception: pass
            for _ in range(20):
                if not alive(inst): break
                time.sleep(0.25)
        try: (Path(inst) / "gab" / "game.pid").unlink()
        except Exception: pass


# ---------------------------------------------------------------- bridge client
def call(port, req, timeout=10.0):
    s = socket.create_connection(("127.0.0.1", int(port)), timeout=timeout); s.settimeout(timeout)
    try:
        s.sendall((json.dumps(req) + "\n").encode()); buf = b""
        while not buf.endswith(b"\n"):
            d = s.recv(1 << 16)
            if not d: break
            buf += d
        return json.loads(buf.decode("utf-8", "replace"))
    finally: s.close()


def log_tail(inst, n=40):
    for f in (Path(inst) / "logs" / "latest.log", Path(inst) / "gab" / "launcher.log"):
        if f.exists(): return "\n".join(f.read_text(errors="replace").splitlines()[-n:])
    return ""


def wait(inst, port, seconds):
    t0 = time.time(); last = None
    while time.time() - t0 < seconds:
        if not alive(inst): return {"ok": False, "error": "the game exited", "log": log_tail(inst)}
        try: h = call(port, {"m": "health"}, 3); last = h
        except Exception as e: h = None; last = {"error": str(e)[:100]}
        if h and h.get("start_error"): return {"ok": False, "error": h["start_error"], "health": h, "log": log_tail(inst)}
        if h and h.get("phase") == "in_world": return {"ok": True, "health": h, "s": round(time.time() - t0, 1)}
        time.sleep(1.0)
    return {"ok": False, "error": f"not in the world after {seconds} s", "health": last, "log": log_tail(inst)}


def state(inst, port):
    if not alive(inst): return {"running": False}
    try: return call(port, {"m": "state"}, 2.8)
    except Exception as e: return {"running": alive(inst), "error": f"the bridge did not answer: {str(e)[:120]}"}


def main(a):
    c = a[0] if a else "help"
    if c == "classpath": print("\n".join(classpath(a[1], a[2])[1]))
    elif c == "build": print(json.dumps(build(a[1], a[2], a[3], a[4], a[5])))
    elif c == "launch": launch(a[1], a[2], a[3], a[4], int(a[5]), a[6])
    elif c == "call":
        try: print(json.dumps(call(int(a[1]), json.loads(a[2]), float(a[3]) if len(a) > 3 else 30)))
        except Exception as e: print(json.dumps({"ok": False, "error": f"no answer from the game: {str(e)[:200]}"}))
    elif c == "wait": print(json.dumps(wait(a[1], int(a[2]), float(a[3]))))
    elif c == "state": print(json.dumps(state(a[1], int(a[2]))))
    elif c == "stop":
        stop(a[1])
        if len(a) > 2: xvfb_stop(a[2])
        print(json.dumps({"ok": True}))
    elif c == "check": print(json.dumps(check(a[1], a[2], a[3], a[4], a[5]), default=str))
    elif c == "install": print(json.dumps(install(a[1], a[2], a[3], a[4], a[5], a[6], probe="--probe" in a), default=str))
    elif c == "errors": print(json.dumps(errors(a[1])))
    elif c == "xvfb": print(json.dumps(xvfb(a[1])))
    elif c == "alive": print(json.dumps({"alive": alive(a[1])}))
    elif c == "probes":
        sys.path.insert(0, str(Path(__file__).parent))
        import mcprobes
        print(json.dumps(mcprobes.run(a[1], int(a[2]), a[3], a[4].split(","), a[5])))
    else: print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

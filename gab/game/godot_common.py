"""Shared by the Godot adapters (godot_tps, tof2): the generic state bridge (godot_state.gd + the game's state_engine.gd,
one autoload GABState added through the instance's override.cfg) and its request / answer over files in the instance's
state dir (docs/STATE_SPEC.md: live values from the running game; no input, no pause, no side effects on the game)."""
import json, uuid
from pathlib import Path

HERE = Path(__file__).parent


def bridge(engine_gd):
    """the bridge script: generic part + the game's _engine() / _ref_pos()"""
    return (HERE / "godot_state.gd").read_text() + "\n\n" + Path(engine_gd).read_text()


def meta_sh(vanilla, mod, sdir):
    """shell: what the bridge needs to tell mod content from vanilla -> <sdir>/modfiles.txt (A = added, C = replaces a
    vanilla file; paths relative to res://, user/... = user://), vanilla_autoloads.txt and vanilla_actions.txt (from the vanilla project.godot)"""
    return (f"mkdir -p {sdir} && : > {sdir}/modfiles.txt && "
            f"if [ -d {mod} ]; then (cd {mod} && find . -type f | sed 's|^\\./||' | while read -r f; do "
            f"if [ -e \"{vanilla}/$f\" ]; then echo \"C $f\"; else echo \"A $f\"; fi; done) > {sdir}/modfiles.txt; fi; "
            f"awk '/^\\[/{{s=$0}} s==\"[autoload]\" && /=/{{split($0,a,\"=\"); print a[1]}}' {vanilla}/project.godot > {sdir}/vanilla_autoloads.txt; "
            f"awk '/^\\[/{{s=$0}} s==\"[input]\" && /^[A-Za-z0-9_]+=[{{]/{{split($0,a,\"=\"); print a[1]}}' {vanilla}/project.godot > {sdir}/vanilla_actions.txt")


def read_state(seat, sdir, pidfile, wait=1.6):
    """one exec: is the game alive (pid file, not a zombie), and if so the bridge's snapshot -> (alive, dict | None). The bridge
    answers on its next frame; a game that cannot answer within `wait` s (loading a level, a stalled GPU) gives its latest
    periodic snapshot (written every 2 s) with engine.stale_s = its age, or None."""
    n = uuid.uuid4().hex[:10]
    r = seat.exec(f"p=$(cat {pidfile} 2>/dev/null); [ -n \"$p\" ] && ps -o stat= -p $p | grep -qv Z || exit 4; "
                  f"rm -f {sdir}/state.json; echo {n} > {sdir}/req.tmp && mv -f {sdir}/req.tmp {sdir}/req; "
                  f"end=$(( $(date +%s%N) + {int(wait * 1e9)} )); while [ $(date +%s%N) -lt $end ]; do "
                  f"grep -qs '\"nonce\":\"{n}\"' {sdir}/state.json && {{ cat {sdir}/state.json; exit 0; }}; sleep 0.03; done; "
                  f"rm -f {sdir}/req; [ -f {sdir}/last.json ] || exit 3; echo $(( $(date +%s) - $(stat -c %Y {sdir}/last.json) )); cat {sdir}/last.json; exit 5",
                  timeout=wait + 30)
    if r.returncode == 4: return False, None
    age, txt = (r.stdout.split("\n", 1) + [""])[:2] if r.returncode == 5 else (None, r.stdout)
    try: d = json.loads(txt) if r.returncode in (0, 5) else None
    except ValueError: d = None
    if d:
        d.pop("nonce", None)
        if age is not None: d.setdefault("engine", {})["stale_s"] = int(age) if age.strip().lstrip("-").isdigit() else None
    return True, d


def state_of(alive, d, why="no answer from the game (busy or paused)"):
    """the STATE_SPEC dict (stable keys also when the bridge did not answer)"""
    if d: return {"running": alive, **d}
    return {"running": alive, "engine": {"screen": "not running" if not alive else why}, "mod": {}, "screen_text": []}


def error_lines(log, err, ignore):
    """Godot log -> the error lines for people, "message @ location" (the `at:` line Godot prints after an error), in log order"""
    from .base import error_lines as el
    L = log.splitlines(); out = []
    for i, l in enumerate(L):
        if err.search(l) and not ignore.search(l):
            at = L[i + 1].strip() if i + 1 < len(L) and L[i + 1].strip().startswith("at:") else ""
            out.append(f"{l.strip()} @ {at[3:].strip()}" if at else l.strip())
    return el("\n".join(out), r".")


def gate_why(res, finished, extra=""):
    """reason of a failed Godot headless gate run"""
    bad = (res or {}).get("failed") or {}
    if bad: return f"{len(bad)} file(s) of the modded project failed: " + "; ".join(f"{f}: {w}" for f, w in list(bad.items())[:8]) + extra
    return ("the headless run did not finish (crashed or hung)" if not finished else "the headless run reported no resources") + extra


def layout(mod_dir, tops):
    """-> the overlay dirs of a deliverable mod/: mod/ itself when it holds project paths (`tops`: vanilla top-level names,
    override.cfg, project.godot), else its subdirs that do (several mods side by side, laid over in name order; also the
    selfcheck's negmod copies, which arrive as <dir>/<name>/), else mod/ itself"""
    m = Path(mod_dir) if mod_dir else None
    if not m or not m.is_dir(): return []
    hit = lambda d: any((d / t).exists() for t in tops)
    if hit(m): return [m]
    subs = sorted(d for d in m.iterdir() if d.is_dir() and hit(d))
    return subs or [m]


def merged(mod_dir, tops, tmp):
    """the overlay dirs of mod_dir laid into one dir under tmp (later ones win) -> path, or None for no files"""
    import shutil
    dirs = layout(mod_dir, tops)
    if not any(p.is_file() for d in dirs for p in d.rglob("*")): return None
    if len(dirs) == 1: return dirs[0]
    out = Path(tmp) / "mod"
    for d in dirs: shutil.copytree(d, out, dirs_exist_ok=True)
    return out

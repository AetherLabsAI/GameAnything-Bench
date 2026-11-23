"""The `game` (development agent) and `play` (play verifier) commands. Each call is a short process: it rebuilds the seat
and the adapter from a context file written by trial.py (ctx.json: game, host, seat, instance, workdir, deadline, mode),
does one thing on the instance and prints a short JSON answer. `play` is the restricted player vocabulary (real input,
start / reset, a screenshot after every action, an action budget); `game` adds build, scripting and the shell.
Every call is logged to calls.jsonl next to ctx.json (<trial>/tools/; workdir/.gab/ when the agent's sandbox cannot write
there). An error whose text matches an infrastructure signature (gab/run/infra.py) is answered as an ENVIRONMENT error with its
code, so the agent does not take it for its own bug and does not fake evidence (an agent once drew screenshots with
ffmpeg after every `game start` failed on the docker socket)."""
import argparse, json, os, sys, time, traceback
from pathlib import Path

import yaml

from ..game import base as gbase
from ..host import base as hbase
from ..host.base import Seat

INPUTS = ("key", "hold", "click", "move", "look", "drag", "type", "wait")


def load_ctx(p):
    c = json.loads(Path(p).read_text()); s = c["seat"]
    seat = Seat(hbase.get(c["host"]), s["sandbox"], s["seat"], s["display"], s["lease"], s.get("home", "/home/player"), s.get("info") or {})
    ad = gbase.get(c["game"], seat)
    return c, ad


def save_ctx(p, c): Path(p).write_text(json.dumps(c, indent=1))


def attach(c, ad):
    """tell the adapter which mod the instance holds (adapters keep it in memory; each call is a new process)"""
    mod = Path(c.get("mod_dir") or Path(c["workdir"]) / "deliverable" / "mod")
    ad.attach(c["inst"], mod if mod.exists() else None)
    return mod


def step_of(a):
    k = a.cmd; v = a.args
    if k == "key": return {"do": "key", "keys": " ".join(v)}
    if k == "hold": return {"do": "hold", "keys": v[:-1], "seconds": float(v[-1])}
    if k in ("click", "move"): return {"do": k, "x": int(v[0]), "y": int(v[1]), **({"button": int(v[2])} if len(v) > 2 else {})}
    if k == "look": return {"do": "look", "dx": int(v[0]), "dy": int(v[1])}
    if k == "drag": return {"do": "drag", "x1": int(v[0]), "y1": int(v[1]), "x2": int(v[2]), "y2": int(v[3]), **({"button": int(v[4])} if len(v) > 4 else {})}
    if k == "type": return {"do": "type", "text": " ".join(v)}
    if k == "wait": return {"do": "wait", "seconds": float(v[0])}


GAME_HELP = """game <command> [args]   — the {game} development kit (docs: devkit/README.md). Answers are JSON.
  info                         instance, display size, time left
  build                        check deliverable/mod and test-load it in a clean headless run (the evaluation's gate)
  start | stop | reset         install deliverable/mod into the dev instance and launch the game on its screen / close it
  state                        structured game state (JSON)
  shot [FILE.png]              screenshot of the game screen (default .gab/shots/NNN.png); look at it with your Read tool
  key KEYS... | hold KEYS... SECONDS | click X Y [BUTTON] | move X Y | look DX DY | drag X1 Y1 X2 Y2 [BUTTON] | type TEXT | wait S
                               real player input (xdotool key names: w, space, Return, Escape, ctrl+s, F1, Up, ...)
  do 'JSON STEP' | replay TRACE.yaml   one trace step / a whole trace on a fresh start (screenshots listed; summary: where it ends, its last state change)
  log [N]                      last N lines of the game log
  check                        what is missing or malformed in your hand-in
  sh 'COMMAND'                 shell command inside the game sandbox, in the instance directory (game data, logs)
  fetch SANDBOX_PATH DEST      copy a file / directory out of the sandbox into your workspace
{extra}  time                         minutes left of your budget"""
PLAY_HELP = """play <command> [args]   — you are a player of {game}. Every action returns a screenshot path (Read it) and the
                               state_diff since your previous action (changed [old, new] / added / removed).
  start | reset                fresh start of the build (the state a new player sees)
  key KEYS... | hold KEYS... SECONDS | click X Y [BUTTON] | move X Y | look DX DY | drag X1 Y1 X2 Y2 [BUTTON] | type TEXT | wait S
  seq 'JSON LIST OF STEPS'     several steps in one call, e.g. '[{{"do":"hold","keys":["w"],"seconds":2}},{{"do":"key","keys":"space"}}]'
  shot                         only a screenshot        state   the game's structured state (read from the running game)
  budget                       actions and minutes left phase2  unlock phase 2 material (see your instructions)
Screen {w}x{h}; xdotool key names (w, a, s, d, space, Return, Escape, Up, F1, ctrl+s, ...)."""


_ANS = []                                   # the answer of this call: printed by main() after the infra check


def out(d, limit=6000): _ANS.append((d, limit)); return d


ENV_NOTE = ("ENVIRONMENT ERROR ({code}, {component}): the benchmark's infrastructure failed; this is NOT caused by you or your "
            "mod. Do not fabricate screenshots, traces, state or other evidence and do not work around the `game` tool. Report it "
            "(e.g. in ACCESS.md) and continue with work that does not need this command, or stop.")
NOT_INFRA = ("model-api",)                  # tool output (game logs, build output) is no place for an API error: "401" in a log is noise
QUIET = {"stdout", "state", "state_diff", "shot", "shots", "sandbox_paths"}   # answer fields that are game / agent data, not errors


def failed(d):
    return isinstance(d, dict) and (d.get("ok") is False or bool(d.get("error")) or bool(d.get("err")) or d.get("rc") not in (None, 0))


def usage_error(e):
    """an exception from parsing the agent's arguments in this file (IndexError of a missing argument, bad JSON, a trace file
    that does not exist): the agent's mistake, not a harness bug"""
    import sysconfig
    lib = tuple(sysconfig.get_paths()[k] for k in ("stdlib", "platstdlib", "purelib", "platlib"))
    fr = [f for f in traceback.extract_tb(e.__traceback__) if not f.filename.startswith(lib)]   # json / yaml / pathlib frames left out
    # also an adapter's dev_command() parsing its arguments (a bad argument raised ValueError inside dev_command -> harness_bug,
    # a systematic code that stopped the whole run); deeper frames stay bugs
    here = bool(fr) and (fr[-1].filename.endswith("control.py") or fr[-1].name == "dev_command")
    return here and isinstance(e, (IndexError, ValueError, KeyError, TypeError, FileNotFoundError, IsADirectoryError, NotADirectoryError)) \
        or type(e).__name__ in ("YAMLError", "ScannerError", "ParserError", "JSONDecodeError")


def agent_file_error(e, wd, args=()):
    """an OSError on a path inside the agent's own workspace (its mod made a directory where the game reads a file, a file
    it deleted, ...) or on a path the agent gave as an argument (or a parent of it): the agent's mistake, answered as an error,
    not harness_bug (e.g. `game build` on a mod whose settings.txt was a directory raised IsADirectoryError, or `game shot
    /home/player/...` a PermissionError: as harness_bug, systematic, they stopped whole runs)"""
    if not isinstance(e, (IsADirectoryError, NotADirectoryError, FileNotFoundError, PermissionError)): return False
    for f in (getattr(e, "filename", None), getattr(e, "filename2", None)):
        if not f: continue
        try:
            if wd and Path(f).resolve().is_relative_to(Path(wd).resolve()): return True
            fp = Path(f).resolve()
            if any(str(x).startswith("/") and Path(str(x)).resolve().is_relative_to(fp) for x in args): return True
        except (OSError, ValueError): pass
    return False


def log_call(ctx, wd, rec):
    for p in ([Path(ctx).parent / "calls.jsonl"] + ([Path(wd) / ".gab" / "calls.jsonl"] if wd else [])):
        try:
            with open(p, "a") as f: f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            return p
        except OSError: continue                       # codex workspace-write: only the workdir is writable


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(add_help=False); ap.add_argument("--ctx", required=True); ap.add_argument("mode", choices=["game", "play"])
    ap.add_argument("cmd", nargs="?", default="help"); ap.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    from . import infra
    t0 = time.time(); _ANS.clear(); wd = None; err = None; inc = []; usage = False
    try:
        c, ad = load_ctx(a.ctx); inst = c["inst"]; wd = Path(c["workdir"])
        left = round((c["deadline"] - time.time()) / 60, 1)
        if a.cmd in ("help", "--help", "-h"):
            if a.mode == "game": print(GAME_HELP.format(game=c["game"], extra=ad.extra_help))
            else: print(PLAY_HELP.format(game=c["game"], w=ad.screen[0], h=ad.screen[1]))
            sys.stdout.flush(); out(None)
        elif left <= 0 and a.cmd not in ("time", "budget"): out({"error": "budget used up: stop now"})
        else:
            mod = attach(c, ad)
            (game if a.mode == "game" else play)(a, c, ad, inst, wd, mod, left)
    except Exception as e:                           # never a traceback to the agent: classify, answer, log
        tb = traceback.format_exc(); err = f"{type(e).__name__}: {e}"
        inc = infra.classify(f"{err}\n{tb}", exclude=NOT_INFRA)
        usage = not inc and usage_error(e)
        mine = not inc and not usage and agent_file_error(e, wd, a.args)
        if not inc and not usage and not mine: inc = [infra.make("harness_bug", tb[-1500:])]
        msg = (f"bad arguments for {a.cmd!r}: {err[:300]}; see `{a.mode} help`" if usage else
               f"{err[:400]} (a file of your workspace is not what the game expects)" if mine else err[:500])
        _ANS[:] = [({"ok": False, "error": msg}, 6000)]
    d, limit = _ANS[-1] if _ANS else ({"ok": False, "error": "no answer"}, 6000)
    if d is None:                                    # help: printed above
        log_call(a.ctx, wd, {"t": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)), "mode": a.mode, "cmd": a.cmd, "s": round(time.time() - t0, 2), "ok": True})
        return None
    bad = failed(d)
    if bad and not inc:
        inc = infra.classify(json.dumps({k: v for k, v in d.items() if k not in QUIET}, ensure_ascii=False, default=str), exclude=NOT_INFRA)
    if inc:
        i = inc[0]
        env = {"code": i["code"], "component": i["component"], "note": ENV_NOTE.format(code=i["code"], component=i["component"]),
               "detail": i["message"][:300]}
        d = {"environment_error": env, **d, "ok": False}          # first: a long answer is cut at `limit`
    print(json.dumps(d, ensure_ascii=False, default=str)[:limit]); sys.stdout.flush()
    first = ("error", "err", "reason", "errors", "stderr", "log", "stage")         # the cause first: the excerpt is the head
    ex = None if not bad and not inc else (err or json.dumps({k: d[k] for k in sorted(d, key=lambda k: (k not in first, first.index(k) if k in first else 0))
                                                              if k not in QUIET | {"environment_error"}}, ensure_ascii=False, default=str))
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)), "mode": a.mode, "cmd": a.cmd,
           "args": [str(x)[:200] for x in a.args[:12]], "s": round(time.time() - t0, 2), "ok": not bad and not inc,
           "rc": d.get("rc"), "error": infra.redact(" ".join(ex.split()))[:500] if ex else None, "usage_error": usage or None}
    if inc: rec.update(infra=inc[0]["code"], kind=inc[0]["kind"], component=inc[0]["component"], also=[i["code"] for i in inc[1:]] or None)
    log_call(a.ctx, wd, {k: v for k, v in rec.items() if v is not None})
    return d


def game(a, c, ad, inst, wd, mod, left):
    k, v = a.cmd, a.args
    shots = wd / ".gab" / "shots"; shots.mkdir(parents=True, exist_ok=True)
    if k == "info": return out({"game": c["game"], "instance": inst, "screen": ad.screen, "minutes_left": left, "mod_dir": str(mod),
                                "sandbox_paths": ad.paths(inst)})
    if k == "fetch":
        dst = (wd / v[1]).resolve() if not Path(v[1]).is_absolute() else Path(v[1]).resolve()
        if wd.resolve() not in dst.parents: return out({"error": f"destination must be inside {wd}"})
        ok = bool(ad.seat.copy_out(v[0], dst))
        hint = None if ok else ("the source is a path inside the game sandbox: give it absolute (game info: sandbox_paths)"
                                if not str(v[0]).startswith("/") else "no such file in the game sandbox")
        return out({"ok": ok, "dest": str(dst), **({"error": hint} if hint else {})})
    if k == "time": return out({"minutes_left": left})
    if k == "check":
        from .trial import check_deliverable
        return out(check_deliverable(wd / "deliverable", c["game"]))
    if k == "build":
        if not mod.exists(): return out({"ok": False, "log": f"{mod} does not exist"})
        ok, log = ad.build(inst, mod)
        if not ok: return out({"ok": False, "stage": "build", "log": log}, 16000)
        gi = f"{inst}-gate"; ad.provision(gi)
        try:
            ad.install_mod(gi, mod)
            ok2, log2 = ad.load_check(gi)
        except Exception as e:
            return out({"ok": False, "stage": "load", "build_log": log[-1500:], "load_log": str(e)[:2500]}, 16000)
        finally: ad.stop(gi)
        g = gbase.read_gate_log(log2)          # the reason and the first error lines whole (adapters put them first), the rest cut
        return out({"ok": ok2, "stage": "load", **({"reason": g["reason"]} if g["reason"] else {}), **({"errors": g["errors"]} if g["errors"] else {}),
                    "build_log": log[-1500:], "load_log": g["detail"][-2500:]}, 16000)
    if k in ("start", "reset"):
        ad.stop(inst)
        if mod.exists():
            try: ad.install_mod(inst, mod)
            except Exception as e: return out({"ok": False, "log": str(e)[:1500]})
        ok, log = ad.start(inst)
        return out({"ok": ok, "log": log[-1500:], "shot": ad.screenshot(inst, shots / f"{int(time.time())}_start.png") if ok else None})
    if k == "stop": ad.stop(inst); return out({"ok": True})
    if k == "state": return out(ad.state(inst))
    if k == "shot":
        # the PNG is written here, in the work dir: a path of the game's machine (e.g. /home/player/gab-minecraft/...) was a
        # PermissionError that counted as harness_bug and stopped the run's new trials
        p = Path(v[0]).resolve() if v else shots / f"{int(time.time() * 10)}.png"
        try: return out({"shot": ad.screenshot(inst, p)})
        except OSError as e:
            return out({"error": f"cannot write the screenshot to {p} ({type(e).__name__}): give a path inside your work dir {wd} "
                                 f"(it is written there, not on the game's machine), or none for {shots}"})
    if k in INPUTS:
        try:
            s = step_of(a)
            r = ad.input(inst, s)
        except Exception as e: return out({"error": f"bad arguments for {k}: {e}; see `game help`"})
        return out({"ok": r is None or r.returncode == 0, **({"err": r.stderr[-300:]} if r is not None and r.returncode else {})})
    if k == "do":
        s = json.loads(v[0]); p = gbase.check_trace({"steps": [s]})
        if p: return out({"error": p})
        if s["do"] == "shot": return out({"shot": ad.screenshot(inst, shots / f"{int(time.time() * 10)}.png")})
        try: ad.input(inst, s)
        except Exception as e: return out({"error": f"bad step {s}: {e}"})
        return out({"ok": True})
    if k == "replay":
        tr = yaml.safe_load(Path(v[0]).read_text()); d = wd / ".gab" / "replay" / f"{Path(v[0]).stem}_{int(time.time())}"
        r = ad.replay(inst, tr, d)
        ans = {"ok": r["ok"], "shots": r["shots"], "problems": [x for x in r["log"] if isinstance(x, str) or "error" in x][:10], "state": r.get("state")}
        if len(json.dumps(ans, ensure_ascii=False, default=str)) > 20000:     # a huge state: cut it
            ans["state"] = json.dumps(ans["state"], ensure_ascii=False, default=str)[:4000] + " ...(cut)"
        return out(ans, 20000)
    if k == "log": return out({"log": ad.log_tail(inst, int(v[0]) if v else 40)})
    if k == "sh":
        d = ad.inst_dir(inst)
        r = ad.seat.exec(f"mkdir -p {d} && cd {d} && " + " ".join(v), timeout=300)
        return out({"rc": r.returncode, "stdout": r.stdout[-5000:], "stderr": r.stderr[-1500:]})
    r = ad.dev_command(inst, k, v, mod, wd)
    if r is not None: return out(r)
    return out({"error": f"unknown command {k!r}; run `game help`"})


def play(a, c, ad, inst, wd, mod, left):
    k, v = a.cmd, a.args; P = c["play"]; d = Path(P["dir"]); (d / "shots").mkdir(parents=True, exist_ok=True)
    if k == "budget": return out({"actions_left": P["actions"] - P["used"], "minutes_left": left})
    if k == "phase2":
        import shutil
        if not P.get("phase2"):
            shutil.copytree(P["phase2_src"], d / "phase2", dirs_exist_ok=True); P["phase2"] = True; save_ctx(a.ctx, c)
        return out({"ok": True, "unlocked": str(d / "phase2"), "files": sorted(str(x.relative_to(d)) for x in (d / "phase2").rglob("*") if x.is_file())})
    if k == "state": return out(ad.state(inst))
    if P["used"] >= P["actions"]: return out({"error": "action budget used up: write your verdict now"})
    P["used"] += 1; n = P["used"]; save_ctx(a.ctx, c); rec = {"n": n, "cmd": k, "args": v, "t": time.strftime("%H:%M:%S")}
    if k in ("start", "reset"):
        ad.stop(inst)
        try: ad.install_mod(inst, mod); ok, log = ad.start(inst)
        except Exception as e: ok, log = False, str(e)[:600]
        rec["ok"] = ok
        if not ok: rec["log"] = log[-600:]
    elif k in INPUTS:
        try: ad.input(inst, step_of(a))
        except Exception as e: rec["error"] = str(e)[:200]
    elif k == "seq":
        steps = json.loads(v[0]); p = gbase.check_trace({"steps": steps})
        if p: rec["error"] = p
        else:
            for s in steps[:30]:
                if s["do"] != "shot": ad.input(inst, s)
    elif k != "shot": rec["error"] = f"unknown command {k!r}; run `play help`"
    rec["shot"] = ad.screenshot(inst, d / "shots" / f"a{n:03d}.png")
    # the state at this screenshot point vs the previous action's (docs/STATE_SPEC.md); kept next to ctx.json, outside the
    # verifier's writable dir; full snapshots under shots/ for the operator
    st = ad.read_state(inst); last = Path(a.ctx).parent / "last_state.json"
    try: prev = json.loads(last.read_text()) if k not in ("start", "reset") else {}
    except Exception: prev = {}
    rec["state_diff"] = gbase.state_diff(prev, st) if prev or k not in ("start", "reset") else {}
    last.write_text(json.dumps(st, default=str)); (d / "shots" / f"a{n:03d}.state.json").write_text(json.dumps(st, indent=1, default=str))
    with open(d / "actions.jsonl", "a") as f: f.write(json.dumps(rec, default=str) + "\n")
    sd = rec["state_diff"] if len(json.dumps(rec["state_diff"], default=str)) < 2500 else gbase.diff_brief(rec["state_diff"], 2500)
    return out({"action": n, **{x: rec[x] for x in ("ok", "error", "log") if x in rec}, "shot": rec["shot"],
                "state_diff": sd if k not in ("start", "reset") else "(fresh start: see `play state`)", "actions_left": P["actions"] - n})


if __name__ == "__main__":
    main()

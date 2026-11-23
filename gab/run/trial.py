"""One trial (docs/SPEC.md): stage the public workspace -> lease a seat, provision a dev instance -> run the agent ->
freeze the deliverable -> evaluate on a fresh instance (eval/) -> result.json. Trial dir: $GAB_RUNS_DIR/<name>/<task>/<level>/
  work/       the staged public workspace the agent sees: brief.md, reference/ (clip_NN.mp4, text_01.md, imgNN.jpg: the files
              only, no titles, authors, captions or sources), task/ (task.yaml + instruction_fine.md), devkit/, deliverable/, .gab/
  tools/      the agent's tools (game, transcribe, ffmpeg, ffprobe; a host agent also gen-image / gen-3d / gen-sfx / gen-music,
              gab/agent/hostgen.py) and the trial context: outside work/, read-only for the agent
  agent/      agent.json (AgentRun), transcript
  deliverable/ frozen copy of work/deliverable (+ its hash in result.json)
  eval/       every evaluator's evidence, prompts, raw answers, costs
  infra.jsonl infrastructure incidents of this trial (gab/run/infra.py; also in the ledger <runs_dir>/_infra/ledger.jsonl)
  attempts/N/ the evidence of an earlier attempt (transcripts, tool-call log, deliverable) when the trial was run again
  result.json"""
import json, sys, os, re, shutil, stat, subprocess, threading, time, traceback
from pathlib import Path

import yaml

from .. import __version__, config
from . import infra
from ..agent import base as abase
from ..game import base as gbase
from ..host import base as hbase
from ..util import log, rjson, tree_hash, wjson

DELIV_DIRS = ("mod", "traces", "shots")

ITEMS = """- mod/              the mod, in the layout of {devkit}/README.md (it is installed into a fresh game the way a player installs it)
- ACCESS.md         how a player reaches and plays the experience from a fresh start of the game with your mod
- traces/*.yaml     2-4 player-input traces that show the experience working, in the trace format of {devkit}/README.md
                    (each starts from a fresh start; give each trace `claims`: what a viewer should see working)
- shots/*.png       3-6 key screenshots of your build (e.g. `game shot {out}/shots/01.png`)"""
GAME = """`game build` and `game start` can take several minutes (a headless load of the game): give such shell commands a timeout of
at least 5 minutes.
Tools on your PATH: ffmpeg, ffprobe, transcribe (offline speech-to-text: `transcribe <audio or video file>`), game, python3,
and the usual shell utilities."""
BRIEF = """You are a game-development agent. Your task (from a user):

{instruction}

Reference material the user means:
{refs}

Target game: {title}. You build a mod for it; the game runs in a sandbox you control with the `game` command
(`game help`; the development kit is in {devkit}). You may launch and play your build as often as you like.
""" + GAME + """

Hand in, in {out}/:
""" + ITEMS + """
`game check` reports what is missing or malformed in your hand-in. Time budget: {minutes:.0f} minutes of wall time in total
(`game time`). When your hand-in is complete, create the empty file {out}/DONE; the hand-in is collected then, or when the
time is up. Work only in {work}."""
# the builder guidance: what is judged, study the references and the target pictures, compare same-view screenshots, the
# generation tools, do not replace the game. Inserted before "Hand in" by brief_text.
GUIDE = """
How the result is judged: how closely the experience in the game matches the user's request AND the reference -- what it
shows (look: shapes, colours, characters, places, mood, interface) as much as what it does (rules, interaction). Placeholder
art, flat colour blocks and bare scenes count against you. Make it look like part of {title}: its own art style, scale and
camera, with your new content in it. Do not replace the original game wholesale.
- Before you build, study the reference: extract frames from the video(s) with ffmpeg and look at them (Read the image files),
  look at every reference image, note the visual details you must reproduce.{targets}
- While you work, check the look in the game: take screenshots with `game shot` from the same view as the reference frames /
  target pictures, put each next to its reference (e.g. `ffmpeg -i shot.png -i ref.png -filter_complex hstack cmp.png`) in
  {work}/compare/, look at the pair, list what is different and fix it. Repeat until they match.
- Generation tools (real assets, not procedural art; bring every result in through the game's own asset pipeline, sized and
  styled to fit the game):
    gen-image "PROMPT" OUT.png [--ref IMG ...] [--aspect-ratio 16:9] [--n 2]   images: textures, sprites, portraits, UI art;
                                       --ref redraws / keeps the look of a reference (e.g. a reference frame, your own sprite)
    gen-3d OUT.glb --image IMG | --prompt "TEXT"     a textured 3D model (minutes) for 3D games
    gen-sfx "PROMPT" OUT.mp3 [--duration S]        sound effects;   gen-music "PROMPT" OUT.mp3 [--duration S]   music
  If one answers an {{"ok": false, ...}} error it is not available this run."""
TARGETS = """
- {work}/targets/ has target pictures: what the result should look like in this game (generated from the reference; follow
  them for layout, look and mood -- your build should resemble them when shot from the same view)."""
KIND_TEXT = {"video": "reference video clip(s) (720p mp4 with the original audio)", "text": "reference text (plain text)",
             "image": "reference image(s)"}


def refs_text(task, w):
    """the brief's list of reference files, grouped by kind (file paths only: nothing about where they come from)"""
    L = []
    for k in ("video", "text", "image"):
        rs = task.references(k)
        if rs: L.append(f"- {KIND_TEXT[k]}:\n" + "\n".join(f"    {w / 'reference' / r['file']}" for r in rs))
    return "\n".join(L)


# reference ablation (`gab run --no-reference` / `--drop-modality KIND`): what the AGENT is given -- its brief
# and work/reference -- without the withheld references; the evaluation keeps the full task (rubric, every reference). The
# target pictures are drawn from the reference video frames / images, so they are withheld too, except when only the text is
# dropped (targets never use the reference text). Default (None): the full task, exactly as before.
REF_KINDS = ("video", "text", "image")
NO_REFS = "- none: this task comes with no reference files; work from the request above."


def reference_setting(no_reference=False, drop=None):
    """the CLI flags -> the trial's reference setting: None (full), "none", or "drop-<kind>" """
    if no_reference and drop: raise ValueError("--no-reference and --drop-modality exclude each other")
    if no_reference: return "none"
    if drop:
        if drop not in REF_KINDS: raise ValueError(f"--drop-modality {drop}: one of {', '.join(REF_KINDS)}")
        return f"drop-{drop}"
    return None


def dropped_kinds(setting):
    if not setting: return set()
    if setting == "none": return set(REF_KINDS)
    if setting.startswith("drop-") and setting[5:] in REF_KINDS: return {setting[5:]}
    raise ValueError(f"unknown reference setting {setting!r}")


class AgentTask:
    """the task as the agent sees it under a reference setting: references() without the dropped kinds, meta (-> work/task/
    task.yaml) with only the kept reference ids and modality; everything else is the task's own"""
    def __init__(self, task, setting):
        self._task, self.reference_ablation = task, setting
        self._drop = dropped_kinds(setting)
        self.withhold_targets = bool(self._drop - {"text"})
    def __getattr__(self, k): return getattr(self._task, k)
    def references(self, kind=None): return [r for r in self._task.references(kind) if r["kind"] not in self._drop]
    def clips(self): return [self._task.mdir / c["file"] for c in self.references("video")]
    def files(self): return [self._task.mdir / r["file"] for r in self.references()]
    @property
    def meta(self):
        m = dict(self._task.meta); keep = {r.get("id") for r in self.references()}
        if isinstance(m.get("references"), list): m["references"] = [i for i in m["references"] if i in keep]
        m["modality"] = "+".join(k for k in REF_KINDS if self.references(k)) or "none"
        return m


def agent_task(task, setting=None, d=None):
    """the task for the agent: setting given, else the trial's trial.json `reference_ablation`; None -> the task itself"""
    if setting is None and d is not None: setting = (rjson(Path(d) / "trial.json", {}) or {}).get("reference_ablation")
    if isinstance(task, AgentTask) or not setting: return task
    return AgentTask(task, setting)


def trial_dir(name, task_id, level): return config.path("runs_dir") / name / task_id / level


def clean_name(s): return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "none"))[:40]


def brief_text(task, level, game, w, minutes):
    """the start-of-trial prompt: BRIEF with GUIDE inserted before the hand-in list (+ the target pictures line when
    w/targets has any)"""
    tg = TARGETS if any((w / "targets").glob("*.png")) else ""
    b = BRIEF.replace("\nHand in, in {out}/:", GUIDE.replace("{targets}", tg) + "\n\nHand in, in {out}/:")
    refs = refs_text(task, w) or (NO_REFS if getattr(task, "reference_ablation", None) else "")
    return b.format(instruction=task.instruction(level), refs=refs, title=gbase.title(game), devkit=w / "devkit",
                    out=w / "deliverable", minutes=minutes, work=w)


def stage(d, task, level, game, minutes=120.0, targets=True):
    """the public workspace: only the reference files, the fine instruction, the dev kit and the tools. references.yaml is NOT
    copied (it names the sources: titles, authors, URLs); task.yaml only with the fields an agent may know"""
    w = d / "work"
    for sub in ("reference", "task", "deliverable/mod", "deliverable/traces", "deliverable/shots", ".gab"): (w / sub).mkdir(parents=True, exist_ok=True)
    for r in task.references():
        dst = w / "reference" / r["file"]
        if not dst.exists(): shutil.copy(task.mdir / r["file"], dst)
    if getattr(task, "reference_ablation", None):        # a workspace staged earlier with more (a re-run, --force): withheld ones out
        keep = {r["file"] for r in task.references()}
        for f in (w / "reference").iterdir():
            if f.name not in keep: shutil.rmtree(f) if f.is_dir() else f.unlink()
        if task.withhold_targets: shutil.rmtree(w / "targets", ignore_errors=True)
    (w / "task" / "task.yaml").write_text(yaml.safe_dump({k: task.meta[k] for k in ("task_id", "target_game", "modality", "references")}, sort_keys=False))
    shutil.copy(task.dir / f"instruction_{level}.md", w / "task" / f"instruction_{level}.md")
    kit = gbase.gdir(game) / "devkit"
    if kit.exists() and not (w / "devkit").exists(): shutil.copytree(kit, w / "devkit")
    if targets and not getattr(task, "withhold_targets", False):   # the task's shared target pictures (gab/refine/refine.py make_targets)
        from ..refine.refine import copy_targets
        copy_targets(task, w)
    return w, brief_text(task, level, game, w, minutes)


def tools(w, ctx_path, mode="game"):
    """executables for the agent: game (or play) -> gab.run.control with this trial's context; transcribe; ffmpeg / ffprobe"""
    b = Path(ctx_path).parent / "bin"; b.mkdir(parents=True, exist_ok=True)   # next to the context, OUTSIDE the agent's workdir
    # (inside work/ an agent could rewrite its `game` script; the agent may not change its tools)
    def sh(name, body):
        p = b / name; p.write_text("#!/bin/bash\n" + body + "\n"); p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH); return str(p)
    # the interpreter running gab, by absolute path, with a clean Python environment: a bare `python3` resolved through the
    # agent's PATH could give a python3 without PyYAML, or another user's site-packages via PYTHONPATH
    T = {mode: sh(mode, f'unset PYTHONHOME PYTHONSTARTUP; PYTHONPATH={config.REPO} PYTHONNOUSERSITE=1 exec {sys.executable} -s -m gab.run.control '
                        f'--ctx {ctx_path} {mode} "$@"')}
    if mode == "game":
        th = config.get("transcribe_home")
        T["transcribe"] = sh("transcribe", (f"export TRANSCRIBE_HOME={th}\n" if th else "") + f'export OMP_NUM_THREADS=8\nexec {config.get("transcribe_cmd")} "$1" 2>/dev/null')
        for t in ("ffmpeg", "ffprobe"):
            src = shutil.which(t)
            if src and not (b / t).exists(): os.symlink(src, b / t)
            T[t] = str(b / t)
    return T


def write_ctx(path, game, seat, inst, workdir, deadline, **extra):
    s = {"sandbox": seat.sandbox, "seat": seat.seat, "display": seat.display, "lease": seat.lease, "home": seat.home,
         "info": {k: v for k, v in seat.info.items() if k in ("project", "user", "id", "sandbox", "seat")}}
    c = {"game": game, "host": seat.backend.name, "seat": s, "inst": inst, "workdir": str(workdir), "deadline": deadline, **extra}
    Path(path).parent.mkdir(parents=True, exist_ok=True); Path(path).write_text(json.dumps(c, indent=1)); return c


def check_deliverable(dv, game=None):
    """the hand-in check (`game check` / `gab check`): what is missing or malformed. -> {ok, problems, warnings}"""
    dv = Path(dv); P, W = [], []
    mod = dv / "mod"
    if not mod.exists() or not any(mod.rglob("*")): P.append("mod/ is missing or empty")
    if not (dv / "ACCESS.md").exists(): P.append("ACCESS.md missing")
    tr = sorted((dv / "traces").glob("*.yaml")) if (dv / "traces").exists() else []
    if not 2 <= len(tr) <= 4: P.append(f"{len(tr)} traces (asked: 2-4 traces/*.yaml)")
    for f in tr:
        try: d = yaml.safe_load(f.read_text())
        except Exception as e: P.append(f"traces/{f.name}: not YAML ({str(e)[:80]})"); continue
        P += [f"traces/{f.name}: {p}" for p in gbase.check_trace(d)]
        if isinstance(d, dict) and not d.get("claims"): W.append(f"traces/{f.name}: no claims")
    sh = sorted((dv / "shots").glob("*.png")) if (dv / "shots").exists() else []
    if not 3 <= len(sh) <= 6: P.append(f"{len(sh)} screenshots (asked: 3-6 shots/*.png)")
    return {"ok": not P, "problems": P, "warnings": W}


def self_check(trial):
    trial = Path(trial)
    dv = trial / "work" / "deliverable" if (trial / "work").exists() else trial / "deliverable" if (trial / "deliverable").exists() else trial
    return check_deliverable(dv)


def audit(transcript, extra=()):
    """paths into hidden material in the agent's log -> list of hits (a hit marks the trial as a leak)"""
    pats = [str(config.path("hidden_dir")), str(config.path("rubrics_dir")), str(config.path("media_dir")),
            str(config.path("edit_dir")), str(config.path("edit_store")), str(config.path("host_fidelity_dir")), *extra]
    if not transcript or not Path(transcript).exists(): return []
    txt = Path(transcript).read_text(errors="replace")
    return sorted({p for p in pats if p and p in txt})


STALE = ("transcript.jsonl", "transcript.stderr.txt", "transcript.*part*.jsonl", "command.txt", "infra.jsonl", "tools/calls.jsonl",
         "tools/vanilla_check.json", "agent", "deliverable", "work/deliverable", "work/.gab")


def archive_attempt(d):
    """move the previous attempt's evidence (transcripts, tool-call log, infra.jsonl, deliverable) into attempts/<n>/: a re-run
    starts clean and its infra scan does not read the old attempt's errors, and nothing is deleted. -> archive dir or None"""
    old = [p for pat in STALE for p in sorted(d.glob(pat)) if p.exists()]
    if not old: return None
    n = 1 + max([int(x.name) for x in (d / "attempts").glob("*") if x.name.isdigit()] or [0]); dst = d / "attempts" / str(n)
    for p in old:
        q = dst / p.relative_to(d); q.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(p), str(q))
    log(f"{d.parent.name}/{d.name}: previous attempt moved to {dst}")
    return dst


RESUME_MOVE = ("transcript.jsonl", "transcript.stderr.txt", "transcript.*part*.jsonl", "command.txt", "infra.jsonl", "tools/calls.jsonl",
               "work/.gab/calls.jsonl")      # evidence the infra scan of the next attempt must not read again
RESUME_COPY = ("agent", "deliverable")       # kept in place (the agent goes on with work/deliverable); a copy goes to attempts/<n>
RESUME_MSG = ("Your previous session was interrupted by an infrastructure problem (not caused by you); it has been fixed. "
              "Your workspace, including {out}, is exactly as you left it, but the game is NOT running: it was stopped with the old "
              "machine; start it again (`game start`) before you send input or take screenshots. "
              "About {left:.0f} minutes of your time budget are left. "
              "A tool that may not have been on your PATH before is now available: `gen-image \"PROMPT\" OUT.png` generates a real "
              "image, if your mod could use one (texture, sprite, icon, UI art, ...) -- `gen-image --help` for its options. If it "
              "answers an error instead, none is set up for this run; that's fine, keep going without it. "
              "Continue the task where you left off; create deliverable/DONE when the hand-in is complete.")


OPERATOR_RESUME_MSG = ("Your session was interrupted: the benchmark operator restarted the run (not caused by you). Your workspace, "
                       "including {out}, is exactly as you left it, but the game is NOT running: start it again (`game start`) "
                       "before you send input or take screenshots. About {left:.0f} minutes of your time budget are left. "
                       "The generation tools (gen-image, gen-3d, gen-sfx, gen-music) work now; if one answered an error earlier "
                       "in this run, try it again. "
                       "Continue the task where you left off; create deliverable/DONE when the hand-in is complete.")
FRESH_NOTE = ("(Your earlier session on this task could not be continued; this is a new session on the same workspace -- look at "
              "what is already in {work} before you go on.)")
MIN_LEFT_INTERRUPTED = 1.0          # minutes: an operator-stopped agent continues with any time left above this


def interrupted_by_operator(prev): return ((prev or {}).get("infra") or {}).get("primary") == "harness_interrupted"


def resume_state(d, minutes, opts=None):
    """can this trial continue its interrupted agent run instead of starting over? -> {sid, left, prev} or None.
    Only after an infra_error caused by the model API alone (infra.resumable: the agent's environment was intact), with the
    deliverable in place and >= 5 minutes of the budget left. Harnesses that cannot resume (or no longer have the session) get a fresh session on the same workspace."""
    if not config.get("infra_resume") or not (opts or {}).get("until_done", True): return None
    if not (d / "work" / "deliverable").is_dir(): return None
    cur = rjson(d / "agent" / "agent.json", {}) or {}
    pend = rjson(d / "agent" / "resume.json", None)       # a resume whose agent never started (e.g. the harness install failed)
    if pend and (cur.get("infra") or {}).get("status") == "infra_error": prev = pend["prev"]
    elif infra.resumable(cur.get("infra")): prev = cur
    else: return None
    left = minutes - (prev.get("wall_s") or 0) / 60
    if left < (MIN_LEFT_INTERRUPTED if interrupted_by_operator(prev) else 5): return None
    return {"sid": (prev.get("extra") or {}).get("session_id"), "left": left, "prev": prev}


def finish_spent_interrupt(d, minutes):
    """an agent stopped by the operator with (almost) none of its budget left: its run is over, recorded as a timeout (not run
    again from the start) -> True when it was finished here"""
    cur = rjson(d / "agent" / "agent.json", {}) or {}
    if not interrupted_by_operator(cur) or (d / "agent" / "resume.json").exists(): return False
    if minutes - (cur.get("wall_s") or 0) / 60 >= MIN_LEFT_INTERRUPTED: return False
    blk = infra.assess([], "timeout", False, 0)
    wjson(d / "agent" / "agent.json", {**cur, "exit_reason": "timeout", "infra_before": cur.get("infra"), "infra": blk})
    if (d / "work" / "deliverable").is_dir(): freeze_deliverable(d / "work" / "deliverable", d / "deliverable")
    (d / "agent" / "interrupted.json").unlink(missing_ok=True)
    log(f"{d.parent.name}/{d.name}: interrupted with {minutes - (cur.get('wall_s') or 0) / 60:.1f} min left: recorded as a timeout")
    return True


def other_live_run(name, game=None):
    """another `gab run --name NAME` process (not this one) that may be running this trial: --game absent or containing game"""
    import glob as _g
    me = os.getpid()
    for f in _g.glob("/proc/[0-9]*/cmdline"):
        try:
            if int(f.split("/")[2]) == me: continue
            c = open(f, "rb").read().split(b"\0")
        except (OSError, ValueError): continue
        if b"run" not in c or b"--name" not in c: continue
        i = c.index(b"--name")
        if i + 1 >= len(c) or c[i + 1].decode(errors="replace") != name: continue
        if game and b"--game" in c and c.index(b"--game") + 1 < len(c) and \
                game not in c[c.index(b"--game") + 1].decode(errors="replace").split(","): continue
        return True
    return False


def archive_for_resume(d):
    """like archive_attempt, but the agent's workspace and deliverable stay: logs move, agent.json / deliverable are copied"""
    n = 1 + max([int(x.name) for x in (d / "attempts").glob("*") if x.name.isdigit()] or [0]); dst = d / "attempts" / str(n)
    for pat in RESUME_MOVE:
        for p in sorted(d.glob(pat)):
            q = dst / p.relative_to(d); q.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(p), str(q))
    for pat in RESUME_COPY:
        p = d / pat
        if p.exists():
            q = dst / pat; q.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copytree if p.is_dir() else shutil.copy)(p, q)
    log(f"{d.parent.name}/{d.name}: resuming; previous attempt's logs in {dst}")
    return dst


def ran_nothing(run):
    """the adapter raised before the agent did anything (no tokens): not an attempt of the agent"""
    return bool((run.extra or {}).get("tb")) and not (run.tokens_in or run.tokens_out)


def combine_runs(prev, run):
    """the totals of an interrupted attempt + its continuation -> one agent record (tokens, cost, time, tool calls)"""
    for f in ("tokens_in", "tokens_out", "tokens_cached"): setattr(run, f, (getattr(run, f) or 0) + (prev.get(f) or 0))
    if prev.get("usd") is not None or run.usd is not None: run.usd = round((run.usd or 0) + (prev.get("usd") or 0), 4)
    run.wall_s = round((run.wall_s or 0) + (prev.get("wall_s") or 0), 1)
    pe = prev.get("extra") or {}
    for k in ("tool_calls", "turns", "steps", "num_turns", "infra_resumes", "usd_estimate", "cost_requests", "cost_priced"):
        if isinstance(pe.get(k), (int, float)): run.extra[k] = run.extra.get(k, 0) + pe[k]
    run.extra["resumed_after"] = (pe.get("resumed_after") or []) + [{"wall_s": prev.get("wall_s"), "infra": (prev.get("infra") or {}).get("primary"),
                                                                     "session_id": pe.get("session_id")}]
    return run


VANILLA_S = 600


def vanilla_start(d, ad, timeout_s=VANILLA_S):
    """does the game start WITHOUT the agent's mod on a fresh instance? (game-start failures are ambiguous: the mod may crash
    the game). Time-bounded, cached per trial in tools/vanilla_check.json. -> {ok, log, s}"""
    f = d / "tools" / "vanilla_check.json"
    if f.exists(): return rjson(f)
    # per trial, not per run (two trials of one run provisioning the same vanilla-<run> instance at once made one start
    # fail -> a false game_vanilla_start_failed)
    import hashlib
    inst = f"vanilla-{clean_name(d.parent.parent.name)}"[:48] + "-" + hashlib.sha1(str(d).encode()).hexdigest()[:8]; res = {}; t0 = time.time()
    def go():
        try:
            ad.provision(inst); ad.attach(inst, None); ok, lg = ad.start(inst); res.update(ok=bool(ok), log=str(lg)[-3000:])
        except Exception as e: res.update(ok=False, log=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}")
    th = threading.Thread(target=go, daemon=True); th.start(); th.join(timeout_s)
    if th.is_alive(): res = {"ok": False, "log": f"vanilla start did not finish in {timeout_s} s", "timed_out": True}
    try: ad.stop(inst)
    except Exception: pass
    res["s"] = round(time.time() - t0, 1); wjson(f, res)
    return res


START_CMDS = ("build", "start", "reset")


OPENCODE_DB = Path("agent_home/opencode/data/opencode/opencode.db")
KILL_CMD = re.compile(r"\b(?:pkill|killall)\b")


def running_kill_command(d):
    """the bash command opencode was still running when it died, if it holds a pkill / killall (e.g.
    `pkill -f "game build"` also matched opencode's own command line, which carries the instruction; the transcript only
    gets a tool call once it completes, so the trial looked like a plain crash). Reads a copy of opencode's sqlite db."""
    db = d / OPENCODE_DB
    if not db.is_file(): return None
    import sqlite3, tempfile
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for f in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
                if f.is_file(): shutil.copy2(f, Path(tmp) / f.name)
            con = sqlite3.connect(Path(tmp) / db.name)
            try: rows = con.execute("select data from part order by rowid desc limit 40").fetchall()
            finally: con.close()
    except Exception: return None
    for (data,) in rows:
        try: p = json.loads(data)
        except Exception: continue
        st = p.get("state") or {}
        if p.get("tool") == "bash" and st.get("status") == "running":
            c = str((st.get("input") or {}).get("command") or "")
            return c if KILL_CMD.search(c) else None
    return None


def agent_infra(d, run, ad=None, minutes=None):
    """after the agent: its tool-call log, transcript + stderr and run.extra errors -> incidents; a vanilla start when game
    starts / builds failed and none succeeded -> the trial's infra block (gab/run/infra.py decide())"""
    w = d / "work"
    recs = infra.calls([d / "tools" / "calls.jsonl", w / ".gab" / "calls.jsonl"])
    game = getattr(ad, "name", None)                      # the adapter's game; else the trial's task
    if not game or game == "base":
        try:
            from .task import Task
            game = Task(d.parent.name).game
        except Exception: game = None
    inc, n_calls = infra.from_calls(recs, build_stops=game in infra.BUILD_STOPS_GAME)
    tr = Path(run.transcript) if run.transcript else None
    if tr and tr.exists():
        err, cmd = infra.errors_of_transcript(tr)
        inc += [{**i, "source": "transcript", "evidence": str(tr)} for i in infra.classify(err)]
        inc += [{**i, "source": "transcript (command output)", "evidence": str(tr)} for i in infra.classify(cmd, exclude=("model-api", "cost-guard"))]
        if run.rc in (143, -15):                          # SIGTERM'd harness: did the agent's own pkill / killall do it?
            m = re.search(r'"command":\s*"((?:[^"\\]|\\.)*?\b(?:pkill|killall)\b(?:[^"\\]|\\.)*)"', tr.read_text(errors="replace")[-4_000_000:])
            if m: inc.append({**infra.make("agent_killed_harness", m.group(1)[:300]), "source": "transcript (agent command)", "evidence": str(tr)})
            else:                                         # the killing command never finished, so it is not in the transcript
                c = running_kill_command(d)
                if c: inc.append({**infra.make("agent_killed_harness", c[:300]), "source": "opencode db (running command)",
                                  "evidence": str(d / OPENCODE_DB)})
    err_tail = ""
    for f in sorted({*d.glob("*stderr*"), *(d / "agent").glob("*stderr*")}):
        if not f.is_file(): continue
        t = f.read_text(errors="replace")[-200000:]; err_tail += t[-1500:]
        inc += [{**i, "source": "stderr", "evidence": str(f)} for i in infra.classify(t)]
    ex = run.extra or {}
    txt = "\n".join(str(ex.get(k) or "") for k in ("error", "errors", "stderr_tail", "usage_error"))
    for s in ex.get("segments") or []: txt += "\n" + "\n".join(str((s.get("extra") or {}).get(k) or "") for k in ("error", "errors", "stderr_tail"))
    inc += [{**i, "source": "agent run", "evidence": str(d / "agent" / "agent.json")} for i in infra.classify(txt)]
    inc += guard_incidents(ex, d / "agent" / "llm_proxy.log")
    if ex.get("tb"):                                  # the adapter itself raised (run_agent caught it)
        m = infra.classify(ex["tb"]) or [infra.make("harness_bug", ex["tb"][-1500:])]
        inc += [{**i, "source": "agent adapter exception", "evidence": str(d / "agent" / "agent.json")} for i in m]
    st = [r for r in recs if r.get("mode", "game") == "game" and r.get("cmd") in START_CMDS]
    if ad is not None and any(not r.get("ok") and not r.get("infra") for r in st) and not any(r.get("ok") for r in st if r.get("cmd") != "build"):
        v = vanilla_start(d, ad)
        if not v.get("ok"):
            more = infra.classify(v.get("log", ""), exclude=("model-api",))
            slow = "did not finish in" in str(v.get("log", ""))        # a timeout under load, not a game that cannot start
            inc += [{**i, "source": "vanilla start", "evidence": str(d / "tools" / "vanilla_check.json")}
                    for i in (more or [infra.make("game_start_slow" if slow else "game_vanilla_start_failed", v.get("log", ""))])]
    if run.exit_reason == "crash" and not (run.tokens_in or run.tokens_out) and not inc:
        # crashed before any model call and nothing above explains it (e.g. EACCES in agent_home after 5 s, otherwise recorded
        # as a normal agent_done): an environment failure, never a result
        inc.append({**infra.make("agent_start_crash", err_tail or str(ex.get("stderr_tail") or "")), "source": "agent run",
                    "evidence": str(d / "agent" / "agent.json")})
    early = ex.get("early_stop") or (run.exit_reason == "done" and ex.get("done_marked") is False)
    return infra.assess(inc, run.exit_reason, early, n_calls)


def guard_incidents(extra, evidence=None):
    """the key proxy's cost guards that fired in this run (run.extra from gab/agent/incontainer.py; also every segment's)"""
    ex = extra or {}; out = []
    segs = [ex] + [s.get("extra") or {} for s in ex.get("segments") or []]
    b = next((x["budget_exceeded"] for x in segs if x.get("budget_exceeded")), None)
    if b: out.append({**infra.make("budget_exceeded", f"gab proxy: spend cap ${b.get('max_usd')} reached (spent ${b.get('spent_usd')})"),
                      "source": "key proxy", "evidence": str(evidence) if evidence else None})
    n = sum(int((x.get("refused") or {}).get("request_too_large") or 0) for x in segs[1:] or segs)
    if n: out.append({**infra.make("request_too_large", f"gab proxy: request too large ({n} requests refused)"), "n": n,
                      "source": "key proxy", "evidence": str(evidence) if evidence else None})
    return out


def record_infra(d, block, attempt=None, phase="agent"):
    for i in (block or {}).get("incidents") or []: infra.record(d, {**i, "sources": [phase, *i.get("sources", [])]}, attempt=attempt)


def freeze_deliverable(src, dv, tries=3):
    """copy the agent's deliverable to <trial>/deliverable. The copy can fail with EACCES (files of the
    agent uid not yet opened by the container cleanup) and left an EMPTY deliverable/ that the next launch took for a finished
    trial: open the tree and retry; a copy that still fails leaves no deliverable/ (the trial runs again)"""
    for k in range(tries):
        shutil.rmtree(dv, ignore_errors=True)
        try: shutil.copytree(src, dv, symlinks=False); return
        except (shutil.Error, OSError):
            if k == tries - 1: shutil.rmtree(dv, ignore_errors=True); raise
            subprocess.run(["chmod", "-R", "a+rwX", str(src), str(dv)], capture_output=True); time.sleep(2 * (k + 1))


def run_agent(d, task, level, agent, model, opts, minutes, seat, ad, attempt=None, may_resume=True):
    rs = resume_state(d, minutes, opts) if may_resume else None
    archive_for_resume(d) if rs else archive_attempt(d)
    task_a = agent_task(task, opts.get("reference_ablation"))   # what the agent is given (reference ablation; default the task)
    w, brief = stage(d, task_a, level, task.game, minutes)
    left = rs["left"] if rs else minutes
    deadline = time.time() + left * 60
    (w / "brief.md").write_text(brief)
    inst = f"dev-{clean_name(d.parent.parent.parent.name)}-{level}"[:48] + "-" + __import__("hashlib").sha1(str(Path(d).resolve()).encode()).hexdigest()[:8]
    ad.provision(inst)
    write_ctx(d / "tools" / "ctx.json", task.game, seat, inst, w, deadline, mode="dev")   # in the trial dir, not the workdir
    T = tools(w, d / "tools" / "ctx.json")
    A = abase.get(agent, model=model, **{k: v for k, v in opts.items() if k not in ("in_container", "brief", "reference_ablation")})
    if opts.get("in_container"):                          # the harness runs inside the seat's container (gab/agent/incontainer.py)
        from ..agent.incontainer import wrap
        A = wrap(A, seat, d, max_usd=config.get("run_max_usd"), spent_usd=((rs or {}).get("prev") or {}).get("usd") or 0.0)
    else:                                                 # an agent on the host: gen-image / gen-3d / gen-sfx / gen-music
        from ..agent.hostgen import wrap as host_gen       # through a host image router (gab/agent/hostgen.py)
        A = host_gen(A, d)
    if opts.get("until_done", True):                     # the run ends when the agent writes deliverable/DONE or time is up
        from ..agent.until_done import UntilDone
        A = UntilDone(A, tag=f"a{attempt or 0}r" if rs else "")
    (d / "agent").mkdir(parents=True, exist_ok=True)
    if rs: wjson(d / "agent" / "resume.json", {"sid": rs["sid"], "prev": rs["prev"]})
    from . import interrupt
    if interrupt.requested():                             # stopped while the seat was set up: nothing of the agent ran
        try: ad.stop(inst)
        except Exception: pass
        interrupt.check(f"{task.id}/{level} before its agent started")
    (d / "agent" / "interrupted.json").unlink(missing_ok=True)
    log(f"{task.id}/{level}: agent {agent} {model or ''} " + (f"resumes ({left:.0f} of {minutes:.0f} min left, session {rs['sid'] or 'new'})" if rs else f"starts ({minutes:.0f} min)"))
    t0 = time.time()
    try:
        if rs:
            msg = (OPERATOR_RESUME_MSG if interrupted_by_operator(rs["prev"]) else RESUME_MSG).format(out=w / "deliverable", left=left)
            run = A.run(w, msg, abase.Budget(minutes=left), T, resume=rs["sid"],
                        fresh=brief + "\n\n" + FRESH_NOTE.format(work=w) + "\n" + msg)
        else: run = A.run(w, brief, abase.Budget(minutes=minutes), T)
    except Exception as e:
        run = abase.AgentRun(exit_reason="crash", wall_s=round(time.time() - t0, 1), extra={"error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-2000:]})
    if run.usd is None and (run.tokens_in or run.tokens_out): run.usd = abase.price_usd(run.model or model, run.tokens_in, run.tokens_out, run.tokens_cached)
    try: ad.stop(inst)
    except Exception: pass
    if run.exit_reason == "interrupted" or (interrupt.requested() and run.exit_reason == "crash"):
        return record_interrupted(d, run, rs, minutes, attempt, task, level)
    leaks = audit(run.transcript)
    try: inf = agent_infra(d, run, ad, minutes)
    except Exception as e: inf = infra.assess([infra.make("harness_bug", traceback.format_exc()[-1500:], source="infra scan")])
    record_infra(d, inf, attempt)
    if rs and ran_nothing(run):                           # the agent never started: keep the resume for the next attempt
        run.extra["resume_pending"] = True
    elif rs:                                              # one record over both attempts (the infra scan above read only this one)
        run = combine_runs(rs["prev"], run)
        leaks = sorted(set(leaks) | set(rs["prev"].get("leaks") or []))
        (d / "agent" / "resume.json").unlink(missing_ok=True)
    wjson(d / "agent" / "agent.json", {**run.to_dict(), "leaks": leaks, "infra": inf})
    freeze_deliverable(w / "deliverable", d / "deliverable")
    log(f"{task.id}/{level}: agent {run.exit_reason} after {run.wall_s:.0f} s; deliverable frozen"
        + (f"; INFRA ERROR {inf['primary']}" if inf["status"] else f"; infra incidents: {', '.join(i['code'] for i in inf['incidents'])}" if inf["incidents"] else ""))
    return run, leaks, inf


def record_interrupted(d, run, rs, minutes, attempt, task, level, mode="run"):
    """the operator stopped gab while this agent worked (gab/run/interrupt.py): agent.json = the run so far as infra_error
    harness_interrupted (resumable: the same `gab run` continues it in its session with the time left) + agent/interrupted.json"""
    run.exit_reason = "interrupted"
    blk = {"status": "infra_error", "primary": "harness_interrupted", "infra_calls": 0,
           "incidents": [infra.make("harness_interrupted", "gab stopped by the operator (signal)", source="gab/run/interrupt.py")]}
    leaks = audit(run.transcript)
    if rs and ran_nothing(run): run.extra["resume_pending"] = True
    elif rs:
        run = combine_runs(rs["prev"], run); leaks = sorted(set(leaks) | set(rs["prev"].get("leaks") or []))
        (d / "agent" / "resume.json").unlink(missing_ok=True)
    if not run.extra.get("session_id") and run.transcript: run.extra["session_id"] = session_in(run.transcript)
    if rs and ran_nothing(run): pass                      # the old agent.json (still the resumable record) stays
    else: wjson(d / "agent" / "agent.json", {**run.to_dict(), "leaks": leaks, "infra": blk})
    record_infra(d, blk, attempt, "interrupted")
    wjson(d / "agent" / "interrupted.json", {"mode": mode, "session_id": run.extra.get("session_id"), "used_s": run.wall_s,
                                             "budget_min": minutes, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "how": "signal"})
    freeze_deliverable(d / "work" / "deliverable", d / "deliverable")
    log(f"{task.id}/{level}: agent interrupted by the operator after {run.wall_s / 60:.1f} of {minutes:.0f} min "
        f"(session {run.extra.get('session_id')}); the same command continues it")
    return run, leaks, blk


def run_trial(name, task, level, agent, model=None, seed=0, opts=None, force=False, minutes=120.0,
              skip_eval=False, host=None, attempt=1):
    """-> result dict (status completed | timeout | build_failed | load_failed | error | infra_error, or agent_done with
    skip_eval). A trial whose result / agent run is an infra_error is not a result: it runs again without --force"""
    d = trial_dir(name, task.id, level)
    old = rjson(d / "result.json") if (d / "result.json").exists() else None
    if old and not force and old.get("status") != "infra_error":
        log(f"{task.id}/{level}: result.json exists, skipped"); return old
    ag = rjson(d / "agent" / "agent.json", {}) or {}
    if skip_eval and not force and (d / "deliverable").is_dir() and ag and (ag.get("infra") or {}).get("status") != "infra_error":
        # the agent part is done (--no-eval has no result.json): leave trial.json as it is (a relaunch would
        # rewrite it with the relaunch's settings, e.g. reasoning_effort, and add an empty attempt)
        return {"task": task.id, "level": level, "status": "agent_done", "infra": ag.get("infra")}
    d.mkdir(parents=True, exist_ok=True)
    prev = rjson(d / "trial.json", {}) or {}
    if prev and not force and config.get("infra_resume") and not other_live_run(name, task.game):
        # the previous `gab run` of this trial was killed / crashed while its agent worked (no graceful stop): recorded as
        # interrupted from its own files, so run_agent continues the session with the time left
        r = mark_interrupted(d, prev, minutes, apply=True, source="gab run (inferred at restart)")
        if r: log(f"{task.id}/{level}: the previous run was killed while its agent worked: {r[0]:.0f} min left, session {r[1]}")
        prev = rjson(d / "trial.json", {}) or {}
    if not force: finish_spent_interrupt(d, minutes)
    old = rjson(d / "result.json") if (d / "result.json").exists() else None
    ag = rjson(d / "agent" / "agent.json", {}) or {}
    if skip_eval and not force and (d / "deliverable").is_dir() and ag and (ag.get("infra") or {}).get("status") != "infra_error":
        return {"task": task.id, "level": level, "status": "agent_done", "infra": ag.get("infra")}
    meta = {"task": task.id, "level": level, "agent": agent, "model": model, "seed": seed, "name": name,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "attempt": attempt, "attempts": prev.get("attempts") or [],
            "reasoning_effort": (opts or {}).get("reasoning_effort")}   # None = the harness / provider default (paper setting)
    ra = (opts or {}).get("reference_ablation")
    if (prev.get("reference_ablation") or None) != ra and prev.get("started") and not force:   # never mix settings in one trial dir
        raise RuntimeError(f"{d}: this trial ran with reference setting {prev.get('reference_ablation') or 'full'}, now "
                           f"{ra or 'full'} was asked: use another --name (or --force to start it over)")
    if ra: meta["reference_ablation"] = ra                # recorded only when set (default trials stay as they were)
    wjson(d / "trial.json", meta)
    res = None
    from .interrupt import Interrupted
    try: res = _run_trial(d, meta, task, level, agent, model, opts, force, minutes, skip_eval, host, attempt)
    except Interrupted as x:                              # stopped by the operator before its agent ran: nothing to record
        log(f"{task.id}/{level}: {x}"); res = {"task": task.id, "level": level, "status": "interrupted"}
    except Exception as x:                                # acquire / provision / eval crashed: an infra cause?
        inc = infra.classify(f"{type(x).__name__}: {x}\n{traceback.format_exc()}", exclude=("model-api",))
        if not inc: raise
        blk = infra.assess([{**i, "source": "trial exception"} for i in inc]); record_infra(d, blk, attempt, "trial")
        log(f"{task.id}/{level}: INFRA ERROR {blk['primary']}: {inc[0]['message'][:200]}")
        res = {"task": task.id, "level": level, "status": "infra_error", "infra": blk, "error": f"{type(x).__name__}: {str(x)[:300]}"}
        if not skip_eval:
            from ..eval.run import infra_result
            res = {**infra_result(d, task, level, blk), "task": task.id, "level": level}
    finally:
        m = rjson(d / "trial.json", meta)
        m["attempts"] = (m.get("attempts") or []) + [{"attempt": attempt, "started": meta["started"], "ended": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                     "status": (res or {}).get("status", "exception"),
                                                     "reasoning_effort": meta.get("reasoning_effort"),
                                                     "infra": ((res or {}).get("infra") or {}).get("primary")}]
        wjson(d / "trial.json", m)
    return res


def _run_trial(d, meta, task, level, agent, model, opts, force, minutes, skip_eval, host, attempt):
    hb = hbase.get(host); seat = None; inf = None
    try:
        req = {"game": task.game}
        if (opts or {}).get("in_container"):              # bind mounts for an agent inside the container (docker host only)
            from ..agent.incontainer import mounts
            req["mounts"] = mounts(d)
        seat = hb.acquire(req, f"gab-{clean_name(meta['name'])}-{task.id[-14:]}-{level[0]}")
        import threading
        stop_hb = threading.Event()
        def beat():                                       # keep the seat's lease alive (host backends may expire a silent one)
            while not stop_hb.wait(1200):
                try: hb.heartbeat(seat)
                except Exception: pass
        threading.Thread(target=beat, daemon=True).start()
        ad = gbase.get(task.game, seat)
        redo = ((rjson(d / "agent" / "agent.json", {}) or {}).get("infra") or {}).get("status") == "infra_error"   # not a real agent run
        if not (d / "deliverable").exists() or force or redo:
            run, leaks, inf = run_agent(d, task, level, agent, model, opts or {}, minutes, seat, ad, attempt,
                                        may_resume=not force)
            if run.exit_reason == "interrupted":           # stopped by the operator: no evaluation, continued on the next start
                return {"task": task.id, "level": level, "status": "infra_error", "infra": inf, "interrupted": True}
        if skip_eval:
            inf = inf or (rjson(d / "agent" / "agent.json", {}) or {}).get("infra")
            st = (inf or {}).get("status")
            return {"task": task.id, "level": level, "status": infra.BUDGET if st == infra.BUDGET else "infra_error" if st else "agent_done", "infra": inf}
        from ..eval.run import eval_trial
        return eval_trial(d, task, level, seat=seat, force=True, attempt=attempt)
    finally:
        if seat:
            stop_hb.set(); seat.release()


def eval_trial(d, task, level, seat=None, force=False, tag=None, deliverable=None):
    from ..eval.run import eval_trial as ev
    return ev(d, task, level, seat=seat, force=force, tag=tag, deliverable=deliverable)


def import_deliverables(name, src, jobs):
    """`gab eval --deliverables DIR`: DIR/<task>/<level>/deliverable/ produced elsewhere -> runs/<name>/<task>/<level>/deliverable"""
    for t, lv in jobs:
        s = src / t.id / lv / "deliverable"
        if not s.exists(): continue
        d = trial_dir(name, t.id, lv); d.mkdir(parents=True, exist_ok=True)
        if not (d / "deliverable").exists(): shutil.copytree(s, d / "deliverable")
        if not (d / "trial.json").exists():
            wjson(d / "trial.json", {"task": t.id, "level": lv, "agent": "external", "model": None, "seed": 0, "name": name})


def session_in(transcript):
    """the harness's session id from a transcript of any harness (first sessionID / session_id / sessionId / thread_id)"""
    try: lines = Path(transcript).read_text(errors="replace").splitlines()
    except OSError: return None
    for ln in lines:
        try: e = json.loads(ln)
        except Exception: continue
        sid = next((e[k] for k in ("sessionID", "session_id", "sessionId", "thread_id") if isinstance(e, dict) and e.get(k)), None)
        if sid: return sid
    return None


def mark_interrupted(d, m, minutes, apply=False, source="inferred at restart"):
    """one trial whose `gab run` died while its agent worked (trial.json `started` without an end: killed / crashed, so no
    agent.json was written) -> (left minutes, session id) or None. apply: record it as infra_error harness_interrupted with the
    time and cost used so far (agent.json; unpriced requests in unpriced.json), so run_agent continues it in its session"""
    if not m or not m.get("started") or not (d / "work" / "deliverable").is_dir(): return None
    if any(x.get("started") == m.get("started") for x in m.get("attempts") or []): return None        # that attempt ended
    cur = rjson(d / "agent" / "agent.json", {}) or {}
    if cur.get("exit_reason") not in (None, "interrupted") and not (d / "agent" / "resume.json").exists():
        return None     # the agent run here ended (e.g. a finished attempt moved back by hand while trial.json still has the
                        # start of a discarded one: never overwrite it as interrupted)
    if (cur.get("infra") or {}).get("primary") == "harness_interrupted" and not (d / "agent" / "resume.json").exists():
        return None     # already recorded (graceful stop, gab/run/interrupt.py)
    pend = rjson(d / "agent" / "resume.json", None)
    prev = pend["prev"] if pend else (cur if (d / "agent" / "resume.json").exists() else {})
    tr = d / "agent" / "transcript.jsonl"
    if not tr.exists(): return None                                                                   # the agent never started
    sid = session_in(tr)
    t0 = time.mktime(time.strptime(m["started"], "%Y-%m-%dT%H:%M:%S"))
    last = max(p.stat().st_mtime for p in [tr, *(d / "agent").glob("*")] if p.is_file())
    costs = [json.loads(x) for x in (d / "agent" / "llm_costs.jsonl").read_text().splitlines() if x.strip()] \
        if (d / "agent" / "llm_costs.jsonl").exists() else []
    usd = round(max(0.0, sum(c.get("cost") or 0 for c in costs) - (prev.get("usd") or 0)), 4)
    part = {"exit_reason": "interrupted", "wall_s": round(max(0.0, last - t0), 1), "usd": usd, "model": m.get("model"),
            "transcript": str(tr), "extra": {"session_id": sid}}
    tot = {**prev, **part, "wall_s": round(part["wall_s"] + (prev.get("wall_s") or 0), 1),
           "usd": round(usd + (prev.get("usd") or 0), 4),
           "extra": {**(prev.get("extra") or {}), "session_id": sid or (prev.get("extra") or {}).get("session_id")}}
    blk = {"status": "infra_error", "primary": "harness_interrupted", "infra_calls": 0,
           "incidents": [infra.make("harness_interrupted", f"gab run stopped at {time.strftime('%H:%M', time.localtime(last))}",
                                    source=source)]}
    left = round(minutes - tot["wall_s"] / 60, 1)
    if apply:
        ended = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last))
        ids = []                                       # charged only when the agent process ends: not priced yet (unpriced.json)
        if (d / "agent" / "llm_proxy.log").exists():
            for ln in (d / "agent" / "llm_proxy.log").read_text(errors="replace").splitlines():
                if ln[:19] >= m["started"] and " POST " in ln and " id=" in ln: ids.append(ln.rsplit(" id=", 1)[1].split()[0])
        up = rjson(d / "agent" / "unpriced.json", {"segments": []}) or {"segments": []}
        up["segments"].append({"started": m["started"], "ended": ended, "ids": ids, "priced": False})
        wjson(d / "agent" / "unpriced.json", up)
        wjson(d / "agent" / "agent.json", {**tot, "infra": blk})
        wjson(d / "agent" / "interrupted.json", {"mode": "run", "session_id": tot["extra"]["session_id"], "used_s": tot["wall_s"],
                                                 "budget_min": minutes, "at": ended, "how": "inferred (no graceful stop)"})
        (d / "agent" / "resume.json").unlink(missing_ok=True)
        m["attempts"] = (m.get("attempts") or []) + [{"attempt": m.get("attempt"), "started": m["started"], "ended": ended,
                                                     "status": "infra_error", "infra": "harness_interrupted"}]
        wjson(d / "trial.json", m); record_infra(d, blk, phase="interrupted")
    return left, sid



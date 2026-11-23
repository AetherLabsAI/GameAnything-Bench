"""Editing mode: an agent gets a finished construction project (the start checkpoint of the parent task) and a change
request; it changes the mod as asked and keeps everything else as it is.

  tasks             benchmark/editing/tasks.json (80 edits; each names its start checkpoint `checkpoint.key`), rubrics.json
                    (edit_success + preservation items), p_to_es.json (preservation items that test the edit itself),
                    checkpoints.json (archive + sha256 per checkpoint key).
  checkpoint store  <edit_store>/<key>/ (`gab media fetch` downloads and unpacks each archive, sha256 checked), read only:
                      work/         the earlier agent's work dir without its private material (reference/, task/, devkit/ are
                                    staged fresh from the parent task)
                      deliverable/  the frozen hand-in (mod, traces, shots, ACCESS.md)
                      eval/         the base evaluation's evidence (gate, replays, visual candidates)
                      result.json, host_fidelity.json, checkpoint.json (where it came from, deliverable hash)
                      p_baseline/   preservation baselines per edit task (gab/eval/edit.py; the only writable part)
  edit round        `gab edit` -> <runs_dir>/<name>/<edit_task_id>/edit/: the store's work dir + the parent's references, task
                    files and dev kit; brief EDIT_BRIEF (parent request + "already implemented" + the change + how it is judged +
                    the generation tools + the hand-in rules); no experience memory; no pre-generated target pictures. Seat,
                    container agent, tools, freeze, infra records: the run_trial machinery (gab/run/trial.py).
  refinement        `gab edit-refine`: record the hand-in's replays (refine/r0/replay), replay the checkpoint's traces the agent
                    changed or removed on the edited mod (refine/r0/replay_ckpt), an edit critic compares BEFORE (checkpoint store)
                    with AFTER -> feedback.md + scores.json, the evaluation gate warning (refine.eval_gate_warning) + a checkpoint
                    comparison (what worked in the checkpoint and fails now), then the agent continues (experience memory on, seeded
                    from the construction run's lessons of the same model and game: seed_memory).
  evaluation        `gab eval-edit` (gab/eval/edit.py): tag `edit` for the edit round's hand-in, `edit-r<K>` after K refine rounds."""
import fcntl, json, os, shutil, subprocess, threading, time, traceback
from contextlib import contextmanager
from pathlib import Path

from .. import config
from ..run import infra
from ..util import log, rjson, tree_hash, wjson

LEVEL = "edit"                       # trial dir <runs_dir>/<name>/<edit_task_id>/edit/ (edit ids start with E-: no collision)
CLASS = {"AP": "appearance", "CS": "content_structure", "BF": "behavior_flow"}


# ---------------------------------------------------------------- data
def _cfg(k, default=None):
    """a config key (GAB_<KEY> env, gab.local.json / gab.toml, DEFAULTS); keys not in DEFAULTS: env / file only, else default"""
    if k in config.DEFAULTS: return config.get(k) or default
    v = os.environ.get("GAB_" + k.upper())
    if v: return v
    try: v = config._toml().get(k)
    except Exception: v = None
    return v if v not in (None, "") else default


def edit_dir(): return config.path("edit_dir")
def tasks_file(): return edit_dir() / "tasks.json"
def rubrics_file(): return edit_dir() / "rubrics.json"
def manifest_file(): return edit_dir() / "checkpoints.json"
def store_root(): return config.path("edit_store")
def store_dir(key): return store_root() / key


def hidden_paths():
    """paths an edit agent must never read (added to the leak audit): the edit rubrics and the checkpoint store (base
    evaluation evidence)"""
    return [str(rubrics_file()), str(edit_dir()), str(store_root())]


def edit_tasks():
    """-> {edit_task_id: row}"""
    return {r["edit_task_id"]: r for r in json.loads(tasks_file().read_text())}


def edit_rubric(eid):
    """the rubric of an edit task: {"edit_success": [...], "preservation": [...]} (anchors with string keys)"""
    from ..run.task import norm_anchors
    for r in json.loads(rubrics_file().read_text()):
        if r["edit_task_id"] == eid:
            return {sec: [{**i, "anchors": norm_anchors(i["anchors"])} for i in r["rubric"].get(sec) or []]
                    for sec in ("edit_success", "preservation")}
    raise KeyError(f"no editing rubric for {eid}")


class EditTask:
    """an edit task: its own id / instruction / rubric; game, references, task files, regression gate of the parent task"""
    def __init__(self, eid, row=None):
        from ..run.task import Task
        self.row = row or edit_tasks()[eid]
        self.id = eid
        self.parent = Task(self.row["parent_task"])
        self.edit_class = self.row["edit_class"]; self.code = self.row.get("edit_class_code") or self.id.rsplit(".", 1)[-1]

    def __getattr__(self, k):                 # game, category, modality, meta, refs, mdir, dir, hidden, regression, references, clips
        if k in ("row", "parent"): raise AttributeError(k)
        return getattr(self.parent, k)

    @property
    def game(self): return self.parent.game

    @property
    def track(self): return self.row.get("track") or ("commercial" if self.parent.meta.get("track") == "commercial" else "open")

    def edit_instruction(self): return self.row["instruction"].strip()

    def parent_instruction(self): return self.parent.instruction("fine")

    def instruction(self, level=LEVEL):
        """the whole request of the edit (memory retrieval / extraction query): parent request + the change"""
        return f"{self.parent_instruction()}\n\nChange request: {self.edit_instruction()}"

    def rubric(self): return edit_rubric(self.id)

    def start_key(self): return (self.row.get("checkpoint") or {}).get("key") or self.parent.id

    def store_path(self): return store_dir(self.start_key())

    def store(self):
        """the store dir of this edit's start checkpoint (downloaded on first use)"""
        return checkpoint(self.start_key())


def select(ids=None, games=None, classes=None, parents=None, track=None):
    """edit tasks by comma lists of ids / game ids / class codes / parent tasks, and track (open | commercial) -> [EditTask]"""
    T = edit_tasks(); out = []
    for eid, r in T.items():
        if ids and eid not in ids: continue
        if games and r.get("game_id") not in games and r["game"] not in games: continue
        if classes and (r.get("edit_class_code") not in classes and r["edit_class"] not in classes): continue
        if parents and r["parent_task"] not in parents: continue
        if track and track != "all" and r.get("track") != track: continue
        out.append(EditTask(eid, r))
    if ids:
        miss = [i for i in ids if i not in T]
        if miss: raise KeyError(f"unknown edit task(s): {', '.join(miss)}")
    return out


# ---------------------------------------------------------------- checkpoint store
def _set_readonly(p, ro=True):
    subprocess.run(["chmod", "-R", "a-w" if ro else "u+w", str(p)], capture_output=True)


def manifest():
    """benchmark/editing/checkpoints.json -> {key: {file, sha256, size_bytes}}"""
    return (rjson(manifest_file(), {}) or {}).get("checkpoints") or {}


def install_checkpoint(key, archive, force=False):
    """unpack a checkpoint archive (<key>/... inside, or the store layout at its top) into the store, read only -> store dir"""
    import tarfile
    out = store_dir(key)
    if (out / "deliverable" / "mod").is_dir() and not force: return out
    if out.exists(): _set_readonly(out, False); shutil.rmtree(out)
    tmp = store_root() / f".{key}.unpack"
    if tmp.exists(): _set_readonly(tmp, False); shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    with tarfile.open(archive) as t:
        for m in t.getmembers():                          # no absolute paths, no "..", no links out of the archive
            if m.name.startswith("/") or ".." in Path(m.name).parts or m.islnk() or (m.issym() and (m.linkname.startswith("/") or ".." in Path(m.linkname).parts)):
                raise RuntimeError(f"{archive}: unsafe member {m.name}")
        t.extractall(tmp)
    src = tmp / key if (tmp / key).is_dir() else tmp
    if not (src / "deliverable" / "mod").is_dir(): raise RuntimeError(f"{archive}: no deliverable/mod inside")
    shutil.move(str(src), str(out)); shutil.rmtree(tmp, ignore_errors=True)
    for sub in ("work", "deliverable", "eval", "result.json", "host_fidelity.json", "trial.json", "checkpoint.json"):
        if (out / sub).exists(): _set_readonly(out / sub)
    (out / "p_baseline").mkdir(exist_ok=True)
    log(f"{key}: checkpoint stored in {out}")
    return out


def checkpoint(key):
    """the store dir of a start checkpoint; downloaded from the dataset on first use (gab/setup/media.py fetch_checkpoint)"""
    out = store_dir(key)
    if (out / "deliverable" / "mod").is_dir(): return out
    from ..setup.media import fetch_checkpoint
    if not fetch_checkpoint(key): raise KeyError(f"{key}: checkpoint not in {store_root()} and not downloadable (gab media fetch)")
    return out


@contextmanager
def file_lock(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)


def ckpt_replays(store):
    """the checkpoint's evaluation replays (store/eval/functions/functions.json) with their screenshot paths mapped into the
    store (the source paths are of the machine that evaluated it) -> R as functions.replays() returns it"""
    fn = Path(store) / "eval" / "functions"; fj = rjson(fn / "functions.json", {}) or {}
    R = []
    for r in fj.get("replays") or []:
        stem = Path(r["trace"]).stem; src = fn / "replay" / stem
        shots = [str(src / Path(s).name) for s in r.get("shots") or [] if (src / Path(s).name).exists()]
        st = (rjson(src / "replay.json", {}) or {}).get("state")
        R.append({**r, "shots": shots, "state": st})
    return R


# ---------------------------------------------------------------- edit round: staging + brief
EDIT_BRIEF = """You are a game-development agent. Earlier, a user asked for a mod for {title}:

{parent_instruction}

Reference material the user meant:
{refs}

This project already implements that request: its mod is in {out}/mod, with its player-input traces ({out}/traces), screenshots
({out}/shots) and access note ({out}/ACCESS.md); {source}
The original request is in {work}/task/instruction_fine.md.

Now the user asks for a change:

{edit_instruction}

The change request is also in {work}/task/edit_request.md.{keep}

The game runs in a sandbox you control with the `game` command (`game help`; the development kit is in {devkit}). You may
launch and play the build as often as you like.
{game}

How the result is judged:
- the change is made as asked, completely (every case the request names), and it shows in the game itself;
- nothing else is broken: what the project did before still works and looks as before (its content, mechanics, interface,
  and what its earlier traces show), and the original game is still there;
- the change looks like part of the game: in {title}'s own art style, scale and camera, consistent with the rest of the mod.
- Before you change anything, build and start the current mod, replay its traces and take screenshots of what the change
  touches (your before pictures).
- While you work, compare same-view screenshots before and after (e.g. `ffmpeg -i before.png -i after.png -filter_complex
  hstack cmp.png`) in {work}/compare/, look at each pair, and fix what differs from the request or changed by accident.
  Before you hand in, replay the earlier traces again and check that what worked before still works.
{tools}
{concept}

Hand in, in {out}/ (update the existing hand-in to the changed mod):
- mod/              the changed mod, in the layout of {devkit}/README.md
- ACCESS.md         updated: how a player reaches the changed part, and the rest of the experience, from a fresh start
- traces/*.yaml     2-4 player-input traces in the trace format of {devkit}/README.md (each starts from a fresh start, with
                    `claims`): at least one shows the change working; keep or update the ones that show the rest still works
- shots/*.png       3-6 key screenshots of the changed build, including ones that show the change
`game check` reports what is missing or malformed in your hand-in. Time budget: {minutes:.0f} minutes of wall time in total
(`game time`). When your hand-in is complete, create the empty file {out}/DONE; the hand-in is collected then, or when the
time is up. Work only in {work}."""
SOURCE_FULL = "the rest of the earlier work (sources, generated assets, scripts) is in {work}."
SOURCE_MOD = ("the earlier work's other source files are not included: the source is the installed mod itself ({out}/mod).")
# no pre-generated edit target pictures; the agent may make its own concept pictures (its own decision)
CONCEPT = """- You may make your own reference or concept pictures of the change with gen-image (e.g. with --ref on a screenshot of
  the current build) and follow them; whether to do so, which pictures and from what material is up to you."""


def edit_tools_text():
    """the brief's generation tool list (gab/run/trial.py GUIDE), same wording"""
    from ..run.trial import GUIDE
    return GUIDE[GUIDE.index("- Generation tools"):].replace("{{", "{").replace("}}", "}")


def full_work(et):
    """does this edit's start come with the whole earlier work dir (False: a non-final round, only its deliverable)?"""
    try: return (rjson(et.store_path() / "checkpoint.json", {}) or {}).get("full_work", True) is not False
    except Exception: return True


def brief_text(et, w, minutes):
    from ..run.trial import GAME, refs_text
    from ..game import base as gbase
    game = GAME.split(" gen-image ")[0].replace("{{", "{").replace("}}", "}")
    src = (SOURCE_FULL if full_work(et) else SOURCE_MOD).format(work=w, out=w / "deliverable")
    return EDIT_BRIEF.format(title=gbase.title(et.game), parent_instruction=et.parent_instruction(), refs=refs_text(et.parent, w),
                             out=w / "deliverable", work=w, edit_instruction=et.edit_instruction(), devkit=w / "devkit",
                             game=game, source=src, concept=CONCEPT, tools=edit_tools_text(), minutes=minutes,
                             keep="" if "keep everything else" in et.edit_instruction().lower() else " Keep everything else as it is.")


def _clear(w):
    """empty a work dir (a fresh attempt): best effort (files of a container uid may resist; chmod first)"""
    subprocess.run(["chmod", "-R", "u+rwX", str(w)], capture_output=True)
    for p in list(Path(w).iterdir()) if Path(w).is_dir() else []:
        if p.is_dir() and not p.is_symlink(): shutil.rmtree(p, ignore_errors=True)
        else: p.unlink(missing_ok=True)


def stage_edit(d, et, minutes=60.0, targets=None):
    """the public workspace of an edit trial: the checkpoint's work dir (store, without the previous agent's private material),
    the parent's references / task files / dev kit (trial.stage), the change request. No edit target pictures
    (the agent may make its own; `targets` is ignored, kept for callers). Staged once
    (work/.gab/edit_staged.json); a resumed attempt keeps the agent's work. -> (work dir, brief)"""
    from ..run.trial import stage
    d = Path(d); w = d / "work"; mark = w / ".gab" / "edit_staged.json"
    if not mark.exists():
        st = et.store()
        if w.exists() and any(w.iterdir()): _clear(w)
        w.mkdir(parents=True, exist_ok=True)
        shutil.copytree(st / "work", w, dirs_exist_ok=True, symlinks=True)
        if not (w / "deliverable" / "mod").is_dir():                   # the work copy should hold it; the frozen one decides
            shutil.copytree(st / "deliverable", w / "deliverable", dirs_exist_ok=True)
        subprocess.run(["chmod", "-R", "u+rwX,go+rwX", str(w)], capture_output=True)   # the store is read-only; the agent uid writes
        for p in ("deliverable/DONE", "lessons.md", "brief.md", "refine_brief.md"): (w / p).unlink(missing_ok=True)
        stage(d, et.parent, "fine", et.game, minutes, targets=False)   # reference/, task/, devkit/, deliverable dirs (only what is missing)
        (w / "task" / "edit_request.md").write_text(et.edit_instruction() + "\n")
        wjson(mark, {"edit": et.id, "parent": et.parent.id, "checkpoint": rjson(st / "checkpoint.json"), "targets": 0,
                     "staged": time.strftime("%Y-%m-%dT%H:%M:%S")})
        subprocess.run(["chmod", "-R", "go+rwX", str(w)], capture_output=True)
    return w, brief_text(et, w, minutes)


# ---------------------------------------------------------------- edit round: the agent
def trial_dir(name, eid): from ..run.trial import trial_dir as td; return td(name, eid, LEVEL)


def _live(name, sub, me=None):
    """another `gab <sub> --name NAME` process alive (not this one)?"""
    import glob as _g
    me = me or os.getpid()
    for f in _g.glob("/proc/[0-9]*/cmdline"):
        try:
            if int(f.split("/")[2]) == me: continue
            c = open(f, "rb").read().split(b"\0")
        except (OSError, ValueError): continue
        if sub.encode() in c and b"--name" in c and c.index(b"--name") + 1 < len(c) and c[c.index(b"--name") + 1].decode(errors="replace") == name:
            return True
    return False


def edit_run_agent(d, et, agent, model, opts, minutes, seat, ad, attempt=None, may_resume=True):
    """trial.run_agent for an edit trial (same resume, wrappers, infra scan, freeze); staging = stage_edit"""
    from ..run import trial as T, interrupt
    from ..agent import base as abase
    rs = T.resume_state(d, minutes, "none", opts) if may_resume else None
    T.archive_for_resume(d) if rs else T.archive_attempt(d)
    w, brief = stage_edit(d, et, minutes)
    left = rs["left"] if rs else minutes
    deadline = time.time() + left * 60
    (w / "brief.md").write_text(brief)
    inst = f"dev-{T.clean_name(d.parent.parent.name)}-{LEVEL}"[:48] + "-" + __import__("hashlib").sha1(str(Path(d).resolve()).encode()).hexdigest()[:8]
    ad.provision(inst)
    T.write_ctx(d / "tools" / "ctx.json", et.game, seat, inst, w, deadline, mode="dev")
    tl = T.tools(w, d / "tools" / "ctx.json")
    A = abase.get(agent, model=model, **{k: v for k, v in opts.items() if k not in ("in_container", "brief", "edit_targets")})
    if opts.get("in_container"):
        from ..agent.incontainer import wrap
        A = wrap(A, seat, d, max_usd=config.get("run_max_usd"), spent_usd=((rs or {}).get("prev") or {}).get("usd") or 0.0)
    else:
        from ..agent.hostgen import wrap as host_gen
        A = host_gen(A, d)
    if opts.get("until_done", True):
        from ..agent.until_done import UntilDone
        A = UntilDone(A, tag=f"a{attempt or 0}r" if rs else "")
    (d / "agent").mkdir(parents=True, exist_ok=True)
    if rs: wjson(d / "agent" / "resume.json", {"sid": rs["sid"], "prev": rs["prev"]})
    if interrupt.requested():
        try: ad.stop(inst)
        except Exception: pass
        interrupt.check(f"{et.id} before its agent started")
    (d / "agent" / "interrupted.json").unlink(missing_ok=True)
    log(f"{et.id}: edit agent {agent} {model or ''} " + (f"resumes ({left:.0f} of {minutes:.0f} min left)" if rs else f"starts ({minutes:.0f} min)"))
    t0 = time.time()
    try:
        if rs:
            msg = (T.OPERATOR_RESUME_MSG if T.interrupted_by_operator(rs["prev"]) else T.RESUME_MSG).format(out=w / "deliverable", left=left)
            run = A.run(w, msg, abase.Budget(minutes=left), tl, resume=rs["sid"], fresh=brief + "\n\n" + T.FRESH_NOTE.format(work=w) + "\n" + msg)
        else: run = A.run(w, brief, abase.Budget(minutes=minutes), tl)
    except Exception as e:
        run = abase.AgentRun(exit_reason="crash", wall_s=round(time.time() - t0, 1), extra={"error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-2000:]})
    if run.usd is None and (run.tokens_in or run.tokens_out): run.usd = abase.price_usd(run.model or model, run.tokens_in, run.tokens_out, run.tokens_cached)
    try: ad.stop(inst)
    except Exception: pass
    if run.exit_reason == "interrupted" or (interrupt.requested() and run.exit_reason == "crash"):
        return T.record_interrupted(d, run, rs, minutes, attempt, et, LEVEL)
    leaks = T.audit(run.transcript, extra=hidden_paths())
    try: inf = T.agent_infra(d, run, ad, minutes)
    except Exception: inf = infra.assess([infra.make("harness_bug", traceback.format_exc()[-1500:], source="infra scan")])
    T.record_infra(d, inf, attempt)
    if rs and T.ran_nothing(run): run.extra["resume_pending"] = True
    elif rs:
        run = T.combine_runs(rs["prev"], run); leaks = sorted(set(leaks) | set(rs["prev"].get("leaks") or []))
        (d / "agent" / "resume.json").unlink(missing_ok=True)
    wjson(d / "agent" / "agent.json", {**run.to_dict(), "leaks": leaks, "infra": inf})
    T.freeze_deliverable(w / "deliverable", d / "deliverable")
    log(f"{et.id}: edit agent {run.exit_reason} after {run.wall_s:.0f} s; deliverable frozen"
        + (f"; INFRA ERROR {inf['primary']}" if inf["status"] else ""))
    return run, leaks, inf


def run_edit_trial(name, et, agent, model=None, opts=None, force=False, minutes=60.0, host=None, attempt=1):
    """the edit round of one edit task -> {task, level, status agent_done | infra_error | interrupted | budget_exceeded}"""
    from ..run import trial as T
    from ..host import base as hbase
    from ..game import base as gbase
    from ..run.interrupt import Interrupted
    opts = opts or {}
    d = trial_dir(name, et.id)
    ag = rjson(d / "agent" / "agent.json", {}) or {}
    done = lambda a: (d / "deliverable").is_dir() and a and (a.get("infra") or {}).get("status") != "infra_error" \
        and a.get("exit_reason") not in (None, "interrupted")
    if done(ag) and not force: return {"task": et.id, "level": LEVEL, "status": "agent_done", "infra": ag.get("infra")}
    if force and d.exists():
        T.archive_attempt(d); (d / "work" / ".gab" / "edit_staged.json").unlink(missing_ok=True)
    d.mkdir(parents=True, exist_ok=True)
    prev = rjson(d / "trial.json", {}) or {}
    if prev and not force and config.get("infra_resume") and not _live(name, "edit"):
        r = T.mark_interrupted(d, prev, minutes, apply=True, source="gab edit (inferred at restart)")
        if r: log(f"{et.id}: the previous edit run was killed while its agent worked: {r[0]:.0f} min left")
        prev = rjson(d / "trial.json", {}) or {}
    if not force: T.finish_spent_interrupt(d, minutes)
    ag = rjson(d / "agent" / "agent.json", {}) or {}
    if done(ag) and not force: return {"task": et.id, "level": LEVEL, "status": "agent_done", "infra": ag.get("infra")}
    st = et.store()
    meta = {"task": et.id, "level": LEVEL, "mode": "edit", "parent_task": et.parent.id, "edit_class": et.edit_class,
            "agent": agent, "model": model, "framework": "none", "seed": 0, "name": name,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "attempt": attempt, "attempts": prev.get("attempts") or [],
            "reasoning_effort": opts.get("reasoning_effort"), "brief": "edit-v1", "minutes": minutes,
            "checkpoint": {k: (rjson(st / "checkpoint.json", {}) or {}).get(k) for k in ("key", "source", "label", "deliverable_sha256", "full_work")}}
    wjson(d / "trial.json", meta)
    res = None; seat = None; stop_hb = threading.Event()
    try:
        hb = hbase.get(host)
        req = {"game": et.game}
        if opts.get("in_container"):
            from ..agent.incontainer import mounts
            req["mounts"] = mounts(d)
        seat = hb.acquire(req, f"gab-{T.clean_name(name)}-{et.id[-14:]}-e")
        def beat():
            while not stop_hb.wait(1200):
                try: hb.heartbeat(seat)
                except Exception: pass
        threading.Thread(target=beat, daemon=True).start()
        ad = gbase.get(et.game, seat)
        run, leaks, inf = edit_run_agent(d, et, agent, model, opts, minutes, seat, ad, attempt, may_resume=not force)
        s = (inf or {}).get("status")
        res = {"task": et.id, "level": LEVEL, "status": "interrupted" if run.exit_reason == "interrupted" else
               infra.BUDGET if s == infra.BUDGET else "infra_error" if s else "agent_done", "infra": inf, "exit_reason": run.exit_reason}
        if run.exit_reason == "interrupted": res["interrupted"] = True
    except Interrupted as x:
        log(f"{et.id}: {x}"); res = {"task": et.id, "level": LEVEL, "status": "interrupted"}
    except Exception as x:
        inc = infra.classify(f"{type(x).__name__}: {x}\n{traceback.format_exc()}", exclude=("model-api",))
        if not inc: raise
        blk = infra.assess([{**i, "source": "trial exception"} for i in inc]); T.record_infra(d, blk, attempt, "trial")
        log(f"{et.id}: INFRA ERROR {blk['primary']}: {inc[0]['message'][:200]}")
        res = {"task": et.id, "level": LEVEL, "status": "infra_error", "infra": blk, "error": f"{type(x).__name__}: {str(x)[:300]}"}
    finally:
        stop_hb.set()
        if seat: seat.release()
        m = rjson(d / "trial.json", meta)
        m["attempts"] = (m.get("attempts") or []) + [{"attempt": attempt, "started": meta["started"], "ended": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                     "status": (res or {}).get("status", "exception"),
                                                     "infra": ((res or {}).get("infra") or {}).get("primary")}]
        wjson(d / "trial.json", m)
    return res


# ---------------------------------------------------------------- iteration: critic (edit version)
EDIT_KINDS = {
    "edit_not_done": "a part of the change request does not show in the game at all (a claim or ACCESS.md promise alone does not count)",
    "edit_partial": "a part of the change is there but incomplete: not every case the request names, only told by text, or "
                    "only in some views",
    "broke_other": "something the project did or showed BEFORE is broken, missing or changed now although the request did not "
                   "ask for it (content, mechanics, interface, a trace that worked before)",
    "game_error": "error, crash, freeze, replay broke off, the game does not start with the mod",
    "off_style": "the change does not look like part of the game: style, scale or quality clash with the game and the rest of the mod",
    "claim_unproven": "a trace claim or ACCESS.md promise is not visible or not true in the replay",
    "other": "anything else",
}
EDIT_CRITIC_BRIEF_VERSION = "edit-v1"
EDIT_CRITIC_BRIEF = """You are an independent, strict reviewer. A game-development agent was given a working mod for {title} and a
user's request to change it. You do NOT trust its claims: judge only what you can see.

What the project did before the change (its original request, for context):
{parent_instruction}

The change the user asked for now:
{edit_instruction}
Everything the request does not name should stay as it was.

In {work}:
- before/      the project BEFORE the change: shots/*.png (its key screenshots), replay/<trace>/*.png (screenshots of its
               traces replayed on a fresh game by an earlier check), traces/*.yaml, ACCESS.md
- after/       the hand-in AFTER the change: replay/*.mp4 (screen recordings of its traces replayed on a fresh game),
               replay/<trace>/*.png (screenshots taken during those replays), shots/*.png, traces/*.yaml (with `claims`),
               ACCESS.md{old_traces}
- reference/   the original reference of the project (context only){targets}

Tools: ffmpeg / ffprobe and python3 on your PATH; look at images with your Read tool. You cannot watch a video directly:
extract frames and look at them. Every image you read stays in your context, so read few, information-dense images (about 20
in all, never more): one contact sheet per video (e.g. `ffmpeg -i after/replay/1_x.mp4 -vf "fps=1/4,scale=480:-1,tile=4x3"
-frames:v 1 -q:v 3 frames/sheet_1_x.jpg`), side-by-side pairs (`hstack`) of a BEFORE picture next to the AFTER picture of the
same view, single frames only where you need detail. Save every image you make as .jpg, at most 1600 px on its longest side.
Never write placeholder content: write the files from what you have actually looked at.

1. The change. Split the change request into the parts it asks for (each thing to change, each case it names, e.g. "every",
   "also newly placed", "only at the rear", "at night ... by day"), in the request's own words. For each part decide from the
   AFTER replays and shots only: done / partly (incomplete, some cases or views only, or only told by a message) / not done.
   A part not done is an `edit_not_done` issue of severity high; partly is `edit_partial` medium.
2. Preservation. Put BEFORE and AFTER side by side (the same trace / the same view) and list everything that worked or was
   visible before and is broken, missing or changed now although the request did not ask for it (`broke_other`, high when
   a mechanic or a whole part is gone). Runtime errors, crashes, replays that broke off: `game_error`.
3. The look: does the change look like part of {title} and of this mod (`off_style`)?

Write {work}/feedback.md:
1. "Change coverage": one line per part: part | done / partly / not done | evidence (file and time).
2. "Issues": most important first. Each issue: `kind` (one of the list below), `severity` (high / medium / low), `evidence`
   (a frame or file you looked at, with time), what is wrong, and a concrete fix the developer can do in the game.
3. "Top fixes": the 3-5 changes that would improve the result the most: game_error first, then every part not done or partly
   done, then everything broken.
Kinds:
{kinds}

Write {work}/scores.json: {{"edit_done": 0-5, "preservation": 0-5, "style_fit": 0-5, "notes": "one line",
"coverage": [{{"part": "...", "status": "done|partly|missing"}}, ...], "missing": ["<part not done or partly done>", ...],
"broken": ["<what worked before and is broken now>", ...]}}
(0 = not at all / everything broken, 5 = fully done / nothing broken / fits perfectly).
When both files are complete, create the empty file {work}/REVIEW_DONE -- the review is collected then. You have about
{minutes:.0f} minutes."""
EDIT_CRITIC_CONTINUE = ("The review is not finished (REVIEW_DONE does not exist). Do not read more images unless you must: write the "
                        "complete feedback.md (Change coverage; Issues with kind, severity, evidence, fix; Top fixes) and scores.json "
                        "(with coverage, missing, broken) from what you have seen, then create REVIEW_DONE.")


def _copy_pngs(src, dst, limit=None):
    src, dst = Path(src), Path(dst); n = 0
    if not src.is_dir(): return 0
    for f in sorted(src.rglob("*")):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".mp4", ".yaml", ".md") and "state" not in f.relative_to(src).parts:
            q = dst / f.relative_to(src); q.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(f, q); n += 1   # (no read-only mode from the store)
            if limit and n >= limit: break
    return n


def edit_critic(d, et, seat, round_dir, replay_dir, model=None, minutes=None, dv=None, retries=None, old_replay_dir=None):
    """the edit reviewer (refine.critic with BEFORE / AFTER material and EDIT_CRITIC_BRIEF) -> round_dir/critic/{feedback.md,
    scores.json}; returns the scores dict (or None)"""
    if retries is None: retries = int(config.get("critic_retries") or 3)   # fresh-session retries of an incomplete review (a vision critic sometimes ends with an empty answer)
    from ..refine import refine as R
    from ..agent import base as abase
    from ..agent.incontainer import wrap, bridge_dir
    from ..game import base as gbase
    from ..agent.until_done import UntilDone
    from ..run import interrupt
    minutes = float(minutes or config.get("critic_minutes") or 15.0)
    rd = Path(round_dir) / "critic"
    if (rd / "scores.json").exists() and (rd / "feedback.md").exists(): return rjson(rd / "scores.json")
    shutil.rmtree(rd, ignore_errors=True); rd.mkdir(parents=True)
    run_dir = Path(d) / "agent_home" / ".critic" / f"{Path(round_dir).name}-{time.strftime('%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
    while run_dir.exists(): run_dir = run_dir.with_name(run_dir.name + "x")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / ".gab-bridge").write_text(str(bridge_dir(d)) + "\n")
    w = run_dir / "work"; (w / "after").mkdir(parents=True); (w / "before").mkdir()
    st = et.store(); bdv = st / "deliverable"
    for sub in ("shots", "traces"):
        if (bdv / sub).is_dir(): shutil.copytree(bdv / sub, w / "before" / sub, copy_function=shutil.copyfile)
    if (bdv / "ACCESS.md").exists(): shutil.copyfile(bdv / "ACCESS.md", w / "before" / "ACCESS.md")
    _copy_pngs(st / "eval" / "functions" / "replay", w / "before" / "replay")
    dv = Path(dv or Path(d) / "deliverable")
    for sub in ("shots", "traces"):
        if (dv / sub).is_dir(): shutil.copytree(dv / sub, w / "after" / sub)
    if (dv / "ACCESS.md").exists(): shutil.copy(dv / "ACCESS.md", w / "after" / "ACCESS.md")
    from ..util import sha256
    old = {sha256(f) for f in (bdv / "shots").glob("*.png")} if (bdv / "shots").is_dir() else set()
    for f in (w / "after" / "shots").glob("*.png") if (w / "after" / "shots").is_dir() else []:
        if sha256(f) in old: f.unlink()                                # unchanged from BEFORE: not evidence of the change
    if Path(replay_dir).is_dir(): _copy_pngs(replay_dir, w / "after" / "replay")
    ot = ""
    if old_replay_dir and Path(old_replay_dir).is_dir() and any(Path(old_replay_dir).glob("*.mp4")):
        _copy_pngs(old_replay_dir, w / "after" / "replay_old_traces")
        ot = ("\n               replay_old_traces/  the BEFORE traces that the agent changed or removed, replayed on the changed "
              "mod (recordings + screenshots)")
    (w / "reference").mkdir()
    for r in et.parent.references(): shutil.copy(et.parent.mdir / r["file"], w / "reference" / r["file"])
    tg = ""                                                            # no edit target pictures
    kinds = "\n".join(f"  {k}: {v}" for k, v in EDIT_KINDS.items())
    brief = EDIT_CRITIC_BRIEF.format(title=gbase.title(et.game), parent_instruction=et.parent_instruction(),
                                     edit_instruction=et.edit_instruction(), work=w, old_traces=ot, targets=tg, kinds=kinds, minutes=minutes)
    (w / "brief.md").write_text(brief)
    model = model or config.get("critic_model")
    A = abase.get("opencode", model=model, base_url=config.get("critic_base_url"), api_key_env=config.get("critic_key_env"), auth="api")
    from ..host.keyproxy import CRITIC_SHRINK
    inner = wrap(A, seat, run_dir, max_usd=config.get("critic_max_usd"), proxy_shrink=CRITIC_SHRINK)
    A = UntilDone(inner, marker="REVIEW_DONE", message=EDIT_CRITIC_CONTINUE, tag="critic", max_continuations=R.CRITIC_MAX_CONTINUATIONS)
    t0 = time.time()
    try: run = A.run(w, brief, abase.Budget(minutes=minutes), {})
    except Exception as e: run = abase.AgentRun(exit_reason="crash", wall_s=round(time.time() - t0, 1), extra={"error": f"{type(e).__name__}: {e}"})
    done = lambda: (w / "REVIEW_DONE").exists() and (w / "feedback.md").exists() and (w / "scores.json").exists()
    finish = None
    if not done() and not (run.extra or {}).get("budget_exceeded") and run.exit_reason not in ("crash", "interrupted") and not interrupt.requested():
        tr = run_dir / "agent" / "transcript.jsonl"
        if tr.exists(): tr.replace(tr.with_name("transcript.review.jsonl"))
        (w / "REVIEW_DONE").unlink(missing_ok=True)
        fm = max(6.0, min(10.0, minutes - (time.time() - t0) / 60))
        F = UntilDone(inner, marker="REVIEW_DONE", message=EDIT_CRITIC_CONTINUE, tag="criticfinish", max_continuations=1)
        t1 = time.time()
        try: fr = F.run(w, R._finish_prompt(brief, w), abase.Budget(minutes=fm), {})
        except Exception as e: fr = abase.AgentRun(exit_reason="crash", wall_s=round(time.time() - t1, 1), extra={"error": f"{type(e).__name__}: {e}"})
        finish = {"exit_reason": fr.exit_reason, "wall_s": fr.wall_s, "usd": fr.usd, "complete": done()}
        for f in ("usd", "tokens_in", "tokens_out"):
            if getattr(fr, f) is not None: setattr(run, f, (getattr(run, f) or 0) + getattr(fr, f))
        run.wall_s = round(time.time() - t0, 1)
    complete = done()
    for f in ("feedback.md", "scores.json"):
        if (w / f).exists(): shutil.copy(w / f, rd / (f if complete else f"incomplete_{f}"))
        elif not complete and (w / f"draft_{f}").exists(): shutil.copy(w / f"draft_{f}", rd / f"incomplete_{f}")
    if (w / "frames").is_dir(): shutil.copytree(w / "frames", rd / "frames", dirs_exist_ok=True)
    for f in ("transcript.jsonl", "llm_proxy.log", "command.txt"):
        if (run_dir / "agent" / f).exists(): shutil.copy(run_dir / "agent" / f, rd / f)
    if not complete: interrupt.check(f"edit review {Path(round_dir).name}")
    try: sc = json.loads((rd / "scores.json").read_text()) if (rd / "scores.json").exists() else None
    except Exception: sc = None
    over = (run.extra or {}).get("budget_exceeded")
    wjson(rd / "critic.json", {"model": model, "brief": EDIT_CRITIC_BRIEF_VERSION, "complete": complete, "exit_reason": run.exit_reason,
                               "wall_s": run.wall_s, "usd": run.usd, "tokens_in": run.tokens_in, "tokens_out": run.tokens_out, "scores": sc,
                               "continuations": (run.extra or {}).get("continuations"), "finish_session": finish})
    if not complete and retries > 0 and not over:
        log(f"{et.id}: edit review {Path(round_dir).name} incomplete, fresh retry ({retries} left)")
        dst = rd.with_name(f"critic_incomplete_{int(time.time())}")
        while dst.exists(): dst = dst.with_name(dst.name + "x")   # several retries in the same second
        shutil.move(str(rd), str(dst))
        return edit_critic(d, et, seat, round_dir, replay_dir, model, minutes, dv, retries - 1, old_replay_dir)
    return sc


# ---------------------------------------------------------------- iteration: checkpoint comparison
CKPT_WARNING = """## Regressions against the project you started from

These worked in the project BEFORE your change and fail now (the change request did not ask for this):
{lines}
Fix them while you finish the change: the change must not break what was there.

"""


def changed_traces(d, et):
    """the checkpoint's traces that the hand-in changed or removed -> [paths in the store]"""
    st = et.store(); new = Path(d) / "deliverable" / "traces"; out = []
    for f in sorted((st / "deliverable" / "traces").glob("*.yaml")):
        g = new / f.name
        if not g.exists() or g.read_bytes() != f.read_bytes(): out.append(f)
    return out


def ckpt_compare(d, et, round_dir, gate=None):
    """what worked in the checkpoint and fails now -> round_dir/ckpt_compare.json; a section on top of critic/feedback.md (after
    the gate warning, if any). Compared: the gate (the checkpoint passed its evaluation gate), each checkpoint trace that ran in
    the checkpoint's evaluation (replayed now: unchanged traces in replay/, changed or removed ones in replay_ckpt/)."""
    rd = Path(round_dir); st = et.store()
    cres = rjson(st / "result.json", {}) or {}; lines = []
    before_ok = {Path(r["trace"]).name: bool(r.get("ok")) for r in rjson(st / "eval" / "functions" / "functions.json", {}).get("replays") or []}
    now = {}
    for sub in ("replay", "replay_ckpt"):
        for t in (rjson(rd / sub / "record.json", {}) or {}).get("traces") or []:
            if sub == "replay" and t["trace"] in {f.name for f in changed_traces(d, et)}: continue   # the new trace of that name
            now[t["trace"]] = t
    for tr, ok in sorted(before_ok.items()):
        if ok and tr in now and not now[tr].get("ok"):
            lines.append(f"- trace {tr} of the starting project replayed fine before; on your changed mod it fails: "
                         f"{str(now[tr].get('error') or 'the replay did not complete')[:300]}")
    cgate = (cres.get("gate") or {}).get("pass")
    if gate and cgate is True and gate.get("pass") is False:
        lines.insert(0, f"- the evaluation gate passed for the starting project and fails now ({gate.get('fail')}): see the section above")
    res = {"checkpoint_gate": cgate, "gate_now": (gate or {}).get("pass"), "traces_before": before_ok,
           "traces_now": {k: bool(v.get("ok")) for k, v in now.items()}, "regressions": lines}
    wjson(rd / "ckpt_compare.json", res)
    fb = rd / "critic" / "feedback.md"
    if lines and fb.exists():
        txt = fb.read_text()
        if "## Regressions against the project you started from" not in txt:
            sec = CKPT_WARNING.format(lines="\n".join(lines))
            if txt.startswith("## FIRST: the evaluation rejects"):           # after the gate warning
                i = txt.find("\n## ", 5); i = len(txt) if i < 0 else i + 1
                fb.write_text(txt[:i] + sec + txt[i:])
            else: fb.write_text(sec + txt)
    return res


# ---------------------------------------------------------------- iteration: the agent
EDIT_REFINE_MSG = """This is an IMPROVEMENT round of about {minutes:.0f} minutes on the change you made to this project. Your
work is still in {work} (mod, traces, shots, ACCESS.md in {out}); you may change anything.

An independent reviewer replayed your traces, recorded the screen and compared the result with the project as it was BEFORE
your change. Its review is in {work}/review/feedback.md (change coverage; issues by kind and severity, with evidence) and
review/scores.json; its frames are in review/frames/ when it kept any.{lessons}
Start with the review's "Change coverage": make every part marked "not done" or "partly" happen in the game itself, and add a
trace and a shot that show it. Then fix everything listed as broken (what worked before must work again) and the rest of the
"Top fixes", the most severe first. Do not rebuild or restyle what the change request does not name; before you hand in,
replay the traces and check that what worked before still works. You may make your own reference or concept pictures with
gen-image (e.g. --ref on a screenshot) and follow them, if you find that useful; all tools of the task below are available.

Keep `game check` passing and the hand-in consistent with the mod: update traces, shots and ACCESS.md to what you changed.
{out}/DONE was removed for this round: create it again when you are done -- it is accepted only after minute
{min_done:.0f} of this round (use the time to fix and verify), or the round ends when the time is up.

The task, as given to you before:

{brief}"""


def construction_run(name):
    """the construction run of the same configuration as an edit memory name: GAB_EDIT_MEMORY_FROM, else the name without its
    '-edit' part (sonnet55-claudecode-edit-s0 -> sonnet55-claudecode-s0; my-agent-edit, the editing memory of a `gab bench`
    run that holds both modes -> my-agent)"""
    v = _cfg("edit_memory_from")
    if v: return v
    parts = [x for x in str(name).split("-") if x != "edit"]
    return "-".join(parts) if parts != str(name).split("-") else None


def seed_memory(d, et):
    """the edit run's experience memory starts from the construction run's lessons of the same model and game: <memory>/<construction run>/games/<game>.md copied once into the edit run's memory (when it has no file for
    the game yet), recorded in its SEED.json. -> the source file or None"""
    from ..refine import memory as mem
    src_name = construction_run(mem.run_name(d))
    if not src_name: return None
    src = mem.game_file(mem.root(src_name), et.game)
    if not src.exists(): return None
    r = mem.root(mem.run_name(d)); rel = f"games/{et.game}.md"
    with mem.locked(r):
        f = r / rel
        if f.exists(): return None
        f.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, f)
        sj = r / "SEED.json"; seed = rjson(sj, None) or {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": {}}
        import hashlib
        seed.setdefault("files", {})[rel] = {"seeded_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "from": f"memory:{src_name}/{rel}",
                                             "seed_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                                             "note": "the construction run's lessons (same model, same game)"}
        sj.write_text(json.dumps(seed, ensure_ascii=False, indent=1))
    log(f"{et.id}: edit memory seeded from {src}")
    return src


def edit_refine_agent(d, et, agent, model, opts, minutes, seat, ad, feedback_dir, round_k=1, resume=None, memory=False):
    """refine.refine_agent for an edit trial: the same agent continues on its work/ with EDIT_REFINE_MSG + the edit review"""
    from ..agent import base as abase
    from ..run.trial import archive_for_resume, write_ctx, tools, clean_name, freeze_deliverable, combine_runs, FRESH_NOTE
    from ..run import interrupt
    from ..refine import memory as mem
    from ..refine.refine import LESSONS_LINE, REFINE_RESUME_MSG, _done_rule
    d = Path(d); w = d / "work"
    if not (w / "deliverable").is_dir(): raise RuntimeError(f"{d}: no work/deliverable to refine")
    if (d / "agent").exists() or (d / "deliverable").is_dir(): archive_for_resume(d)
    mrec = None
    if not resume:
        rv = w / "review"; shutil.rmtree(rv, ignore_errors=True); rv.mkdir()
        fd = Path(feedback_dir)
        for f in ("feedback.md", "scores.json"):
            if (fd / f).exists(): shutil.copy(fd / f, rv / f)
        if (fd / "frames").is_dir(): shutil.copytree(fd / "frames", rv / "frames")
        (w / "deliverable" / "DONE").unlink(missing_ok=True)
        (w / "lessons.md").unlink(missing_ok=True); mem.round_file(d, round_k).unlink(missing_ok=True)
        if memory:
            try: seed_memory(d, et)
            except Exception as e: log(f"{et.id}: memory not seeded from the construction run ({type(e).__name__}: {e})")
            try: mrec = mem.before_round(d, et, LEVEL, round_k, feedback_dir, w)
            except Exception as e:
                log(f"{et.id}: lessons not retrieved ({type(e).__name__}: {e})")
                wjson(mem.round_file(d, round_k), {"round": round_k, "retrieval": {"ids": [], "error": f"{type(e).__name__}: {str(e)[:300]}"}})
    left = resume["left"] if resume else minutes; used0 = resume["used_min"] if resume else 0.0
    inst = f"dev-{clean_name(d.parent.parent.name)}-{LEVEL}"[:48] + "-" + __import__("hashlib").sha1(str(Path(d).resolve()).encode()).hexdigest()[:8]
    deadline = time.time() + left * 60
    ad.provision(inst)
    write_ctx(d / "tools" / "ctx.json", et.game, seat, inst, w, deadline, mode="dev")
    T = tools(w, d / "tools" / "ctx.json")
    A = abase.get(agent, model=model, **{k: v for k, v in opts.items() if k not in ("in_container", "brief", "edit_targets")})
    if opts.get("in_container"):
        from ..agent.incontainer import wrap
        A = wrap(A, seat, d, max_usd=config.get("refine_max_usd"), spent_usd=((resume or {}).get("prev") or {}).get("usd") or 0.0)
    else:
        from ..agent.hostgen import wrap as host_gen
        A = host_gen(A, d)
    if opts.get("until_done", True):
        from ..agent.until_done import UntilDone
        A = UntilDone(A, tag=f"refine{round_k}" + ("r" if resume else ""), min_minutes=round(0.75 * minutes, 1), elapsed0=used0)
    if resume and (w / "refine_brief.md").exists(): msg0 = (w / "refine_brief.md").read_text()
    else:
        brief = (w / "brief.md").read_text() if (w / "brief.md").exists() else brief_text(et, w, minutes)
        msg0 = EDIT_REFINE_MSG.format(minutes=minutes, min_done=0.75 * minutes, work=w, out=w / "deliverable", brief=brief,
                                      lessons=LESSONS_LINE.format(work=w) if mrec and mrec.get("ids") else "")
        (w / "refine_brief.md").write_text(msg0)
    seg0 = time.time()
    wjson(d / "agent" / "refine_running.json", {"round": round_k, "minutes": minutes, "phase": "agent",
                                                "started": resume["started"] if resume else time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                "segment_started": seg0, "prev": resume["prev"] if resume else None})
    (d / "agent" / "interrupted.json").unlink(missing_ok=True)
    try: interrupt.check(f"{et.id} edit refine round before its agent started")
    except interrupt.Interrupted:
        try: ad.stop(inst)
        except Exception: pass
        raise
    log(f"{et.id}: edit refine round {round_k} " + (f"resumes ({left:.0f} of {minutes:.0f} min left)" if resume else f"starts ({minutes:.0f} min)"))
    t0 = time.time()
    try:
        if resume:
            msg = REFINE_RESUME_MSG.format(out=w / "deliverable", left=left, done_rule=_done_rule(minutes, used0))
            run = A.run(w, msg, abase.Budget(minutes=left), T, resume=resume["sid"], fresh=msg0 + "\n\n" + FRESH_NOTE.format(work=w) + "\n" + msg)
        else: run = A.run(w, msg0, abase.Budget(minutes=minutes), T)
    except Exception as e:
        run = abase.AgentRun(exit_reason="crash", wall_s=round(time.time() - t0, 1), extra={"error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-2000:]})
    finally:
        try: ad.stop(inst)
        except Exception: pass
    if run.usd is None and (run.tokens_in or run.tokens_out): run.usd = abase.price_usd(run.model or model, run.tokens_in, run.tokens_out, run.tokens_cached)
    if interrupt.requested() and run.exit_reason in ("interrupted", "crash"): run.exit_reason = "interrupted"
    if resume and resume.get("prev"): run = combine_runs(resume["prev"], run)
    if run.exit_reason == "interrupted":
        log(f"{et.id}: edit refine round {round_k} interrupted after {run.wall_s / 60:.1f} of {minutes:.0f} min"); return run
    freeze_deliverable(w / "deliverable", d / "deliverable")
    log(f"{et.id}: edit refine round {run.exit_reason} after {run.wall_s:.0f} s; deliverable re-frozen")
    return run


def record_old_traces(d, et, seat, out):
    """the checkpoint's traces that the hand-in changed or removed, replayed (recorded) on the edited mod -> out/ (None if none)"""
    from ..refine.refine import record_replays
    ch = changed_traces(d, et)
    if not ch: return None
    return record_replays(d, et, LEVEL, seat, out, traces=ch)


def edit_refine_trial(d, et, agent, model, opts, minutes, seat, critic_model=None, after=True, memory=None):
    """record + checkpoint comparison + edit review (r0), the agent continues (one round), record + review again (r1).
    agent.json refine_rounds[]. A round stopped by the operator is continued (refine.interrupted_round)."""
    from ..refine.refine import record_replays, eval_gate_warning, interrupted_round, MIN_LEFT
    from ..agent.base import AgentRun
    from ..run.trial import check_deliverable, freeze_deliverable
    from ..game import base as gbase
    from ..run import interrupt
    if memory is None:
        from ..refine.memory import enabled_default
        memory = enabled_default()
    d = Path(d); rroot = d / "refine"; a = d / "agent"
    mk = rjson(a / "refine_running.json", None)
    if mk and mk.get("phase") == "review" and mk.get("run"):
        k = int(mk["round"]); run = AgentRun(**mk["run"]); sb = rjson(rroot / f"r{k - 1}" / "critic" / "scores.json")
        return _edit_after(d, et, seat, k, minutes, run, sb, mk.get("started"), critic_model, after, agent)
    k = 1 + max([int(p.name[1:]) for p in rroot.glob("r*") if p.name[1:].isdigit()] or [0]) if rroot.exists() else 1
    before = rroot / f"r{k - 1}"
    if not (before / "critic" / "scores.json").exists():
        if not (before / "replay" / "record.json").exists():
            record_replays(d, et, LEVEL, seat, before / "replay")
            if interrupt.requested(): (before / "replay" / "record.json").unlink(missing_ok=True); interrupt.check("record")
        if not (before / "replay_ckpt" / "record.json").exists() and changed_traces(d, et):
            record_old_traces(d, et, seat, before / "replay_ckpt")
        sb = edit_critic(d, et, seat, before, before / "replay", model=critic_model, old_replay_dir=before / "replay_ckpt")
    else: sb = rjson(before / "critic" / "scores.json")
    if not (before / "critic" / "feedback.md").exists():
        raise RuntimeError(f"{d}: the edit critic wrote no feedback in {before / 'critic'}; refine not started")
    g = None
    try: g = eval_gate_warning(d, et, LEVEL, seat, before)
    except Exception as e: log(f"{et.id}: edit refine round {k}: evaluation gate check skipped ({type(e).__name__}: {e})")
    try: ckpt_compare(d, et, before, g)
    except Exception as e: log(f"{et.id}: edit refine round {k}: checkpoint comparison skipped ({type(e).__name__}: {e})")
    interrupt.check(f"{et.id} edit refine round {k}")
    ad = gbase.get(et.game, seat)
    rs = interrupted_round(d, k, minutes)
    if rs and rs["left"] < MIN_LEFT:
        run = AgentRun(**{f: v for f, v in rs["prev"].items() if f in AgentRun.__dataclass_fields__}); run.exit_reason = "timeout"
        freeze_deliverable(d / "work" / "deliverable", d / "deliverable")
    else:
        run = edit_refine_agent(d, et, agent, model, opts, minutes, seat, ad, before / "critic", round_k=k, resume=rs, memory=memory)
    started = (rs or {}).get("started") or (rjson(a / "refine_running.json", {}) or {}).get("started")
    if run.exit_reason == "interrupted":
        wjson(a / "interrupted.json", {"mode": "refine", "round": k, "session_id": (run.extra or {}).get("session_id"), "used_s": run.wall_s,
                                       "budget_min": minutes, "started": started, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "how": "signal",
                                       "run": run.to_dict()})
        return run, sb, None
    mk = rjson(a / "refine_running.json", {}) or {}
    wjson(a / "refine_running.json", {**mk, "round": k, "phase": "review", "started": started, "run": run.to_dict(), "harness": agent})
    (a / "interrupted.json").unlink(missing_ok=True)
    return _edit_after(d, et, seat, k, minutes, run, sb, started, critic_model, after, agent)


def _edit_after(d, et, seat, k, minutes, run, sb, started, critic_model, after, harness):
    from ..refine.refine import record_replays
    from ..run.trial import check_deliverable, guard_incidents
    from ..run import interrupt
    from ..refine import memory as mem
    rroot = d / "refine"; sa = None
    if after:
        aft = rroot / f"r{k}"
        if not (aft / "replay" / "record.json").exists():
            record_replays(d, et, LEVEL, seat, aft / "replay")
            if interrupt.requested(): (aft / "replay" / "record.json").unlink(missing_ok=True); interrupt.check("record")
        if not (aft / "replay_ckpt" / "record.json").exists() and changed_traces(d, et):
            record_old_traces(d, et, seat, aft / "replay_ckpt")
        sa = edit_critic(d, et, seat, aft, aft / "replay", model=critic_model, old_replay_dir=aft / "replay_ckpt")
    mrec = None
    if (rjson(mem.round_file(d, k), None) or {}).get("retrieval") is not None:
        try: mem.after_round(d, et, LEVEL, k, run, rroot / f"r{k - 1}" / "critic", (rroot / f"r{k}" / "critic") if after else None, started)
        except Exception as e: log(f"{et.id}: edit refine round {k}: lesson extraction failed ({type(e).__name__}: {e})")
        mrec = rjson(mem.round_file(d, k), None)
    cur = rjson(d / "agent" / "agent.json", {}) or {}
    st = started or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - (run.wall_s or 0)))
    g = guard_incidents(run.extra, d / "agent" / "llm_proxy.log")
    cur.setdefault("refine_rounds", []).append({"round": k, "mode": "edit", "started": st, "minutes": minutes, "scores_before": sb,
                                                "scores_after": sa, "check": check_deliverable(d / "deliverable"), **run.to_dict(),
                                                **({"infra": infra.assess(g, run.exit_reason)} if g else {}),
                                                **({"memory": {x: mrec.get(x) for x in ("retrieval", "extraction")}} if mrec else {}),
                                                "harness": harness})
    wjson(d / "agent" / "agent.json", cur)
    (d / "agent" / "refine_running.json").unlink(missing_ok=True)
    return run, sb, sa



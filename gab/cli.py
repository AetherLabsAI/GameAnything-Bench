"""`gab`: GameAnything-Bench. `gab bench` runs the whole benchmark for one agent configuration; the step commands below do one
part each. Every step is resumable (a finished trial / evaluation is skipped unless --force)."""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .util import log

AGENTS = {"claude": "claude_code", "codex": "codex", "opencode": "opencode", "stub": "stub", "cmd": "cmd"}
TRACKS = ("open", "commercial", "all")


def track_of(meta):
    """a task.yaml's track -> open | commercial"""
    return "commercial" if str((meta or {}).get("track") or "") == "commercial" else "open"


def _tasks(a):
    from .run.task import Task, all_task_ids
    ids = all_task_ids()
    if getattr(a, "task", None): ids = [i for i in ids if i in a.task.split(",")]
    out = []
    for i in ids:
        t = Task(i)
        if getattr(a, "game", None) and t.game not in a.game.split(","): continue
        if getattr(a, "category", None) and t.category not in a.category.split(","): continue
        if getattr(a, "modality", None) and not _modality_ok(t.modality, a.modality.split(",")): continue
        if getattr(a, "track", None) not in (None, "all") and track_of(t.meta) != a.track: continue
        out.append(t)
    return out


def _modality_ok(modality, wanted):
    """--modality video,text,image,hybrid: a task matches a kind it has; `hybrid` matches tasks with several kinds"""
    return any(m == "hybrid" and "+" in modality or m in modality.split("+") for m in wanted)


def _edits(a):
    """the editing tasks selected by --edit / --game / --track (all 80 without filters)"""
    from .edit.edit import select
    sp = lambda x: [s for s in (x or "").split(",") if s] or None
    return select(ids=sp(getattr(a, "edit", None)), games=sp(getattr(a, "game", None)), track=getattr(a, "track", None))


def _levels(a):
    """one instruction level (fine)"""
    lv = getattr(a, "level", "fine") or "fine"
    if lv != "fine": sys.exit(f"--level {lv}: the benchmark has the fine instruction only")
    return ["fine"]


def _opts(a):
    """the agent options of a command line -> the opts dict of the adapters"""
    opts = {k: getattr(a, k) for k in ("base_url", "api_key_env", "auth", "reasoning_effort", "agent_cmd") if getattr(a, k, None)}
    if getattr(a, "agent_in_container", False): opts["in_container"] = True
    return opts


def cmd_setup(a):
    p = config.REPO / "gab.local.json"
    cur = json.loads(p.read_text()) if p.exists() else {}
    for k, v in (("runs_dir", a.data), ("hidden_dir", a.hidden)):
        if v: cur[k] = v
    for k, v in (a.set or []):
        if k not in config.DEFAULTS: sys.exit(f"unknown config key {k!r}")
        cur[k] = v
    if any((a.data, a.hidden, a.set)): p.write_text(json.dumps(cur, indent=1) + "\n")
    for k in ("tasks_dir", "rubrics_dir", "edit_dir", "media_dir", "edit_store", "targets_dir", "games_dir", "runs_dir", "hf_dataset",
              "judge_model", "claude", "critic_model", "critic_base_url", "image_base_url", "image_model"):
        v = config.get(k); ok = Path(v).exists() if k.endswith(("_dir", "_store")) else True
        print(f"  {k:16} {v}{'' if ok else '   (missing)'}")
    from .host.docker import Backend, image_ref, manifest
    b = Backend()
    if a.pull:
        for g in (sorted(manifest().get("games") or {}) if a.pull == "all" else a.pull.split(",")):
            print(f"  pull {g}: {image_ref(g)}"); b.pull(image_ref(g))
    for k in ("docker", "docker_gpus", "docker_network", "docker_pull", "docker_tag", "docker_agent_uid"):
        print(f"  {k:16} {config.get(k)!r}")
    for name, ok, detail in b.doctor(a.game.split(",") if a.game else None):
        print(f"  {'ok ' if ok else '-- '} {name:22} {detail}")
    from .run.preflight import judge_checks
    for name, ok, detail, _ in judge_checks():
        print(f"  {'ok ' if ok else '-- '} {name:22} {detail}")
    for name, ok, detail in disk_checks(b):
        print(f"  {'ok ' if ok else '-- '} {name:22} {detail}")


DISK_WARN_GB = 50


def free_gb(path):
    """free space (GB) of the file system holding path (its nearest existing parent), or None"""
    import shutil
    p = Path(path)
    while not p.exists() and p != p.parent: p = p.parent
    try: return shutil.disk_usage(p).free / 1e9
    except OSError: return None


def disk_checks(backend=None):
    """[(name, ok, detail)]: free disk of the data dirs (media, checkpoints, runs) and of docker's data root; a warning below
    DISK_WARN_GB"""
    out, seen = [], set()
    for k in ("media_dir", "edit_store", "runs_dir"):
        p = config.path(k); g = free_gb(p)
        dev = None
        try:
            q = p
            while not q.exists() and q != q.parent: q = q.parent
            dev = q.stat().st_dev
        except OSError: pass
        if g is None or dev in seen: continue
        seen.add(dev)
        out.append((f"disk {k}", g >= DISK_WARN_GB, f"{g:.0f} GB free at {p}" + ("" if g >= DISK_WARN_GB else
                    f"   (below {DISK_WARN_GB} GB: media, checkpoints and runs need tens of GB)")))
    try:
        r = (backend._run(["info", "--format", "{{.DockerRootDir}}"], timeout=30) if backend else None)
        root = (r.stdout or "").strip() if r is not None and r.returncode == 0 else ""
        g = free_gb(root) if root else None
        if g is not None:
            out.append(("disk docker root", g >= DISK_WARN_GB, f"{g:.0f} GB free at {root}" + ("" if g >= DISK_WARN_GB else
                        f"   (below {DISK_WARN_GB} GB: the game images need about 25-85 GB)")))
    except Exception: pass
    return out


def cmd_task(a):
    from .run.task import validate, all_task_ids
    bad = 0; ids = [x for x in all_task_ids() if not a.task or x in a.task.split(",")]
    for tid in ids:
        E, W = validate(tid, check_media=not a.no_media)
        bad += bool(E)
        print(f"{'FAIL' if E else 'ok  '} {tid}" + "".join(f"\n    error: {e}" for e in E) + "".join(f"\n    warn:  {w}" for w in W if a.verbose or E))
    print(f"{len(ids) - bad} ok, {bad} failed"); sys.exit(1 if bad else 0)


def cmd_media(a):
    from .setup.media import fetch
    tasks = [] if a.editing_only else _tasks(a)
    edits = [] if a.construction_only or a.task else _edits(a)
    sys.exit(0 if fetch(tasks, edits) else 1)


def cmd_list(a):
    from collections import Counter
    from .game.base import GAMES
    ts = _tasks(a); es = [] if getattr(a, "task", None) else _edits(a)
    print("games with an adapter:", ", ".join(GAMES))
    by, eby = {}, {}
    for t in ts: by.setdefault(t.game, []).append(t)
    for e in es: eby.setdefault(e.game, []).append(e)
    print(f"Construction: {len(ts)} tasks")
    for g, xs in sorted(by.items()):
        print(f"  {g:22} {len(xs):3} tasks   ({track_of(xs[0].meta)}){'' if g in GAMES else '   (no adapter)'}")
        if a.tasks:
            for t in xs: print(f"      {t.id}  {t.category:18} {t.modality}")
    print("  references:", ", ".join(f"{k} {n}" for k, n in sorted(Counter(t.modality for t in ts).items())))
    print(f"Editing: {len(es)} tasks")
    for g, xs in sorted(eby.items()):
        print(f"  {g:22} {len(xs):3} tasks   ({xs[0].track})")
        if a.tasks:
            for e in xs: print(f"      {e.id}  {e.edit_class:18} checkpoint {e.start_key()}")


def _trial_jobs(a):
    return [(t, lv) for t in _tasks(a) for lv in _levels(a)]


def _over_budget(ag):
    """a trial whose agent run or last refine round stopped at the spend cap (budget_exceeded): refine skips it; `gab run
    --force` runs it again"""
    if ((ag.get("infra") or {}).get("status") or "") == "budget_exceeded": return True
    rr = ag.get("refine_rounds") or []
    return bool(rr) and ((rr[-1].get("infra") or {}).get("status") or "") == "budget_exceeded"


RUN_SUFFIX = {"claudecode": "claude_code", "codex": "codex", "opencode": "opencode"}


def _own_agent(d, name):
    """the harness and model the trial itself ran with (round 1) -> (agent | None, model given to `gab run` | None,
    model the round-1 agent reported | None). Harness: trial.json agent, else the run name's -<harness> suffix."""
    from .util import rjson
    m = rjson(Path(d) / "trial.json", {}) or {}
    ag = rjson(Path(d) / "agent" / "agent.json", {}) or {}
    agent, given = m.get("agent"), m.get("model")
    if agent in (None, "external"):                      # no record of what `gab run` was given: the name and agent.json
        agent = next((RUN_SUFFIX[p] for p in reversed(str(name).split("-")) if p in RUN_SUFFIX), None)
        given = ag.get("model")
    return agent, given, ag.get("model")


def _extra_round_agent(a, d):
    """refine: an extra round always runs the trial's OWN harness and model. --agent / --agent-cmd / --model may be left out
    (the trial's are taken) or must name the same; anything else is refused for that trial, never silently overridden.
    -> (agent, model, None) or (None, None, error message)"""
    own, given, reported = _own_agent(d, a.name)
    want = "cmd" if getattr(a, "agent_cmd", None) else (AGENTS.get(a.agent, a.agent) if getattr(a, "agent", None) else None)
    if own is None:
        return None, None, f"{d}: the trial's own harness is unknown (no trial.json agent, no -<harness> run name suffix): refused"
    if want is not None and want != own:
        return None, None, (f"{d}: --agent {want} but the trial ran with {own}: an extra round must use the trial's own "
                            f"harness; refused (leave --agent out to use the trial's)")
    if a.model is not None and a.model != given and not (given is None and a.model == reported):
        return None, None, (f"{d}: --model {a.model} but the trial ran with {given or reported}: an extra round must use the "
                            f"trial's own model; refused (leave --model out to use the trial's)")
    return own, (a.model if a.model is not None else given), None


def cmd_run(a):
    from .run.trial import run_trial
    agent = "cmd" if a.agent_cmd else AGENTS.get(a.agent, a.agent)
    opts = _opts(a)
    jobs = _trial_jobs(a)
    from .run.trial import reference_setting, dropped_kinds
    try: ra = reference_setting(getattr(a, "no_reference", False), getattr(a, "drop_modality", None))
    except ValueError as e: sys.exit(f"gab run: {e}")
    if ra:                                               # reference ablation: the agent's brief / workspace only, eval unchanged
        opts["reference_ablation"] = ra
        if ra.startswith("drop-"):                       # only hybrid tasks that have the dropped kind
            k = ra[5:]; n0 = len(jobs)
            jobs = [j for j in jobs if "+" in j[0].modality and k in j[0].modality.split("+")]
            log(f"run {a.name}: --drop-modality {k}: {len(jobs)} of {n0} trials are hybrid tasks with a {k} reference")
        log(f"run {a.name}: reference setting {ra} (the agent is not given: {', '.join(sorted(dropped_kinds(ra)))}; "
            "the evaluation uses the full task)")
    if not a.plan and not (ra and dropped_kinds(ra) - {"text"}):   # target pictures (withheld under a video / image ablation)
        from .refine.refine import make_targets
        for t in {t.id: t for t, _ in jobs}.values(): make_targets(t)
    log(f"run {a.name}: {len(jobs)} trials, agent {agent} {a.model or ''}")
    all_games = [t.game for t, _ in jobs]
    from .run.sched import unverified_pool
    for g in sorted({t.game for t, _ in jobs}):           # games whose gate items are not verified stay out (even a single trial)
        why = None if a.allow_unverified else unverified_pool(g)
        if why:
            n = sum(t.game == g for t, _ in jobs); jobs = [j for j in jobs if j[0].game != g]
            log(f"run {a.name}: skipping {n} trial(s) of {g}: its regression pool is not verified ({why}); --allow-unverified runs them anyway")
    if not jobs and not a.plan: log(f"run {a.name}: nothing to run"); return
    if not a.no_preflight and not a.plan:                # fail fast on the environment (gab/run/preflight.py)
        from .run.preflight import preflight
        judge = not a.no_eval or bool(getattr(a, "bench", False))   # the judges run now, or in gab bench's evaluation step
        if not preflight(agent, opts, a.model, [t.game for t, _ in jobs], judge=judge): sys.exit(2)
    from .run.sched import InfraPolicy, Scheduler, confirm, plan
    p = plan(all_games, a.workers, allow_unverified=a.allow_unverified)   # parallel plan by game type; the user confirms it (or --yes)
    if len(jobs) > 1 or a.plan:
        if not confirm(p, a.minutes, yes=a.yes, only_plan=a.plan): return
    from .run import interrupt
    interrupt.install()                                  # SIGTERM / SIGINT: stop the agents, record them as interrupted, exit
    sched = Scheduler(p); policy = InfraPolicy()         # infra_error: transient -> retry, systematic -> no new trials
    runnable = {r["game"] for r in p["games"] if r["max_parallel"] > 0}
    jobs = [j for j in jobs if j[0].game in runnable] if len(jobs) > 1 else jobs
    from .run.trial import trial_dir
    from .util import rjson
    def resumable(t, lv):                                # an interrupted trial of this run: it goes first (Scheduler.slot)
        d = trial_dir(a.name, t.id, lv)
        ag = rjson(d / "agent" / "agent.json", {}) or {}
        if (ag.get("infra") or {}).get("status") == "infra_error": return True
        m = rjson(d / "trial.json", {}) or {}               # killed while its agent worked (run_trial infers it at its start)
        return bool(m.get("started")) and not any(x.get("started") == m["started"] for x in m.get("attempts") or []) \
            and (d / "agent" / "transcript.jsonl").exists() and not a.force
    firsts = {(t.id, lv) for t, lv in jobs if resumable(t, lv)}
    for t, lv in jobs:
        if (t.id, lv) in firsts: sched.reserve_first(t.game)   # before the threads start (Scheduler.reserve_first)
    def one(j):
        t, lv = j
        first = (t.id, lv) in firsts; asked = []
        def attempt(n):
            asked.append(n)
            with sched.slot(t.game, first=first and n == 1):
                if interrupt.requested(): return {"task": t.id, "level": lv, **policy.INTERRUPTED}
                if policy.stop.is_set(): return {"task": t.id, "level": lv, **policy.SKIP}
                return run_trial(a.name, t, lv, agent, a.model, seed=a.seed, opts=opts, force=a.force, minutes=a.minutes,
                                 skip_eval=a.no_eval, attempt=n)
        try: return {"task": t.id, "level": lv, **(policy.run(f"{t.id}/{lv}", attempt) or {})}
        except Exception as e:
            import traceback; traceback.print_exc(); return {"task": t.id, "level": lv, "error": str(e)}
        finally:
            if first and not asked: sched.unreserve_first(t.game)
    with ThreadPoolExecutor(max(len(jobs), 1)) as ex: res = list(ex.map(one, jobs))
    for r in res:
        print(json.dumps({**{k: r.get(k) for k in ("task", "level", "status", "scores", "error") if k in r},
                          **({"infra": (r.get("infra") or {}).get("primary")} if r.get("status") == "infra_error" else {})}))
    if interrupt.requested():
        n = sum(bool(r.get("interrupted")) for r in res)
        print(f"\nstopped by the operator: {n} running agent(s) recorded as interrupted; re-run the same command to continue them "
              "in their sessions with the time they had left", file=sys.stderr); sys.exit(143)
    b = policy.banner()
    if b: print(b, file=sys.stderr); sys.exit(2)
    if any(r.get("status") == "infra_error" for r in res):
        print(f"\n{sum(r.get('status') == 'infra_error' for r in res)} trials ended in infra_error (transient, retries used up): "
              f"re-run the same command to run them again", file=sys.stderr); sys.exit(3)


def _refused_exit(refused):
    """trials refused by _extra_round_agent: listed, and the command exits non-zero"""
    if not refused: return
    for r in refused: print(json.dumps(r))
    print(f"\n{len(refused)} trial(s) refused: an extra round must use the trial's own harness and model (leave --agent / "
          "--model out, or give the trial's)", file=sys.stderr)
    sys.exit(2)


def _ablation_base(d):
    """the source trial's base hand-in (0 refine rounds) for gab refine --ablation, when its agent run finished (not an
    infra_error / interrupted / over-budget run) -> Path or None"""
    from .refine.refine import snapshot
    from .util import rjson
    b = snapshot(d, 0)
    if b is None: return None
    ag = rjson(b.parent / "agent" / "agent.json", {}) or {}
    if (ag.get("infra") or {}).get("status") == "infra_error" or ag.get("exit_reason") in (None, "interrupted") or _over_budget(ag): return None
    return b


def cmd_refine(a):
    """refine rounds on finished trials of --name (gab/refine/refine.py): record the hand-in's replays, an independent reviewer agent
    compares them with the reference and the target pictures, the trial's own agent improves its work for --minutes, then the
    after-state is recorded and reviewed again; the experience memory gives lessons before and extracts new ones after"""
    from .run.trial import trial_dir, clean_name
    from .util import rjson
    from .host import base as hbase
    from .run.sched import plan, confirm, Scheduler
    from .refine.refine import refine_trial, make_targets, missing_parts, archive_last_review, ablation_run_name, ablation_round
    opts = _opts(a)
    abl = "nocritic" if getattr(a, "no_critic", False) else getattr(a, "ablation", None)
    if getattr(a, "no_critic", False) and getattr(a, "ablation", None) not in (None, "nocritic"): sys.exit("gab refine: --no-critic is --ablation nocritic")
    into = None
    if abl:                                              # one round from the source's R0 in a separate run (gab/refine/refine.py section 6)
        into = getattr(a, "into", None) or ablation_run_name(a.name, abl)
        if into == a.name: sys.exit("gab refine --ablation: --into must name another run (the source trial is never changed)")
        if a.review_only or getattr(a, "rereview_last", False) or getattr(a, "only_missing", False) or a.force or a.rounds != 1:
            sys.exit("gab refine --ablation: one round per trial; not with --review-only / --rereview-last / --only-missing / --force / --rounds")
        if abl == "full" and getattr(a, "no_memory", False): sys.exit("gab refine --ablation full: the setting has the memory; not with --no-memory")
        log(f"refine {a.name}: ablation {abl} -> run {into} (from each trial's base hand-in R0)")
    todo = []; own = {}; refused = []
    for t, lv in _trial_jobs(a):
        d = trial_dir(a.name, t.id, lv)
        if abl:                                          # the source's own rounds do not matter: the new run starts from its R0
            new = trial_dir(into, t.id, lv)
            if len((rjson(new / "agent" / "agent.json", {}) or {}).get("refine_rounds") or []) >= 1: continue   # done
            if not (new / "trial.json").exists() and _ablation_base(d) is None:
                log(f"refine {a.name}: {t.id}/{lv}: no finished base hand-in (R0 snapshot) to start {into} from"); continue
            agent, model, err = _extra_round_agent(a, d)
            if err: refused.append({"task": t.id, "level": lv, "status": "refused", "error": err}); log(f"refine {a.name}: {err}"); continue
            own[(t.id, lv)] = (agent, model); todo.append((t, lv)); continue
        if not (d / "work" / "deliverable").is_dir(): continue
        ag = rjson(d / "agent" / "agent.json", {}) or {}
        if (ag.get("infra") or {}).get("status") == "infra_error" or ag.get("exit_reason") in (None, "interrupted"): continue
        if _over_budget(ag): continue
        if a.review_only:
            if (d / "refine").is_dir() or getattr(a, "rereview_last", False): todo.append((t, lv))
            continue
        if len(ag.get("refine_rounds") or []) >= a.rounds and not a.force: continue
        if getattr(a, "only_missing", False) and missing_parts(d) == []: continue      # the newest review found every part (None = unknown: kept)
        agent, model, err = _extra_round_agent(a, d)
        if err: refused.append({"task": t.id, "level": lv, "status": "refused", "error": err}); log(f"refine {a.name}: {err}"); continue
        own[(t.id, lv)] = (agent, model)
        todo.append((t, lv))
    hs = sorted({f"{ag_} {m or ''}".strip() for ag_, m in own.values()})
    from .refine.memory import enabled_default, root as mem_root
    from .refine.refine import ABLATIONS
    mem_on = ABLATIONS[abl]["memory"] if abl else (not getattr(a, "no_memory", False) and enabled_default())
    log(f"refine {a.name}" + (f" -> {into} ({abl})" if abl else "") + f": {len(todo)} trials, the trial's own agent ({', '.join(hs)}), {a.minutes:.0f} min, "
        f"critic {a.critic_model or config.get('critic_model')}, memory " + ((f"on ({mem_root(into)}, a frozen copy of {mem_root(a.name)}, own task left out)" if abl
                                                          else f"on ({mem_root(a.name)})") if mem_on else "off") + (f"; {len(refused)} refused (another harness / model than the trial's)" if refused else ""))
    if not todo:
        _refused_exit(refused); return
    for t in {t.id: t for t, _ in todo}.values(): make_targets(t)          # shared, once per task
    p = plan([t.game for t, _ in todo], a.workers, allow_unverified=True)
    if len(todo) > 1 or a.plan:
        if not confirm(p, a.minutes + 30, yes=a.yes, only_plan=a.plan): return
    from .run import interrupt
    interrupt.install()                                  # SIGTERM / SIGINT: the running rounds are recorded and continued next time
    sched = Scheduler(p); hb = hbase.get(None)
    def one(j):
        t, lv = j
        d = trial_dir(a.name, t.id, lv); src = d
        if abl: d = trial_dir(into, t.id, lv)            # the new trial (its seat mounts it; the source is only read)
        from .agent.incontainer import mounts             # the reviewer runs in the seat container: the trial is mounted
        req = {"game": t.game, "mounts": mounts(d)}
        with sched.slot(t.game):
            if interrupt.requested(): return {"task": t.id, "level": lv, "status": "interrupted", "error": "not started"}
            seat = hb.acquire(req, f"gab-refine-{clean_name(a.name)}-{t.id[-14:]}-{lv[0]}")
            try:
                if getattr(a, "rereview_last", False): archive_last_review(d)  # the newest review is redone (old one archived)
                if a.review_only:
                    from .refine.refine import review_rounds
                    return {"task": t.id, "level": lv, "status": "reviewed", "scores": review_rounds(d, t, lv, seat, a.critic_model)}
                agent, model = own[(t.id, lv)]
                if abl:
                    run, sb, sa = ablation_round(src, d, t, lv, agent, model, opts, a.minutes, seat, into, abl, critic_model=a.critic_model)
                    return {"task": t.id, "level": lv, "status": "interrupted" if run.exit_reason == "interrupted" else "refined",
                            "setting": abl, "trial": str(d), "exit_reason": run.exit_reason, "usd": run.usd, "scores_before": sb, "scores_after": sa}
                run, sb, sa = refine_trial(d, t, lv, agent, model, opts, a.minutes, seat, critic_model=a.critic_model,
                                           memory=False if getattr(a, "no_memory", False) else None)
                return {"task": t.id, "level": lv, "status": "interrupted" if run.exit_reason == "interrupted" else "refined",
                        "exit_reason": run.exit_reason, "usd": run.usd, "scores_before": sb, "scores_after": sa}
            except interrupt.Interrupted as e:
                return {"task": t.id, "level": lv, "status": "interrupted", "error": str(e)}
            except Exception as e:
                import traceback; traceback.print_exc()
                return {"task": t.id, "level": lv, "status": "error", "error": str(e)}
            finally:
                seat.release()
    with ThreadPoolExecutor(max(len(todo), 1)) as ex: res = list(ex.map(one, todo))
    for r in res: print(json.dumps(r))
    if interrupt.requested():
        print(f"\nstopped by the operator: {sum(r.get('exit_reason') == 'interrupted' for r in res)} refine round(s) recorded as "
              "interrupted; re-run the same command to continue them in their sessions with the time left", file=sys.stderr)
        sys.exit(143)
    _refused_exit(refused)


def cmd_memory(a):
    """the experience memory of one configuration (gab/refine/memory.py): its lessons, or counts"""
    from .refine import memory
    if a.action == "show": print(memory.show(a.name, a.game))
    else: print(json.dumps(memory.stats(a.name), ensure_ascii=False, indent=1))


def eval_tag_for(lite=False, rnd=0):
    """the result tag of an evaluation: Full of the base hand-in -> None (result.json); Lite -> "lite"; after K refine rounds
    -> "r<K>" / "lite-r<K>" """
    rnd = int(rnd or 0)
    if not rnd: return "lite" if lite else None
    return f"lite-r{rnd}" if lite else f"r{rnd}"


def _eval_jobs(a, deliverables=None):
    """-> [(run name, task, level)]: --from-file (lines `run/task` or `run/task/level`, optionally followed by an alternate
    deliverable dir inside the trial, e.g. `run/task attempts/2/deliverable`; # comments) or --name + the filters.
    deliverables (a dict): filled with {(run, task id, level): dir} for the lines that name one"""
    from .run.task import Task
    if not getattr(a, "from_file", None):
        if not a.name: sys.exit("gab eval: --name or --from-file")
        return [(a.name, t, lv) for t, lv in _trial_jobs(a)]
    out = []
    for ln in Path(a.from_file).read_text().splitlines():
        ln = ln.split("#")[0].strip()
        if not ln: continue
        f = ln.split()
        if len(f) > 2: sys.exit(f"{a.from_file}: {ln!r}: expected run/task[/level] [deliverable dir]")
        p = f[0].strip("/").split("/")
        if len(p) not in (2, 3): sys.exit(f"{a.from_file}: {ln!r}: expected run/task or run/task/level")
        out.append((p[0], Task(p[1]), p[2] if len(p) == 3 else "fine"))
        if len(f) == 2 and deliverables is not None: deliverables[(p[0], p[1], out[-1][2])] = f[1]
    return out


def eval_mode(a):
    """--lite / --evidence of a command line -> "full" | "lite" (default full)"""
    m = getattr(a, "evidence", None) or ("lite" if getattr(a, "lite", False) else None) or "full"
    if getattr(a, "lite", False) and m != "lite": sys.exit("--lite and --evidence full exclude each other")
    return m


def cmd_eval(a):
    from .run.trial import eval_trial, import_deliverables
    mode = eval_mode(a); rnd = int(getattr(a, "round", 0) or 0)
    if a.tag is None: a.tag = eval_tag_for(mode == "lite", rnd)
    os.environ["GAB_EVIDENCE"] = mode                                                # the knobs reach every worker thread via config
    for k, env in (("play_actions", "GAB_PLAY_ACTIONS"), ("play_minutes", "GAB_PLAY_MINUTES"), ("tag", "GAB_EVAL_TAG")):
        if getattr(a, k, None) is not None: os.environ[env] = str(getattr(a, k))
    if getattr(a, "no_host_fidelity", False): os.environ["GAB_HOST_FIDELITY_IN_EVAL"] = "0"
    from .eval.run import eval_tag
    from .eval.functions import evidence_mode, play_budget
    tag = eval_tag(); evidence_mode(); play_budget()                                 # validate before anything starts
    if a.deliverables: import_deliverables(a.name, Path(a.deliverables), _trial_jobs(a))
    alt = {}; jobs = _eval_jobs(a, alt)
    if alt and not tag: sys.exit("gab eval: a deliverable dir in --from-file needs --tag (never overwrites the main result)")
    log(f"eval: {len(jobs)} trial(s), {mode} evaluation" + (f", after {rnd} refine round(s)" if rnd else "") + f", result {('result@' + tag) if tag else 'result'}.json")
    def one(name, t, lv, sched=None):
        d = config.path("runs_dir") / name / t.id / lv
        try:
            rel = alt.get((name, t.id, lv))
            if rnd:                                       # the hand-in after K refine rounds (gab/refine/refine.py snapshot)
                from .refine.refine import snapshot
                s = snapshot(d, rnd)
                if s is None: log(f"{name}/{t.id}/{lv}: no hand-in after {rnd} refine round(s)"); return
                rel = str(s.relative_to(d))
            kw = {"deliverable": rel} if rel else {}
            from .eval.run import deliverable_dir
            if not deliverable_dir(d, rel, tag)[0].is_dir(): log(f"{name}/{t.id}/{lv}: no {rel or 'deliverable'}/"); return
            if sched is None: return eval_trial(d, t, lv, force=a.force, tag=tag, **kw)
            with sched.slot(t.game): return eval_trial(d, t, lv, force=a.force, tag=tag, **kw)
        except Exception as x:
            import traceback; traceback.print_exc(); log(f"{name}/{t.id}/{lv}: {type(x).__name__}: {x}")
    W = a.workers or 1
    if W <= 1 or len(jobs) <= 1:
        for j in jobs: one(*j)
        return
    from .run.sched import Scheduler, plan                    # game evaluations: the scheduler's per-game / RAM / core / GPU limits
    p = plan([t.game for _, t, _ in jobs], W, allow_unverified=True); sched = Scheduler(p)
    log(f"eval: {len(jobs)} trials, at most {p['workers']} at once; per game: " + ", ".join(f"{r['game']} {r['max_parallel']}" for r in p["games"]))
    with ThreadPoolExecutor(W) as ex: list(ex.map(lambda j: one(*j, sched=sched), jobs))


def cmd_check(a):
    from .run.trial import self_check
    print(json.dumps(self_check(Path(a.trial)), indent=1))


def _score_name(a):
    """--name (+ --lite / --round) -> run@tag as the report reads it"""
    if "@" in a.name: return a.name
    t = eval_tag_for(getattr(a, "lite", False), getattr(a, "round", 0))
    return f"{a.name}@{t}" if t else a.name


def cmd_score(a):
    from .report.aggregate import score
    s = score(_score_name(a), edit_round=getattr(a, "round", 0) or 0)
    print(json.dumps({k: v for k, v in s.items() if k != "cells"} | {"all": s["cells"]["all"]}, indent=1)[:6000])


def cmd_table(a):
    from .report.aggregate import table
    print(table(a.names.split(","), a.table))


def cmd_selfcheck(a):
    from .setup.selfcheck import selfcheck
    sys.exit(0 if selfcheck(a.game, quick=a.quick, state_only=a.state) else 1)


def cmd_game(a):
    from .setup.selfcheck import game_cmd
    game_cmd(a.action, a.game)


def cmd_bench(a):
    from .run.bench import bench
    sys.exit(bench(a))


def construction_filters(p):
    p.add_argument("--task", help="task ids (comma list)"); p.add_argument("--game", help="game ids (comma list)")
    p.add_argument("--category"); p.add_argument("--level", default="fine", help=argparse.SUPPRESS)
    p.add_argument("--modality", help="video, text, image, hybrid (comma list; a hybrid task matches each of its kinds)")
    p.add_argument("--track", choices=TRACKS, default="all", help="open-source games, commercial games, or all (default)")


def agent_flags(s, default_agent="claude"):
    s.add_argument("--agent", default=default_agent, choices=sorted(AGENTS) if default_agent else None,
                   help="claude (Claude Code), codex (Codex CLI), opencode (OpenCode: any OpenAI-compatible endpoint), stub "
                        "(pipeline self-check, no model); --agent-cmd for any other agent" + ("" if default_agent else
                        "; default: the trial's own harness (another one is refused)"))
    s.add_argument("--agent-cmd", help="your own agent: a shell command with {brief} {refs} {clips} {out} {task} {game} {level} {model} "
                                       "placeholders (docs/adding_an_agent.md)")
    s.add_argument("--model", help="the model id given to the harness" + ("" if default_agent else " (default: the trial's own)"))
    s.add_argument("--base-url", help="OpenAI-compatible endpoint (opencode, codex) or the key proxy's upstream (--agent-in-container)")
    s.add_argument("--api-key-env", help="NAME of the environment variable that holds the API key (its value is never printed)")
    s.add_argument("--reasoning-effort")
    s.add_argument("--auth", choices=["subscription", "api"], help="--agent claude --agent-in-container: subscription (default; "
                   "CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`) or api (an Anthropic key in --api-key-env)")
    s.add_argument("--agent-in-container", action="store_true", help="run the agent harness inside the trial container (model "
                   "calls through the host key proxy: --base-url + --api-key-env)")


def batch_flags(s, workers_default=None):
    s.add_argument("--workers", type=int, default=workers_default, help="at most N trials at once (default: the plan's suggestion by game type)")
    s.add_argument("--plan", action="store_true", help="print the parallel plan and exit")
    s.add_argument("--yes", action="store_true", help="start without confirming the plan")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gab", description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    s = sp.add_parser("bench", help="the whole benchmark for one agent: Construction + Editing, evaluation, score, report")
    s.add_argument("--name", required=True, help="run name: results go to <runs_dir>/NAME/ (report: NAME/report/)")
    agent_flags(s)
    s.add_argument("--track", choices=TRACKS, default="all", help="open-source games, commercial games, or all (default)")
    s.add_argument("--lite", action="store_true", help="Lite evaluation (replay verifier only) instead of Full")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--construction-only", action="store_true"); g.add_argument("--editing-only", action="store_true")
    s.add_argument("--minutes", type=float, default=120.0, help="Construction agent budget per task (default 120, the paper's)")
    s.add_argument("--edit-minutes", type=float, default=60.0, help="Editing agent budget per task (default 60, the paper's)")
    s.add_argument("--game", help="only these games (comma list)")
    s.add_argument("--task", help="only these Construction tasks (comma list; with --edit, a quick test)")
    s.add_argument("--edit", help="only these Editing tasks (comma list)")
    s.add_argument("--eval-workers", type=int, default=None, help="evaluations at once (default: --workers, else 4)")
    s.add_argument("--no-preflight", action="store_true"); s.add_argument("--force", action="store_true", help="redo finished steps")
    batch_flags(s); s.set_defaults(f=cmd_bench)

    s = sp.add_parser("setup", help="show / set the configuration (gab.local.json) and check docker")
    s.add_argument("--data", help="runs_dir"); s.add_argument("--hidden", help="hidden_dir (rubrics of your own private tasks)")
    s.add_argument("--set", nargs=2, action="append", metavar=("KEY", "VALUE"), help="any config key, e.g. --set docker_gpus none")
    s.add_argument("--pull", help="pull the docker images of these games (comma list or all)"); s.add_argument("--game")
    s.set_defaults(f=cmd_setup)
    s = sp.add_parser("get-game", help="set games up on this machine in one command: image, game files, config, start check")
    s.add_argument("games", help="comma list, or all / open / commercial"); s.add_argument("--no-check", action="store_true")
    s.set_defaults(f=lambda a: __import__("gab.setup.getgame", fromlist=["get_game"]).get_game(a.games, do_check=not a.no_check))
    s = sp.add_parser("media", help="download reference media, editing start checkpoints and target pictures (sha256 checked)")
    s.add_argument("action", choices=["fetch"]); construction_filters(s)
    s.add_argument("--edit", help="editing task ids (comma list)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--construction-only", action="store_true"); g.add_argument("--editing-only", action="store_true")
    s.set_defaults(f=cmd_media)
    s = sp.add_parser("list", help="games and tasks (Construction and Editing)")
    s.add_argument("--tasks", action="store_true", help="list every task"); construction_filters(s); s.add_argument("--edit")
    s.set_defaults(f=cmd_list)
    s = sp.add_parser("task", help="check task packages against the schemas (for new tasks)")
    s.add_argument("action", choices=["validate"]); s.add_argument("--task")
    s.add_argument("--no-media", action="store_true"); s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(f=cmd_task)

    s = sp.add_parser("run", help="Construction: one autonomous agent attempt per task (then evaluated unless --no-eval)")
    construction_filters(s); s.add_argument("--name", required=True); agent_flags(s)
    s.add_argument("--seed", type=int, default=0, help=argparse.SUPPRESS)
    batch_flags(s)
    s.add_argument("--minutes", type=float, default=120.0, help="agent budget per task (default 120)")
    s.add_argument("--force", action="store_true", help="start finished trials over")
    s.add_argument("--no-eval", action="store_true", help="agent only; evaluate later with `gab eval`")
    s.add_argument("--no-reference", action="store_true", help="paper ablation: the agent gets the instruction only (no reference "
                   "files, no target pictures); the evaluation is unchanged. Use a distinct --name, e.g. <cfg>-noref")
    s.add_argument("--drop-modality", choices=("video", "image", "text"), help="paper ablation on hybrid tasks: withhold this kind "
                   "of reference from the agent (video / image also withhold the target pictures, which are drawn from them); only "
                   "hybrid tasks with that kind run; the evaluation is unchanged. Distinct --name, e.g. <cfg>-novideo")
    s.add_argument("--allow-unverified", action="store_true", help="also run games whose gate items (pool.yaml `gate:`) are "
                   "missing or not verified (default: they are skipped)")
    s.add_argument("--no-preflight", action="store_true", help="skip the environment checks before the first trial (gab/run/preflight.py)")
    s.set_defaults(f=cmd_run)

    s = sp.add_parser("refine", help="+Ours: one refinement round on finished trials of --name (critic review, revision, "
                      "experience memory)")
    construction_filters(s); s.add_argument("--name", required=True); agent_flags(s, default_agent=None)
    s.add_argument("--minutes", type=float, default=60.0, help="the revision's budget per trial (default 60)")
    s.add_argument("--rounds", type=int, default=1, help="refine trials that have fewer than this many rounds (default 1: "
                   "R1; --rounds 2 then gives R2, ...)")
    s.add_argument("--critic-model", default=None, help="the reviewer's model (config critic_model)")
    s.add_argument("--review-only", action="store_true", help="only (re-)review refine rounds whose review is missing or incomplete")
    s.add_argument("--rereview-last", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--only-missing", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--no-memory", action="store_true", help="without the experience memory (on by default; one memory per --name)")
    s.add_argument("--ablation", choices=("nomem", "nocritic", "full"), help="paper ablation: one refine round under another setting, "
                   "from the SAME base hand-in (R0), in a NEW run (default <name>-<ablation>; the source is only read): nomem = "
                   "critic, no memory; nocritic = no review, no memory, the same budget; full = critic + a frozen copy of the "
                   "source run's memory (the current task's own lessons left out)")
    s.add_argument("--no-critic", action="store_true", help="= --ablation nocritic")
    s.add_argument("--into", help="with --ablation: the new run name")
    s.add_argument("--force", action="store_true"); batch_flags(s)
    s.set_defaults(f=cmd_refine)
    s = sp.add_parser("memory", help="the experience memory of a run's refine rounds: show its lessons, or stats")
    s.add_argument("action", choices=["show", "stats"]); s.add_argument("--name", required=True); s.add_argument("--game")
    s.set_defaults(f=cmd_memory)

    s = sp.add_parser("eval", help="evaluate Construction trials (Full by default; --lite: replay verifier only)")
    construction_filters(s); s.add_argument("--name"); s.add_argument("--deliverables", help="evaluate hand-ins made elsewhere: "
                                                                                          "DIR/<task>/fine/deliverable/")
    s.add_argument("--lite", action="store_true", help="Lite evaluation: replay verifier only (result@lite.json)")
    s.add_argument("--evidence", choices=("full", "lite"), help="the same as --lite / the default, spelled out")
    s.add_argument("--round", type=int, default=0, help="evaluate the hand-in after K refine rounds (result@r<K>.json)")
    s.add_argument("--from-file", help="trials to evaluate, one `run/task` per line, instead of --name + filters")
    s.add_argument("--workers", type=int, default=1, help="evaluate this many trials at once (within the scheduler's limits)")
    s.add_argument("--tag", help="write eval@TAG/ + result@TAG.json (default: none for Full, lite for Lite, r<K> / lite-r<K> "
                   "with --round)")
    s.add_argument("--play-actions", type=int, help="play verifier action budget (default 40)")
    s.add_argument("--play-minutes", type=float, help="play verifier minutes (default 30)")
    s.add_argument("--no-host-fidelity", action="store_true", help="skip the host-fidelity judge (then P is missing and Overall = "
                   "the mean of CR / FP / VP)")
    s.add_argument("--force", action="store_true"); s.set_defaults(f=cmd_eval)
    s = sp.add_parser("check", help="what is missing in a hand-in"); s.add_argument("--trial", required=True); s.set_defaults(f=cmd_check)
    s = sp.add_parser("score", help="aggregate a run: <runs_dir>/NAME/report/ (summary.json, tasks.csv, main_table.md, ...)")
    s.add_argument("--name", required=True, help="a run, or run@TAG for its tagged evaluations")
    s.add_argument("--lite", action="store_true", help="the Lite evaluations (result@lite.json)")
    s.add_argument("--round", type=int, default=0, help="the evaluations after K refine rounds")
    s.set_defaults(f=cmd_score)
    s = sp.add_parser("table", help="a table over several runs (markdown; CSV + LaTeX in <runs_dir>/_tables/)")
    s.add_argument("--names", required=True, help="comma list of runs; run@TAG = the tagged evaluations of a run")
    s.add_argument("--table", default="main", choices=["main", "modality", "cost"])
    s.set_defaults(f=cmd_table)
    s = sp.add_parser("selfcheck", help="game self-check: A/A of the probes, negative mods, a start without dialogs")
    s.add_argument("game"); s.add_argument("--quick", action="store_true")
    s.add_argument("--state", action="store_true", help="only the game-state check (docs/STATE_SPEC.md)"); s.set_defaults(f=cmd_selfcheck)
    s = sp.add_parser("game", help="one game: provision, start check, vanilla baseline")
    s.add_argument("action", choices=["provision", "check", "baseline"]); s.add_argument("game"); s.set_defaults(f=cmd_game)
    __import__("gab.setup.steamcli", fromlist=["add_parser"]).add_parser(sp)
    __import__("gab.edit.cli", fromlist=["add_parser"]).add_parser(sp)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()

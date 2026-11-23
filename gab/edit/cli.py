"""Editing commands (gab/edit/edit.py, gab/eval/edit.py):

  gab edit --name RUN [--edit E[,E2] | --game G | --track T] [agent flags] [--minutes 60]
                                     the edit round: <runs_dir>/RUN/<edit>/edit/ (agent only)
  gab edit-refine --name RUN ...     +Ours: one refinement round (record, edit critic, gate + checkpoint comparison, the trial's
                                     own agent continues; experience memory on)
  gab eval-edit --name RUN ... [--round K]
                                     evaluate (result@edit.json; after K refine rounds result@edit-r<K>.json)
Selection: --edit (comma list), --game (game ids), --class AP,CS,BF, --parent, --track; without any: every edit task (80).
The start checkpoints come with `gab media fetch`."""
import json, sys
from concurrent.futures import ThreadPoolExecutor

from ..util import log, rjson


def _sel(a):
    from .edit import select
    sp = lambda x: [s for s in (x or "").split(",") if s] or None
    return select(ids=sp(getattr(a, "edit", None)), games=sp(getattr(a, "game", None)), classes=sp(getattr(a, "cls", None)),
                  parents=sp(getattr(a, "parent", None)), track=getattr(a, "track", None))


def cmd_edit(a):
    from ..cli import AGENTS, _opts
    from .edit import run_edit_trial
    from ..run.sched import InfraPolicy, Scheduler, confirm, plan
    from ..run import interrupt
    agent = "cmd" if a.agent_cmd else AGENTS.get(a.agent, a.agent)
    opts = _opts(a); jobs = _sel(a)
    if not jobs: log("edit: nothing selected"); return
    if not a.plan:
        for et in jobs: et.store()                                        # checkpoints in place before any seat is taken
    log(f"edit {a.name}: {len(jobs)} edit trial(s), agent {agent} {a.model or ''}, {a.minutes:.0f} min")
    if not a.no_preflight and not a.plan:
        from ..run.preflight import preflight
        if not preflight(agent, opts, a.model, [et.game for et in jobs]): sys.exit(2)
    p = plan([et.game for et in jobs], a.workers, allow_unverified=True)
    if len(jobs) > 1 or a.plan:
        if not confirm(p, a.minutes, yes=a.yes, only_plan=a.plan): return
    interrupt.install()
    sched = Scheduler(p); policy = InfraPolicy()
    def one(et):
        def attempt(n):
            with sched.slot(et.game):
                if interrupt.requested(): return {"task": et.id, **policy.INTERRUPTED}
                if policy.stop.is_set(): return {"task": et.id, **policy.SKIP}
                return run_edit_trial(a.name, et, agent, a.model, opts=opts, force=a.force and n == 1, minutes=a.minutes, attempt=n)
        try: return policy.run(et.id, attempt) or {}
        except Exception as e:
            import traceback; traceback.print_exc(); return {"task": et.id, "status": "error", "error": str(e)}
    with ThreadPoolExecutor(max(len(jobs), 1)) as ex: res = list(ex.map(one, jobs))
    for r in res: print(json.dumps({k: r.get(k) for k in ("task", "status", "exit_reason", "error") if k in r}
                                   | ({"infra": (r.get("infra") or {}).get("primary")} if r.get("status") == "infra_error" else {})))
    if interrupt.requested(): sys.exit(143)
    b = policy.banner()
    if b: print(b, file=sys.stderr); sys.exit(2)


def cmd_refine(a):
    from ..cli import _extra_round_agent, _over_budget, _opts
    from .edit import trial_dir, edit_refine_trial
    from ..run.trial import clean_name
    from ..host import base as hbase
    from ..run.sched import plan, confirm, Scheduler
    from ..run import interrupt
    opts = _opts(a); todo = []; own = {}; refused = []
    for et in _sel(a):
        d = trial_dir(a.name, et.id)
        if not (d / "work" / "deliverable").is_dir(): continue
        ag = rjson(d / "agent" / "agent.json", {}) or {}
        if (ag.get("infra") or {}).get("status") == "infra_error" or ag.get("exit_reason") in (None, "interrupted") or _over_budget(ag): continue
        if len(ag.get("refine_rounds") or []) >= a.rounds and not a.force: continue
        agent, model, err = _extra_round_agent(a, d)
        if err: refused.append({"task": et.id, "status": "refused", "error": err}); log(f"edit-refine {a.name}: {err}"); continue
        own[et.id] = (agent, model); todo.append(et)
    log(f"edit-refine {a.name}: {len(todo)} trial(s), {a.minutes:.0f} min, critic {a.critic_model or 'default'}, memory "
        + ("off" if a.no_memory else f"on ({a.name if 'edit' in a.name.split('-') else a.name + '-edit'}, apart from the "
                                         "Construction memory)") + (f"; {len(refused)} refused" if refused else ""))
    if not todo:
        for r in refused: print(json.dumps(r))
        sys.exit(2 if refused else 0)
    p = plan([et.game for et in todo], a.workers, allow_unverified=True)
    if len(todo) > 1 or a.plan:
        if not confirm(p, a.minutes + 40, yes=a.yes, only_plan=a.plan): return
    interrupt.install()
    sched = Scheduler(p); hb = hbase.get(None)
    def one(et):
        d = trial_dir(a.name, et.id)
        from ..agent.incontainer import mounts
        req = {"game": et.game, "mounts": mounts(d)}
        with sched.slot(et.game):
            if interrupt.requested(): return {"task": et.id, "status": "interrupted", "error": "not started"}
            seat = hb.acquire(req, f"gab-erefine-{clean_name(a.name)}-{et.id[-14:]}-e")
            try:
                agent, model = own[et.id]
                run, sb, sa = edit_refine_trial(d, et, agent, model, opts, a.minutes, seat, critic_model=a.critic_model,
                                                after=not a.no_after, memory=False if a.no_memory else None)
                return {"task": et.id, "status": "interrupted" if run.exit_reason == "interrupted" else "refined",
                        "exit_reason": run.exit_reason, "usd": run.usd, "scores_before": sb, "scores_after": sa}
            except interrupt.Interrupted as e: return {"task": et.id, "status": "interrupted", "error": str(e)}
            except Exception as e:
                import traceback; traceback.print_exc(); return {"task": et.id, "status": "error", "error": str(e)}
            finally: seat.release()
    with ThreadPoolExecutor(max(len(todo), 1)) as ex: res = list(ex.map(one, todo))
    for r in res + refused: print(json.dumps(r))
    if interrupt.requested(): sys.exit(143)
    if refused: sys.exit(2)


def cmd_eval(a):
    from .edit import trial_dir
    from ..eval.edit import eval_edit, tag_of_round
    jobs = _sel(a)
    tag = a.tag or tag_of_round(a.round)
    def one(et, sched=None):
        d = trial_dir(a.name, et.id)
        if not (d / "trial.json").exists(): return
        try:
            if sched is None: return eval_edit(d, et, tag=tag, force=a.force, rnd=a.round, host_fid=not a.no_host_fidelity)
            with sched.slot(et.game): return eval_edit(d, et, tag=tag, force=a.force, rnd=a.round, host_fid=not a.no_host_fidelity)
        except Exception as x:
            import traceback; traceback.print_exc(); log(f"{et.id}: {type(x).__name__}: {x}")
    jobs = [et for et in jobs if (trial_dir(a.name, et.id) / "trial.json").exists()]
    log(f"eval-edit {a.name}: {len(jobs)} trial(s), result@{tag}.json")
    if (a.workers or 1) <= 1 or len(jobs) <= 1:
        res = [one(et) for et in jobs]
    else:
        from ..run.sched import Scheduler, plan
        sched = Scheduler(plan([et.game for et in jobs], a.workers, allow_unverified=True))
        with ThreadPoolExecutor(a.workers) as ex: res = list(ex.map(lambda et: one(et, sched), jobs))
    for et, r in zip(jobs, res):
        print(json.dumps({"task": et.id, "status": (r or {}).get("status"), "scores": (r or {}).get("scores")}))


def _filters(s):
    s.add_argument("--edit", help="edit task ids (comma list)"); s.add_argument("--game", help="game ids (comma list)")
    s.add_argument("--class", dest="cls", help="AP, CS, BF (comma list)"); s.add_argument("--parent", help="parent tasks (comma list)")
    s.add_argument("--track", choices=("open", "commercial", "all"), default="all")


def add_parser(sp):
    from ..cli import agent_flags, batch_flags
    s = sp.add_parser("edit", help="Editing: one agent attempt per edit task on its start checkpoint")
    _filters(s); s.add_argument("--name", required=True); agent_flags(s)
    s.add_argument("--minutes", type=float, default=60.0, help="agent budget per edit (default 60)"); batch_flags(s)
    s.add_argument("--force", action="store_true"); s.add_argument("--no-preflight", action="store_true"); s.set_defaults(f=cmd_edit)
    s = sp.add_parser("edit-refine", help="+Ours for Editing: one refinement round on finished edit trials (edit critic, memory)")
    _filters(s); s.add_argument("--name", required=True); agent_flags(s, default_agent=None)
    s.add_argument("--minutes", type=float, default=60.0); s.add_argument("--rounds", type=int, default=1)
    s.add_argument("--critic-model", default=None); s.add_argument("--no-memory", action="store_true")
    s.add_argument("--no-after", action="store_true", help="skip recording + reviewing the after-state")
    batch_flags(s); s.add_argument("--force", action="store_true"); s.set_defaults(f=cmd_refine)
    s = sp.add_parser("eval-edit", help="evaluate edit trials (ES + P items, preservation baseline, host fidelity)")
    _filters(s); s.add_argument("--name", required=True)
    s.add_argument("--round", type=int, default=0, help="the hand-in after K refine rounds (result@edit-r<K>.json)")
    s.add_argument("--tag", help="default: edit, or edit-r<K> with --round")
    s.add_argument("--workers", type=int, default=1); s.add_argument("--force", action="store_true")
    s.add_argument("--no-host-fidelity", action="store_true")
    s.set_defaults(f=cmd_eval)

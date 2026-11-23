"""`gab bench`: the whole benchmark for one agent configuration, in one resumable command.

  1. Construction generation   gab run --no-eval     (one autonomous attempt per task, --minutes, default 120)
  2. Editing generation        gab edit              (one attempt per edit task on its start checkpoint, --edit-minutes, default 60)
  3. Construction evaluation   gab eval              (Full by default; --lite: replay verifier only)
  4. Editing evaluation        gab eval-edit
  5. Report                    gab score -> <runs_dir>/NAME/report[@lite]/ (summary.json, main_table.md / .csv, tasks.csv, ...)

Each step skips what is already done, so re-running the same command continues where it stopped (a trial that ended in an
infrastructure error runs again). A step that fails for some trials does not stop the later steps; the exit code is non-zero
then. The "+Ours" refinement rounds are separate (`gab refine`, `gab edit-refine`, scripts/reproduce_paper.sh)."""
import argparse

from .. import config
from ..util import log


def _ns(a, **kw):
    """the shared agent / batch options of `gab bench` + the step's own -> an argparse namespace for the step command"""
    base = {k: getattr(a, k, None) for k in ("name", "agent", "agent_cmd", "model", "base_url", "api_key_env", "reasoning_effort",
                                             "auth", "agent_in_container", "workers", "yes", "force", "no_preflight")}
    base.update(bench=True, plan=False, track=a.track, game=a.game, task=None, category=None, modality=None, level="fine")
    base.update(kw)
    return argparse.Namespace(**base)


def _step(name, fn, ns):
    """run one step command -> exit code (0 = ok); a SystemExit of the step is caught, an interrupt (143) is passed on"""
    log(f"bench: {name}")
    try: fn(ns); return 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if code == 143: raise
        if code: log(f"bench: {name} ended with exit code {code} (continuing with the next step)")
        return code


def counts(a):
    """the selected tasks per track: ({track: n construction}, {track: n editing})"""
    from ..cli import _tasks, _edits, track_of
    ns = _ns(a, task=a.task)
    ts = [] if a.editing_only else _tasks(ns)
    es = [] if a.construction_only else _edits(argparse.Namespace(edit=a.edit, game=a.game, track=a.track))
    nt = {"all": len(ts), "open": sum(track_of(t.meta) == "open" for t in ts), "commercial": sum(track_of(t.meta) == "commercial" for t in ts)}
    ne = {"all": len(es), "open": sum(e.track == "open" for e in es), "commercial": sum(e.track == "commercial" for e in es)}
    return nt, ne


def bench(a):
    """-> exit code"""
    from .. import cli
    from ..edit import cli as editcli
    from ..report.aggregate import score
    lite = bool(getattr(a, "lite", False)); codes = []
    nt, ne = counts(a)
    mode = "Lite (replay verifier only)" if lite else "Full (replay verifier + play verifier + adjudication)"
    log(f"bench {a.name}: {nt['all']} Construction + {ne['all']} Editing tasks (track {a.track}), agent "
        f"{'cmd' if a.agent_cmd else a.agent} {a.model or ''}, evaluation {mode}")
    ew = a.eval_workers or a.workers or 4
    if not a.editing_only:
        codes.append(_step("Construction generation", cli.cmd_run, _ns(a, task=a.task, minutes=a.minutes, no_eval=True,
                                                                         no_reference=False, drop_modality=None, allow_unverified=False,
                                                                         seed=0, plan=a.plan)))
    if not a.construction_only:
        codes.append(_step("Editing generation", editcli.cmd_edit, _ns(a, edit=a.edit, cls=None, parent=None, minutes=a.edit_minutes,
                                                                       plan=a.plan)))
    if a.plan: return 0
    if not a.editing_only:
        codes.append(_step("Construction evaluation", cli.cmd_eval, _ns(a, task=a.task, deliverables=None, from_file=None, lite=lite,
                                                                        evidence=None, round=0, tag=None, workers=ew,
                                                                        play_actions=None, play_minutes=None, no_host_fidelity=False)))
    if not a.construction_only:
        codes.append(_step("Editing evaluation", editcli.cmd_eval, _ns(a, edit=a.edit, cls=None, parent=None, round=0, tag=None,
                                                                       workers=ew, no_host_fidelity=False)))
    name = f"{a.name}@lite" if lite else a.name
    meta = (f"GameAnything-Bench: {a.name} -- agent {'cmd' if a.agent_cmd else a.agent} {a.model or ''}; track {a.track}; "
            f"{mode} evaluation; judge {config.get('judge_model')}")
    s = score(name, n_tasks=nt, n_edits=ne, meta=meta)
    print("\n" + s["main_table"])
    log(f"bench {a.name}: report in {s['report']}")
    return next((c for c in codes if c), 0)

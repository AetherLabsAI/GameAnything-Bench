"""Aggregation: reads only result files; idempotent. A cell = one run name (fine level); its
column score is the mean over tasks with a 95% bootstrap interval over tasks. Columns: Content Realization, Functions &
Playability, Visual & Presentation, and Overall (per task result scores.overall: 0.3 CR + 0.3 FP + 0.3 VP + 0.1 P, P = the
host-fidelity Preservation score, missing dimensions renormalized, gab/eval/scoring.py; a gate failure = 0 everywhere).
The gate is not a column: the gate pass rate and the failures by reason (build / load / unstable /
host_removed) are reported next to the scores. Also: the same by reference kind (video / text / image / hybrid),
explicit-requirement satisfaction, agent and evaluation cost."""
import csv, json, random
from pathlib import Path

from .. import config
from ..run.task import COLS_C
from ..util import wjson

SHORT = {"content_realization": "CR", "functions_playability": "F&P", "visual_presentation": "V&P", "overall": "Overall"}
COLS = (*COLS_C, "overall")


def task_scores(r):
    """result.json -> {column: score} for COLS; overall recomputed from the columns when an older result lacks it"""
    C = dict(r["scores"]["C"]); ov = r["scores"].get("overall")
    if ov is None and "overall" not in r["scores"]:
        xs = [C.get(c) for c in COLS_C]; ov = None if any(x is None for x in xs) else round(sum(xs) / len(xs), 4)
    return {**{c: C.get(c) for c in COLS_C}, "overall": ov}


def gate_fail(r):
    """gate failure reason of a result: build | load | unstable | host_removed, None if it passed (older results: from the status)"""
    g = r.get("gate") or {}
    if g.get("pass") is not False: return None
    return g.get("fail") or {"build_failed": "build", "load_failed": "load"}.get(r.get("status"), "load")
KINDS = ("video", "text", "image", "hybrid")


def split_tag(name):
    """`run@tag` -> (run, tag): a tagged evaluation (gab eval --tag: result@tag.json); plain names read result.json only"""
    n, _, t = name.partition("@"); return n, t or None


def results(name):
    n, tag = split_tag(name); d = config.path("runs_dir") / n
    return [json.loads(p.read_text()) for p in sorted(d.glob(f"*/fine/result{'@' + tag if tag else ''}.json"))] if d.exists() else []


def edit_results(name, rnd=0):
    """the editing results of run `name` (result@edit.json, or result@edit-r<K>.json after K refine rounds)"""
    from ..eval.edit import tag_of_round
    d = config.path("runs_dir") / split_tag(name)[0]
    return [json.loads(p.read_text()) for p in sorted(d.glob(f"E-*/edit/result@{tag_of_round(rnd)}.json"))] if d.exists() else []


def track(r):
    """open | commercial of a result's task (construction: task.yaml track; editing: the edit task's row)"""
    from ..cli import track_of
    if r.get("mode") == "edit":
        from ..edit.edit import edit_tasks
        return (edit_tasks().get((r.get("edit") or {}).get("edit_task_id")) or {}).get("track") or "open"
    from ..run.task import Task
    try: return track_of(Task(r["trial"]["task"]).meta)
    except Exception: return "open"


SCORED = ("completed", "timeout", "build_failed", "load_failed", "gate_failed", "host_removed", "unstable")   # a result; the
# rest (infra_error, error, budget_exceeded) is not counted as a score but reported as pending


def edit_cell(R):
    """editing results -> {n, included, pending, ES, P, P_fidelity, overall, agent_usd} (means over the included results; P /
    P_fidelity over the results that have one)"""
    inc = [r for r in R if r.get("status") in SCORED]
    sc = [r.get("scores") or {} for r in inc]
    return {"n": len(R), "included": len(inc), "pending": len(R) - len(inc),
            **{k: (None if mean([s.get(k) for s in sc]) is None else round(mean([s.get(k) for s in sc]), 4)) for k in ("ES", "P", "P_fidelity", "overall")},
            "n_P": sum(1 for s in sc if s.get("P") is not None), "n_P_fidelity": sum(1 for s in sc if s.get("P_fidelity") is not None),
            "agent_usd": mean([((r.get("cost") or {}).get("agent") or {}).get("usd") for r in inc])}


def construction_cell(R):
    """construction results -> the paper's columns: included / pending, CR, FP, VP, P, overall, agent_usd (means over included)"""
    inc = [r for r in R if r.get("status") in SCORED]
    S = [task_scores(r) for r in inc]
    out = {"n": len(R), "included": len(inc), "pending": len(R) - len(inc)}
    for c in COLS: out[c] = None if mean([x[c] for x in S]) is None else round(mean([x[c] for x in S]), 4)
    ps = [(r.get("scores") or {}).get("P") for r in inc]
    out["P"] = None if mean(ps) is None else round(mean(ps), 4); out["n_P"] = sum(1 for p in ps if p is not None)
    out["agent_usd"] = mean([((r.get("cost") or {}).get("agent") or {}).get("usd") for r in inc])
    return out


MAIN_HEAD = ["Track", "C: Content", "C: Func.", "C: Vis.", "C: Pres.", "C: Overall", "C: Cost ($)",
             "E: Edit Succ.", "E: Pres.", "E: Fidelity", "E: Overall", "E: Cost ($)", "C: evaluated", "E: evaluated"]


def main_rows(R, E, n_tasks=None, n_edits=None):
    """rows of the paper's main table (scores x 100, cost = mean agent USD per task) per track: all / open / commercial"""
    def p(x): return "--" if x is None else f"{100 * x:.1f}"
    def usd(x): return "--" if x is None else f"{x:.2f}"
    rows = []
    for tr in ("all", "open", "commercial"):
        r = [x for x in R if tr == "all" or track(x) == tr]; e = [x for x in E if tr == "all" or track(x) == tr]
        if not r and not e and tr != "all": continue
        c, ec = construction_cell(r), edit_cell(e)
        nt = (n_tasks or {}).get(tr); ne = (n_edits or {}).get(tr)
        rows.append([tr, p(c["content_realization"]), p(c["functions_playability"]), p(c["visual_presentation"]), p(c["P"]),
                     p(c["overall"]), usd(c["agent_usd"]), p(ec["ES"]), p(ec["P"]), p(ec["P_fidelity"]), p(ec["overall"]), usd(ec["agent_usd"]),
                     f"{c['included']} / {nt if nt is not None else c['n']}", f"{ec['included']} / {ne if ne is not None else ec['n']}"])
    return rows


def md_table(head, rows):
    return "\n".join(["| " + " | ".join(map(str, head)) + " |", "|" + "---|" * len(head), *("| " + " | ".join(map(str, r)) + " |" for r in rows)])


def kind(r):
    """reference kind of a result's task, from its id (T-VO / T-TO / T-IO / T-HY)"""
    return {"VO": "video", "TO": "text", "IO": "image", "HY": "hybrid"}.get(str(r["trial"]["task"]).split("-")[1], "?")


def mean(xs): xs = [x for x in xs if x is not None]; return sum(xs) / len(xs) if xs else None


def boot(xs, n=2000, seed=0):
    """95% bootstrap interval of the mean over tasks"""
    xs = [x for x in xs if x is not None]
    if len(xs) < 2: return None
    rng = random.Random(seed); ms = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return [round(ms[int(0.025 * n)], 3), round(ms[int(0.975 * n) - 1], 3)]


def cell(R):
    out = {"n": len(R), "status": {}}
    for r in R: out["status"][r["status"]] = out["status"].get(r["status"], 0) + 1
    R = [r for r in R if r["status"] not in ("infra_error", "budget_exceeded")]   # an infrastructure failure / a run stopped by
    # its USD cap is not a result (gab/run/infra.py): counted, not scored
    S = [task_scores(r) for r in R]
    for c in COLS:
        xs = [s[c] for s in S]
        out[c] = {"mean": None if mean(xs) is None else round(mean(xs), 3), "ci95": boot(xs), "n": len([x for x in xs if x is not None])}
    gf = [gate_fail(r) for r in R if (r.get("gate") or {}).get("pass") is not None]
    out["gate_pass"] = round(sum(1 for f in gf if f is None) / len(gf), 3) if gf else None
    out["gate_fail"] = {k: gf.count(k) for k in ("build", "load", "unstable", "host_removed") if gf.count(k)}
    ex = [e["satisfied"] for r in R for e in r.get("explicit_requirements") or [] if e.get("satisfied") is not None]
    out["explicit_satisfied"] = round(mean(ex), 3) if ex else None
    for k in ("usd", "wall_s", "tokens_in", "tokens_out", "tokens_cached"):
        out[f"agent_{k}"] = mean([(r["cost"]["agent"] or {}).get(k) for r in R])
    out["eval_usd"] = mean([r["cost"]["eval"].get("usd") for r in R]); out["eval_game_minutes"] = mean([r["cost"]["eval"].get("game_minutes") for r in R])
    out["could_not_test"] = sum(1 for r in R for i in r["items"] if i.get("could_not_test")); out["leaks"] = sum(1 for r in R if r.get("leaks"))
    return out


def score(name, edit_round=0, n_tasks=None, n_edits=None, meta=None):
    """aggregate a run -> <runs_dir>/<run>/report[@tag]/: summary.json (construction cells by reference kind, editing cells,
    the main table rows), tasks.csv, edits.csv, cost.csv, main_table.md / .csv. n_tasks / n_edits: {track: number of tasks
    selected} for the coverage column; meta: shown at the top of main_table.md"""
    n, tag = split_tag(name)
    R = results(name); E = edit_results(name, edit_round)
    rep = config.path("runs_dir") / n / (f"report@{tag}" if tag else "report"); rep.mkdir(parents=True, exist_ok=True)
    summ = {"all": cell(R), **{k: cell([r for r in R if kind(r) == k]) for k in KINDS}}
    rows = main_rows(R, E, n_tasks, n_edits)
    ed = {tr: edit_cell([e for e in E if tr == "all" or track(e) == tr]) for tr in ("all", "open", "commercial")}
    wjson(rep / "summary.json", {"name": name, "n_results": len(R), "n_edit_results": len(E), "cells": summ, "editing": ed,
                                 "construction": {tr: construction_cell([r for r in R if tr == "all" or track(r) == tr]) for tr in ("all", "open", "commercial")},
                                 "main_table": {"head": MAIN_HEAD, "rows": rows}, **({"meta": meta} if meta else {})})
    with open(rep / "main_table.csv", "w", newline="") as f: csv.writer(f).writerows([MAIN_HEAD, *rows])
    (rep / "main_table.md").write_text((f"{meta}\n\n" if meta else "") + md_table(MAIN_HEAD, rows) + "\n\nScores x 100 (C: Construction, "
        "E: Editing); Cost = mean agent USD per task; infra errors are pending, not 0 (see the evaluated column).\n")
    with open(rep / "edits.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["edit", "status", "ES", "P", "P_fidelity", "overall", "agent_usd"])
        for r in E:
            s_ = r.get("scores") or {}
            w.writerow([(r.get("edit") or {}).get("edit_task_id"), r.get("status"), s_.get("ES"), s_.get("P"), s_.get("P_fidelity"),
                        s_.get("overall"), ((r.get("cost") or {}).get("agent") or {}).get("usd")])
    with open(rep / "tasks.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["task", "kind", "status", "gate", "gate_fail", *COLS])
        for r in R:
            ts = task_scores(r); w.writerow([r["trial"]["task"], kind(r), r["status"], r["gate"].get("pass"), gate_fail(r), *[ts[c] for c in COLS]])
    with open(rep / "cost.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["task", "agent_usd", "agent_wall_s", "tokens_in", "tokens_out", "tokens_cached", "eval_usd", "eval_wall_s", "game_minutes"])
        for r in R:
            a, e = r["cost"]["agent"], r["cost"]["eval"]
            w.writerow([r["trial"]["task"], a.get("usd"), a.get("wall_s"), a.get("tokens_in"), a.get("tokens_out"), a.get("tokens_cached"), e.get("usd"), e.get("wall_s"), e.get("game_minutes")])
    return {"name": name, "n_results": len(R), "n_edit_results": len(E), "cells": summ, "editing": ed["all"],
            "main_table": md_table(MAIN_HEAD, rows), "report": str(rep)}


def fmt(x, ci=None): return "–" if x is None else f"{x:.2f}" + (f" [{ci[0]:.2f},{ci[1]:.2f}]" if ci else "")


def table(names, kind_="main"):
    """markdown table (CSV + LaTeX next to it in runs/_tables/). main: CR / F&P / V&P / Overall + gate pass rate + explicit
    requirements; modality: CR / F&P / V&P / Overall per reference kind; cost: agent and evaluation cost"""
    S = {n: score(n) for n in names}; rows = []
    if kind_ == "main":
        head = ["run", "n", *[SHORT[c] for c in COLS], "gate", "explicit"]
        for n in names:
            c = S[n]["cells"]["all"]; rows.append([n, c["n"], *[fmt(c[x]["mean"]) for x in COLS], fmt(c["gate_pass"]), fmt(c["explicit_satisfied"])])
    elif kind_ == "modality":
        head = ["run"] + [f"{k[:3]}:{SHORT[c]}" for k in KINDS for c in COLS]
        for n in names: rows.append([n] + [fmt(S[n]["cells"][k][c]["mean"]) for k in KINDS for c in COLS])
    else:
        head = ["run", "n", "agent $", "agent min", "tokens in", "tokens out", "cached", "eval $", "game min"]
        for n in names:
            c = S[n]["cells"]["all"]
            rows.append([n, c["n"], fmt(c["agent_usd"]), fmt((c["agent_wall_s"] or 0) / 60 if c["agent_wall_s"] else None),
                         f"{c['agent_tokens_in'] or 0:.0f}", f"{c['agent_tokens_out'] or 0:.0f}", f"{c['agent_tokens_cached'] or 0:.0f}",
                         fmt(c["eval_usd"]), fmt(c["eval_game_minutes"])])
    td = config.path("runs_dir") / "_tables"; td.mkdir(parents=True, exist_ok=True)
    with open(td / f"{kind_}.csv", "w", newline="") as f: csv.writer(f).writerows([head, *rows])
    (td / f"{kind_}.tex").write_text("\\begin{tabular}{" + "l" * len(head) + "}\n" + " & ".join(map(str, head)).replace("&P", "\\&P") + " \\\\\n\\hline\n" +
                                     "".join(" & ".join(map(str, r)).replace("_", "\\_") + " \\\\\n" for r in rows) + "\\end{tabular}\n")
    return "\n".join(["| " + " | ".join(map(str, head)) + " |", "|" + "---|" * len(head), *("| " + " | ".join(map(str, r)) + " |" for r in rows)])

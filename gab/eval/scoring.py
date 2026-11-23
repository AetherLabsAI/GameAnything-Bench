"""Item -> column scores for one trial (docs/evaluation.md: rubric v4 + the gate). Pure functions (unit-tested without
games). Three judged columns (Content Realization CR, Functions & Playability FP, Visual & Presentation VP) plus P = Preservation,
the host-fidelity score 0 / 0.5 / 1 of gab/eval/host_fidelity.py (fine/host_fidelity[@tag].json).

Overall = 0.3 CR + 0.3 FP + 0.3 VP + 0.1 P (WEIGHTS). A dimension that is missing (a column without items, P not judged, P
"insufficient" / judge_failed / no record) is left out and the remaining weights are renormalized to sum to 1: without P,
Overall is exactly the equal-weight mean of the columns that have a score. A gate failure (gab/eval/regression.py: build, load,
unstable, host_removed) sets every column and Overall to 0, whatever P is.
`overall_cfv` keeps the previous rule (equal-weight mean of CR / FP / VP, None if any is None): the human-agreement analyses
compare against humans who never scored P."""
from statistics import median

from ..run.task import COLS_C

CR, FP, VP = COLS_C


def median3(xs):
    """median of the judge runs; a missing run counts as nothing (median of what came back); None if none came back"""
    xs = [float(x) for x in xs if x is not None]
    return float(median(xs)) if xs else None


def snap(x):
    """judge scores are 0 / 0.5 / 1; a median of 3 is already one of them, other inputs are snapped to the nearest"""
    return None if x is None else min((0.0, 0.5, 1.0), key=lambda v: abs(v - float(x)))


WEIGHTS = {CR: 0.3, FP: 0.3, VP: 0.3, "P": 0.1}


def overall_cfv(C):
    """the 3-column rule (without P): equal-weight mean of CR / FP / VP; None if any column is None"""
    xs = [C.get(c) for c in COLS_C]
    return None if any(x is None for x in xs) else round(sum(xs) / len(xs), 4)


def preservation(hf):
    """a host_fidelity[@tag].json record (or None) -> {"score": 0.0 | 0.5 | 1.0 | None, "status": ...}. Only status "scored"
    with a 0 / 0.5 / 1 score counts; insufficient, gate_failed, judge_failed, no_result or no record -> score None"""
    if not hf: return {"score": None, "status": "missing"}
    st, s = hf.get("status"), hf.get("score")
    ok = st == "scored" and isinstance(s, (int, float)) and not isinstance(s, bool) and float(s) in (0.0, 0.5, 1.0)
    return {"score": float(s) if ok else None, "status": st if st else "missing"}


def overall(C, P=None, gate_pass=None):
    """Overall = WEIGHTS-weighted mean of CR, FP, VP and P over the dimensions that have a score (weights renormalized);
    without P it is the plain mean of the columns present (identical to the 3-column mean when all three are there).
    gate_pass False -> 0.0. None if no dimension has a score"""
    if gate_pass is False: return 0.0
    xs = {c: C.get(c) for c in COLS_C if C.get(c) is not None}
    if P is not None: xs["P"] = float(P)
    if not xs: return None
    if "P" not in xs: return round(sum(xs.values()) / len(xs), 4)
    return round(sum(WEIGHTS[k] * v for k, v in xs.items()) / sum(WEIGHTS[k] for k in xs), 4)


def with_preservation(scores, hf, gate_pass=None):
    """result["scores"] + a host_fidelity record (None = not available) -> new scores dict with P, P_status, overall (new rule)
    and overall_cfv (3-column rule); the columns are not changed"""
    C = scores["C"]; p = preservation(hf)
    gp = False if gate_pass is False else gate_pass
    return {**scores, "C": C, "P": p["score"], "P_status": p["status"], "overall": overall(C, p["score"], gp), "overall_cfv": overall_cfv(C)}


def column_scores(items, gate_pass, hf=None):
    """items: [{id, column, score, could_not_test, excluded?}] (+ the trial's host_fidelity record, None if not available) ->
    {"C": {content_realization, functions_playability, visual_presentation}, "P", "P_status", "overall" (WEIGHTS rule),
    "overall_cfv" (mean of the three)}. Column = mean of its items. A could-not-test item stays in the denominator with score
    0 unless it is `excluded` (the harness, not the build, could not test it; counted in the result).
    Gate failure (build, load, unstable, host_removed) -> every column 0 and overall 0."""
    def col(c):
        xs = [i for i in items if i["column"] == c and not i.get("excluded")]
        if not xs: return None
        return round(sum(float(i["score"] or 0) for i in xs) / len(xs), 4)
    C = {c: (0.0 if gate_pass is False else col(c)) for c in COLS_C}
    return with_preservation({"C": C}, hf, gate_pass)


def explicit(reqs, items):
    """explicit user requirements of the fine instruction: satisfied = mean score of the covered CR / FP / VP items; None when
    no covered item has a score"""
    sc = {i["id"]: i["score"] for i in items}
    out = []
    for r in reqs:
        xs = [sc[c] for c in r["covers"] if sc.get(c) is not None]
        out.append({"type": r["type"], "items": r["covers"], "satisfied": round(sum(xs) / len(xs), 3) if xs else None})
    return out

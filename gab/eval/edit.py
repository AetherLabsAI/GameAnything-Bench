"""Evaluation of an edit trial (`gab eval-edit`). Tags: `edit` (the edit round's hand-in) and `edit-r<K>` (after K refine
rounds); the snapshot of round K is the deliverable after K refine rounds (top level, or attempts/<n>/ whose agent.json has K
rounds). One evidence mode (replay verifier: shots + state + replay, no play verifier).

Order (as gab/eval/run.py):
  gate      build + install + load (run.gate_check), the gate probes (regression.evaluate): build_failed / load_failed /
            unstable / host_removed -> every item 0, Overall 0
  replays   every trace of the hand-in on a fresh instance (functions.replays: screenshots + state, recorded when eval_record)
  judging   the edit rubric (benchmark/editing/rubrics.json): ES items (edit success) and P items (preservation), anchors
            1 / 0.5 / 0, plus the completion-quality item ESQ. A locator call first finds, per item, the time segments of the
            replay recordings where its evidence should be visible (gab/eval/edit_v2.py); items with judge "visual" -> the
            visual verifier (candidates: the hand-in's shots + our replay screenshots + the located frames; BEFORE pictures of
            the checkpoint for comparison); the others -> the replay verifier (our replays' screenshots + state timelines; the
            checkpoint's replays as BEFORE). The same judge machinery as construction (util.claude, judge.run with its check,
            one follow-up, one retry; judge_failed -> infra_error).
  baseline  the P items scored once on the checkpoint itself (its saved base evaluation, no game), cached per (checkpoint,
            edit task) in <store>/p_baseline/<edit>.json. A P item the checkpoint scores 0 is N/A (excluded). A P item the
            checkpoint scores 0.5 is scored RELATIVE to it: relative = min(1, score / baseline), so keeping the checkpoint's
            half-working state counts as fully preserved and losing it counts 0. Baseline 1: relative = score.
  P_fidelity the construction host-fidelity judge (host_fidelity.run_trial) on this evaluation's evidence.
  P -> ES   P items that test the edit itself (they can only pass once the change is made, e.g. "with the bridge opened the
            towers still show as the new lattice towers") are scored as ES items: benchmark/editing/p_to_es.json (a one-off
            judge-model classification per edit, reviewed). Other P items whose baseline is 0 stay N/A.
  Overall   ES x (0.5 + 0.4 P + 0.1 P_fidelity): a no-op scores 0, a perfect edit 1, a correct edit that breaks everything
            else about 0.5. Inside the bracket a missing part (no P item left, P_fidelity not scored) is left out and the
            bracket renormalized (0.5 + 0.4 P) / 0.9 etc.; a gate failure -> 0.
-> <trial>/result@<tag>.json (+ eval@<tag>/)."""
import json, re, shutil, subprocess, time, traceback
from pathlib import Path

from .. import __version__, config
from ..run import infra
from ..util import claude, log, rjson, tree_hash, wjson
from . import functions, judge, regression

DEFAULT_TAG = "edit"
WEIGHTS = {"base": 0.5, "P": 0.4, "P_fidelity": 0.1}      # the bracket of ES x (0.5 + 0.4 P + 0.1 P_fidelity)
FORMULA = "ES * (0.5 + 0.4 P + 0.1 P_fidelity); missing P / P_fidelity left out of the bracket (renormalized); gate failure -> 0"
COLUMN = {"ES": "edit_success", "P": "preservation"}
BASELINE_VERSION = "edit-pbaseline-v2"      # only the P items that stay P (p_to_es.json)

INTRO = """A developer agent was given a working mod for {game} and asked to change it.
What the mod did before the change (its original request):
<<<
{parent}
>>>
The change the user asked for:
<<<
{edit}
>>>
Everything the change request does not name should stay as it was."""

BASE_NOTE = """You score the project as it is BEFORE any change, to find out which PRESERVATION items it satisfies at all (they
are written for the changed build: read "the restyled X" / "still" as the X and the behaviour as they are in this build, and
judge whether the content or behaviour each item protects is there and works)."""

BEFORE_NOTE = """For comparison, the captures of the project BEFORE the change (its own traces replayed by an earlier evaluation)
are in {before}/ (listed under BEFORE below): use them to know how things were and what stayed the same, never as evidence of
the changed build."""

REPLAY = """You are the REPLAY VERIFIER of a game-modding benchmark (editing track). {intro}

WE replayed each player-input trace of the {which} with real input on a clean install and captured the screenshots listed
below; at every screenshot we also read the game's structured state from the running game (engine fields, what mods created
inside the engine, on-screen text). Score each item 1 / 0.5 / 0 with its anchors, using ONLY what these captures show (read the
screenshots with the Read tool). EDIT items (ES) ask whether the change was made as asked; PRESERVATION items (P) ask whether
something the project had before the change still works or is still there. A claim the captures do not show counts for
nothing. If the captures cannot show an item at all, score it 0 and say so; set "could_not_test": true only if the replay
itself broke (e.g. the game did not start).
{note}

The timeline of each replay lists, per screenshot, the input since the previous one and the state diff (changed old -> new,
+added, -removed). Full snapshots are in each replay folder (state/*.json) if you need more than a diff.
{rule}

ITEMS:
{items}

REPLAYS{after}:
{replays}
{before}
Answer with ONLY a JSON object: {{"items": [{{"id": "ES1", "score": 0, "could_not_test": false, "evidence": ["<screenshot>: what it shows / which state change"], "reason": "<= 40 words"}}, ...]}}"""

VISUAL = """You are the VISUAL VERIFIER of a game-modding benchmark (editing track). {intro}

For each visual item, find the candidate screenshot(s) of the {which} that best show what the item asks about, then score
0 / 0.5 / 1 with its anchors. EDIT items (ES) ask whether the change was made as asked and looks like part of {game} (its own
art style, scale and camera); PRESERVATION items (P) ask whether something the project had before still looks as it did.
{note} If no candidate shows the item, the score is 0. Read the candidate screenshots with the Read tool before scoring.

VISUAL ITEMS:
{items}

CANDIDATE SCREENSHOTS{after}:
{candidates}
{before}
Answer with ONLY a JSON object: {{"items": [{{"id": "ES1", "score": 0, "best": ["<candidate path>"], "reason": "<= 40 words"}}, ...]}}"""

VBEFORE_NOTE = """BEFORE pictures of the project (before the change) are listed under BEFORE: use them to see what changed and
what stayed the same; score only what the AFTER candidates show. Our replay screenshots show the changed build as it actually
runs; the developer's screenshots may be outdated: where they disagree, our replay screenshots decide."""


def round_of_tag(tag):
    m = re.search(r"-r(\d+)$", tag or "")                   # edit (the edit round's hand-in), edit-r1, edit-r2, ...
    return int(m.group(1)) if m else 0


def tag_of_round(k): return DEFAULT_TAG if not k else f"{DEFAULT_TAG}-r{int(k)}"


def snapshot(d, k):
    """the hand-in after k refine rounds -> (deliverable dir, path relative to d) or (None, None)"""
    d = Path(d); ag = rjson(d / "agent" / "agent.json", {}) or {}; n = len(ag.get("refine_rounds") or [])
    running = (d / "agent" / "refine_running.json").exists()
    if n == k and not running and (d / "deliverable").is_dir(): return d / "deliverable", "deliverable"
    for a in sorted((d / "attempts").glob("*"), key=lambda p: int(p.name) if p.name.isdigit() else -1, reverse=True):
        aj = rjson(a / "agent" / "agent.json", None)
        if aj is not None and len(aj.get("refine_rounds") or []) == k and (a / "deliverable").is_dir():
            return a / "deliverable", str((a / "deliverable").relative_to(d))
    if n == k and (d / "deliverable").is_dir(): return d / "deliverable", "deliverable"
    return None, None


def parent_item(et, ref):
    """'parent:FP3' -> the parent task's rubric item text, or None"""
    pid = str(ref).split(":", 1)[1].strip() if ":" in str(ref) else None
    if not pid: return None
    for sec in ("content_realization", "functions_playability", "visual_presentation"):
        try: items = et.parent.items(sec)
        except Exception: return None
        for i in items:
            if i["id"] == pid: return f"{pid}: {i['item']}"
    return None


def items_text(et, F):
    L = []
    for f in F:
        kind = "edit" if f["id"].startswith("ES") else "preservation"
        s = (f"{f['id']} ({kind}): {f['item']}\n"
             f"  anchors: 1 = {f['anchors']['1']} | 0.5 = {f['anchors']['0.5']} | 0 = {f['anchors']['0']}")
        rel = [parent_item(et, e) for e in f.get("evidence") or [] if str(e).startswith("parent:")]
        rel = [r for r in rel if r]
        if rel: s += "\n  the original project's requirement it relates to: " + " | ".join(rel)
        L.append(s)
    return "\n".join(L)


def _copy_replays(R, dst):
    """copies of replay captures (screenshots + state) under dst/<trace>/ -> R with the copied paths"""
    out = []
    for r in R:
        stem = Path(r["trace"]).stem; q = Path(dst) / stem; q.mkdir(parents=True, exist_ok=True); shots = []
        src = Path(r["shots"][0]).parent if r.get("shots") else None
        for s in r.get("shots") or []:
            if Path(s).exists(): shutil.copyfile(s, q / Path(s).name); shots.append(str(q / Path(s).name))
        if src is not None and (src / "state").is_dir() and not (q / "state").exists(): shutil.copytree(src / "state", q / "state", copy_function=shutil.copyfile)   # writable copies (the store is read-only)
        out.append({**r, "shots": shots})
    return out


def _replays_text(R, limit=None):
    rep = []
    for r in R:
        rep.append(f"- trace {r['trace']}: claims {json.dumps(r.get('claims') or [], ensure_ascii=False)}; replay "
                   f"{'ran' if r.get('ok') else 'FAILED TO RUN (an evaluation fault, not evidence against the mod; an item that only this trace would show is could_not_test): ' + str(r.get('log'))[:200]}\n"
                   f"  folder: {Path(r['shots'][0]).parent if r.get('shots') else '-'}\n"
                   f"  screenshots: {', '.join(r.get('shots') or []) or 'none'}\n"
                   f"  timeline (screenshot | input since the previous one | state diff):\n{functions.timeline_text(r.get('timeline'), limit or functions.TIMELINE_CHARS) or '    (none)'}\n"
                   f"  final state: {json.dumps(r.get('state'), ensure_ascii=False, default=str)[:800]}")
    return "\n".join(rep)


def replay_judge(et, F, R, out, cost, before=None, stage="edit_replay_verifier"):
    """the replay verifier on edit items -> {id: verdict}; before: the checkpoint's replays (copied) or None (baseline run)"""
    if not F: return {}
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    if not R: return {f["id"]: {"score": 0.0, "could_not_test": False, "reason": "no traces handed in", "evidence": []} for f in F}
    from ..game import base as gbase
    intro = INTRO.format(game=gbase.title(et.game), parent=et.parent_instruction(), edit=et.edit_instruction())
    if before is not None:
        bt = "\nBEFORE (the project before the change; comparison only):\n" + _replays_text(before, 3000) + "\n"
        note, which, aft = BEFORE_NOTE.format(before=out / "before"), "changed build", " (AFTER: the changed build)"
    else: bt, note, which, aft = "", BASE_NOTE, "project (before any change)", ""
    pr = lambda FF: REPLAY.format(intro=intro, which=which, note=note, rule=functions.STATE_RULE, items=items_text(et, FF),
                                  replays=_replays_text(R), after=aft, before=bt)
    run1 = lambda p, name: claude(p, out, name, tools=("Read",), allowed=("Read",), add_dirs=(out,), max_turns=100, cwd=out)
    fu = functions.followup_call(stage, lambda miss, name: run1(pr([f for f in F if f["id"] in miss]), name), functions.json_judge)
    v, bad = judge.run(cost, stage, lambda n: run1(pr(F), stage + ("" if n == 1 else f".try{n}")),
                       functions.json_judge([f["id"] for f in F]), followup=fu)
    return {f["id"]: v.get(f["id"]) or judge.placeholder(stage, bad) for f in F}


def _cands(dv, R, out, sub="cand", max_replay=None, stale=None):
    """jpg copies (960 px) of the hand-in's shots and the replay screenshots -> ["<rel path>  (what)"]. stale: the start
    checkpoint's shots dir; a developer screenshot identical to one of them shows the project BEFORE the change and is left out
    (an agent that ran out of time before updating shots/ would get ES 0 from its stale shots although our replays show the
    change)"""
    d = Path(out) / sub; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True); C = []
    from ..util import sha256
    old = {sha256(f) for f in Path(stale).glob("*.png")} if stale and Path(stale).is_dir() else set()
    def add(src, tag, what):
        dst = d / f"{tag}.jpg"
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vf", "scale=960:-2", "-q:v", "4", str(dst)])
        if r.returncode == 0: C.append(f"{sub}/{dst.name}  ({what})")
    for i, f in enumerate(sorted((Path(dv) / "shots").glob("*.png"))[:6]):
        if old and sha256(f) in old: continue                          # unchanged from the start: not evidence of the change
        add(f, f"dev_{i:02d}", f"developer's screenshot {f.name}")
    for r in R or []:
        shots = r.get("shots") or []
        if max_replay and len(shots) > max_replay: shots = shots[::max(1, len(shots) // max_replay)][:max_replay]
        for j, s in enumerate(shots): add(s, f"replay_{Path(r['trace']).stem}_{j:02d}", f"our replay of trace {r['trace']}")
    return C


def visual_judge(et, F, dv, R, out, cost, before=None, stage="edit_visual_judge"):
    """the visual verifier on edit items -> {id: verdict}; before: (checkpoint deliverable, checkpoint replays) or None"""
    if not F: return {}
    from ..game import base as gbase
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    C = _cands(dv, R, out, stale=(Path(before[0]) / "shots") if before is not None else None)
    if not C: return {f["id"]: {"score": 0.0, "could_not_test": False, "reason": "no screenshots at all", "evidence": []} for f in F}
    intro = INTRO.format(game=gbase.title(et.game), parent=et.parent_instruction(), edit=et.edit_instruction())
    if before is not None:
        B = _cands(before[0], before[1], out, sub="before", max_replay=4)
        bt = "\nBEFORE (the project before the change; comparison only):\n" + "\n".join(B) + "\n"
        note, which, aft = VBEFORE_NOTE, "changed build", " (AFTER: the changed build)"
    else: bt, note, which, aft = "", BASE_NOTE, "project (before any change)", ""
    pr = lambda FF: VISUAL.format(intro=intro, which=which, game=gbase.title(et.game), note=note, items=items_text(et, FF),
                                  candidates="\n".join(C), after=aft, before=bt)
    run1 = lambda p, name: claude(p, out, name, tools=("Read",), allowed=("Read",), add_dirs=(out,), max_turns=150, cwd=out, timeout=2400)
    fu = functions.followup_call(stage, lambda miss, name: run1(pr([f for f in F if f["id"] in miss]), name),
                                 lambda miss: functions.json_judge(miss, need_score=True))
    v, bad = judge.run(cost, stage, lambda n: run1(pr(F), stage + ("" if n == 1 else f".try{n}")),
                       functions.json_judge([f["id"] for f in F], need_score=True), followup=fu)
    return {f["id"]: v.get(f["id"]) or judge.placeholder(stage, bad) for f in F}


def judge_all(et, F, dv, R, out, cost, before=None):
    """ES / P items -> {id: verdict}: judge "visual" -> visual verifier, others -> replay verifier"""
    vis = [f for f in F if f.get("judge") == "visual"]; ply = [f for f in F if f.get("judge") != "visual"]
    v = {}
    v.update(replay_judge(et, ply, R, Path(out) / "replay", cost, before=None if before is None else before[2]))
    v.update(visual_judge(et, vis, dv, R, Path(out) / "visual", cost, before=None if before is None else before[:2]))
    return v


def _score(x):
    if not x or x.get("judge_failed") or x.get("could_not_test") or x.get("score") is None: return None
    try: return float(x["score"])
    except (TypeError, ValueError): return None


def p_baseline(et, force=False):
    """the P items scored on the checkpoint itself (no game: its saved base evaluation), cached per (parent, checkpoint, edit)
    -> {"items": {id: score | None}, "checkpoint": sha, "status": "scored" | "judge_failed" | "gate_failed"}"""
    from ..edit.edit import checkpoint, ckpt_replays, file_lock
    st = et.store(); sha = (rjson(st / "checkpoint.json", {}) or {}).get("deliverable_sha256")
    f = st / "p_baseline" / f"{et.id}.json"
    with file_lock(st / "p_baseline" / f"{et.id}.lock"):
        P = split_rubric(et)[1]; pids = [p["id"] for p in P]
        b = rjson(f, None)
        if b and not force and b.get("checkpoint") == sha and b.get("version") == BASELINE_VERSION and b.get("status") == "scored" \
                and b.get("p_ids") == pids: return b
        out = st / "p_baseline" / et.id
        subprocess.run(["chmod", "-R", "u+rwX", str(out)], capture_output=True); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
        cres = rjson(st / "result.json", {}) or {}
        if not P: pass
        elif (cres.get("gate") or {}).get("pass") is not True:
            b = {"status": "gate_failed", "items": {p["id"]: 0.0 for p in P}}
        else:
            R = _copy_replays(ckpt_replays(st), out / "replay" / "captures"); cost = {}   # inside the replay judge's folder
            v = judge_all(et, P, st / "deliverable", R, out, cost)
            bad = [i for i in v.values() if i.get("judge_failed")]
            b = {"status": "judge_failed" if bad else "scored", "items": {k: _score(x) for k, x in v.items()},
                 "verdicts": v, "judges": judge.summary(cost), "usd": cost.get("usd", 0)}
        if not P: b = {"status": "scored", "items": {}}
        b = {**b, "edit": et.id, "parent": et.parent.id, "checkpoint": sha, "version": BASELINE_VERSION, "p_ids": pids,
             "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        wjson(f, b)
        log(f"{et.id}: preservation baseline on the checkpoint: {b['status']} {b['items']}")
        return b


def relative(score, base):
    """P item score relative to the checkpoint baseline: None = N/A (baseline 0 or unknown); baseline 0.5 -> min(1, s / 0.5)"""
    if base is None or float(base) == 0.0: return None
    if score is None: return 0.0
    return round(min(1.0, float(score) / float(base)), 4)


def edit_scores(items, gate_pass, pf=None):
    """items with column / score / relative / na -> {"ES", "P", "P_fidelity", "quality", "overall", ...}:
    overall = ES x quality, quality = (0.5 + 0.4 P + 0.1 P_fidelity) over the parts present"""
    es = [float(i["score"] or 0) for i in items if i["column"] == "edit_success"]
    ps = [float(i["relative"]) for i in items if i["column"] == "preservation" and not i.get("na")]
    ES = round(sum(es) / len(es), 4) if es else None
    P = round(sum(ps) / len(ps), 4) if ps else None
    num, den = WEIGHTS["base"], WEIGHTS["base"]
    for k, v in (("P", P), ("P_fidelity", pf)):
        if v is not None: num += WEIGHTS[k] * float(v); den += WEIGHTS[k]
    q = round(num / den, 4)
    ov = 0.0 if gate_pass is False else (round(ES * q, 4) if ES is not None else None)
    return {"ES": 0.0 if gate_pass is False else ES, "P": 0.0 if gate_pass is False else P, "P_fidelity": pf,
            "quality": 0.0 if gate_pass is False else q, "overall": ov, "formula": FORMULA,
            "n_ES": len(es), "n_ES_from_P": sum(1 for i in items if i.get("moved_from") == "preservation"), "n_P": len(ps),
            "n_P_na": sum(1 for i in items if i["column"] == "preservation" and i.get("na")), "weights": WEIGHTS}


def p_to_es_file():
    from ..edit.edit import edit_dir
    return edit_dir() / "p_to_es.json"


def _dep(v): return bool(v.get("manual")) if "manual" in v else bool(v.get("edit_dependent"))


def moved_ids(eid):
    """the P items of an edit scored as ES (p_to_es.json; an edit missing there: none, with a warning)"""
    d = rjson(p_to_es_file(), None) or {}
    x = (d.get("edits") or {}).get(eid)
    if x is None: log(f"{eid}: not in {p_to_es_file().name}: no P item is scored as ES"); return set()
    return {k for k, v in (x.get("items") or {}).items() if _dep(v)}


def split_rubric(et):
    """-> (ES items incl. the moved P items (moved_from preservation), the P items that stay)"""
    R = et.rubric(); mv = moved_ids(et.id)
    return R["edit_success"] + [{**i, "moved_from": "preservation"} for i in R["preservation"] if i["id"] in mv], \
        [i for i in R["preservation"] if i["id"] not in mv]


def _not_scored(d, et, tag, block, status="infra_error", err=None, judges=None, rel=None):
    tm = rjson(d / "trial.json", {}) or {}; ag = rjson(d / "agent" / "agent.json", {}) or {}
    res = {"trial": {k: tm.get(k) for k in ("task", "level", "agent", "model", "name")}, "mode": "edit", "edit": _edit_meta(et),
           "versions": {"gab": __version__}, "status": status, "error": err or f"{status}: {(block or {}).get('primary')}",
           "infra": block, "eval_settings": {"tag": tag, "evidence": "shots+state+replay"}, "judges": judges,
           "gate": {"pass": None}, "scores": {"ES": None, "P": None, "P_fidelity": None, "overall": None}, "items": [],
           "deliverable": {"path": rel}, "cost": {"agent": {k: ag.get(k) for k in ("wall_s", "usd", "exit_reason")}},
           "timestamps": {"eval_end": time.strftime("%Y-%m-%dT%H:%M:%S")}}
    wjson(Path(d) / f"result@{tag}.json", res); log(f"{et.id}: result@{tag}.json written ({status}); not scored")
    return res


def _edit_meta(et):
    from ..edit.edit import store_dir
    ck = rjson(et.store_path() / "checkpoint.json", {}) or {}
    return {"edit_task_id": et.id, "parent_task": et.parent.id, "edit_class": et.edit_class, "code": et.code,
            "scope": et.row.get("scope"), "reference_dependence": et.row.get("reference_dependence"),
            "checkpoint": {"label": ck.get("label"), "source": ck.get("source"), "deliverable_sha256": ck.get("deliverable_sha256"),
                           "overall": ck.get("overall")}}


def _es_levels():
    from .edit_v2 import ES_LEVELS_VERSION
    return ES_LEVELS_VERSION


def eval_edit(d, et, tag=None, seat=None, force=False, rnd=None, host_fid=True):
    """evaluate an edit trial's snapshot (round from the tag unless rnd) -> result@<tag>.json dict. Scoring: the edit rubric's
    items on 3 levels after a rubric-guided search of the replay videos, plus the completion-quality item (gab/eval/edit_v2.py)"""
    from ..edit.edit import ckpt_replays
    from ..host import base as hbase
    from ..game import base as gbase
    from .run import gate_check
    from ..run.trial import clean_name, check_deliverable
    from . import edit_v2
    tag = tag or tag_of_round(rnd); d = Path(d); e = d / f"eval@{tag}"; rf = d / f"result@{tag}.json"
    if rf.exists() and not force and (rjson(rf, {}) or {}).get("status") != "infra_error": return rjson(rf, {}) or {}
    k = round_of_tag(tag) if rnd is None else rnd
    dv, rel = snapshot(d, k)
    if dv is None: log(f"{et.id}: no hand-in after {k} refine round(s): not evaluated ({tag})"); return None
    ag = rjson(d / "agent" / "agent.json", {}) or {}
    if (ag.get("infra") or {}).get("status") == "infra_error": return _not_scored(d, et, tag, ag["infra"], rel=rel)
    if (ag.get("infra") or {}).get("status") == infra.BUDGET: return _not_scored(d, et, tag, ag["infra"], status=infra.BUDGET, rel=rel)
    st = et.store(); ES, P = split_rubric(et)
    ES = ES + [edit_v2.quality_item(et)]
    subprocess.run(["chmod", "-R", "u+rwX", str(e)], capture_output=True); shutil.rmtree(e, ignore_errors=True); e.mkdir(parents=True)
    tm = rjson(d / "trial.json", {}) or {}
    T0 = time.time(); ts = {"eval_start": time.strftime("%Y-%m-%dT%H:%M:%S"), "agent_start": tm.get("started")}
    cost = {}; gm = 0.0; verdicts = {}; status = "completed"; gate = {"pass": None}; err = None; ginfra = None; ver = {}; R = []
    own = seat is None
    try:
        if own: seat = hbase.get().acquire({"game": et.game}, f"gab-evaled-{clean_name(tm.get('name'))}-{et.id[-14:]}-e")
        ad = gbase.get(et.game, seat); ver = ad.version()
        inst = f"evaled-{clean_name(tm.get('name'))}-{et.code}"[:48] + "-" + __import__("hashlib").sha1(str(Path(d).resolve()).encode()).hexdigest()[:8]
        t0 = time.time(); mod = dv / "mod"
        gate = gate_check(ad, inst, mod, e); gm += (time.time() - t0) / 60
        if gate["pass"]:
            gp, m = regression.evaluate(et, ad, mod, e / "regression"); gm += m
            gate = {**gate, "pass": gp["pass"], "fail": gp["fail"], "probes": gp}
            if gp["pass"] is False: gate["reason"] = gp["reason"]
        if not gate["build"]: status = "build_failed"
        elif not gate["load"]: status = "load_failed"
        elif gate.get("fail") in ("unstable", "host_removed"): status = gate["fail"]
        log(f"{et.id}: gate {'pass' if gate['pass'] else gate.get('fail')}")
        if gate["pass"] is None and gate.get("fail") == "infra":
            ginfra = infra.make("gate_baseline_broken", (gate.get("probes") or {}).get("reason") or "")
        elif gate["pass"]:
            ad.provision(inst); ad.install_mod(inst, mod)
            t1 = time.time(); fn = e / "functions"
            R = functions.replays(ad, inst, dv, fn / "replay"); gm += (time.time() - t1) / 60
            wjson(fn / "functions.json", {"evidence": "shots+state+replay", "mode": "edit",
                                          "replays": [{k2: r.get(k2) for k2 in ("trace", "ok", "claims", "shots", "timeline")} for r in R]})
            BR = _copy_replays(ckpt_replays(st), fn / "replay" / "before")
            verdicts = edit_v2.judge_all_v2(et, ES + P, dv, R, fn, cost, before=(st / "deliverable", BR, BR))
            log(f"{et.id}: edit items judged")
    except Exception as x:
        status, err = "error", f"{type(x).__name__}: {x}"; (e / "error.txt").write_text(traceback.format_exc()); log(f"{et.id}: ERROR {err}")
    finally:
        if own and seat: seat.release()
    inc, n_calls = infra.from_calls(infra.calls(sorted(e.rglob("calls.jsonl"))))
    inc += judge.incidents(cost)
    if status == "error": inc += [{**i, "source": "eval exception", "evidence": str(e / "error.txt")}
                                  for i in infra.classify(f"{err}\n{(e / 'error.txt').read_text(errors='replace')}", exclude=("model-api",))]
    if gate.get("fail") in ("build", "load") and gate.get("log") and Path(gate["log"]).exists():
        inc += [{**i, "source": "eval gate", "evidence": gate["log"]} for i in infra.classify(Path(gate["log"]).read_text(errors="replace")[-200000:], exclude=("model-api",))]
    if ginfra: inc.append({**ginfra, "source": "eval gate probes"})
    if gate.get("fail") in ("unstable", "host_removed"):
        inc += [{**i, "source": "eval gate probes"} for i in infra.classify(str(gate.get("reason") or ""), exclude=("model-api",))]
    blk = infra.assess([{**i, "source": i.get("source") or "eval tool call"} for i in inc], infra_calls=n_calls) if inc else None
    if blk:
        for i in blk["incidents"]: infra.record(d, {**i, "sources": [f"eval@{tag}", *i.get("sources", [])]})
        if blk["status"]: return _not_scored(d, et, tag, blk, err=err, judges=judge.summary(cost), rel=rel)
    base = None
    if gate.get("pass") and status != "error":
        base = p_baseline(et)
        if base.get("status") == "judge_failed":
            blk = infra.assess([infra.make("judge_failed", "preservation baseline on the checkpoint", source="p_baseline")])
            return _not_scored(d, et, tag, blk, judges=judge.summary(cost), rel=rel)
    items = []
    for sec, xs in (("edit_success", ES), ("preservation", P)):
        for f in xs:
            v = verdicts.get(f["id"]); s = 0.0 if gate.get("pass") is not True else (_score(v) or 0.0)
            s = edit_v2.snap(s, edit_v2.LEVELS_ES if sec == "edit_success" else edit_v2.LEVELS_P)
            it = {"id": f["id"], "column": sec, "judge_kind": f.get("judge"), "score": s,
                  **({"moved_from": "preservation"} if f.get("moved_from") else {}),
                  "could_not_test": bool(v and v.get("could_not_test")), "evidence": v if v is not None else f"gate failed ({gate.get('fail')})",
                  "judge": config.get("judge_model") if v is not None else None, "trap": bool(f.get("trap")),
                  **({"added": f["added"]} if f.get("added") else {})}
            if sec == "preservation":
                b = ((base or {}).get("items") or {}).get(f["id"]) if base else None
                it["baseline"] = b
                if gate.get("pass") is True:
                    r_ = relative(s, b); it["na"] = r_ is None; it["relative"] = r_ if r_ is not None else None
                else: it["na"] = False; it["relative"] = 0.0
            items.append(it)
    if ag.get("exit_reason") == "timeout" and status == "completed": status = "timeout"
    gp = gate.get("pass") if status != "error" else None
    sc = edit_scores(items, False if gp is False else gp)
    res = {"trial": {k2: tm.get(k2) for k2 in ("task", "level", "agent", "model", "name", "parent_task")}, "mode": "edit",
           "edit": _edit_meta(et),
           "versions": {"game": ver.get("build"), "image": ver.get("image"), "gab": __version__, "judges": config.get("judge_model"),
                        "baseline": BASELINE_VERSION},
           "eval_settings": {"tag": tag, "evidence": "shots+state+replay", "round": k, "scoring": "v2", "es_levels": _es_levels()},
           "status": status, "error": err,
           "judges": judge.summary(cost), "gate": gate, "scores": sc, "items": items,
           "p_to_es": {"file": str(p_to_es_file()), "moved": sorted(i["id"] for i in ES if i.get("moved_from"))},
           "p_baseline": {"status": (base or {}).get("status"), "file": f"p_baseline/{et.id}.json" if base else None,
                          "rule": "P item: baseline 0 -> N/A; baseline 0.5 -> min(1, score / 0.5); baseline 1 -> score"},
           "deliverable": {"sha256": tree_hash(dv), "check": check_deliverable(dv), "path": rel},
           "leaks": ag.get("leaks") or [], "infra": blk or ag.get("infra"),
           "cost": {"agent": {k2: ag.get(k2) for k2 in ("wall_s", "tokens_in", "tokens_out", "tokens_cached", "usd", "exit_reason")},
                    "refine_rounds": [{k2: r.get(k2) for k2 in ("round", "wall_s", "usd", "exit_reason")} for r in ag.get("refine_rounds") or []][:k],
                    "eval": {"wall_s": round(time.time() - T0), "tokens_in": cost.get("tokens_in", 0), "tokens_out": cost.get("tokens_out", 0),
                             "usd": cost.get("usd", 0), "game_minutes": round(gm, 1)}},
           "timestamps": {**ts, "eval_end": time.strftime("%Y-%m-%dT%H:%M:%S")}}
    wjson(rf, res)
    log(f"{et.id}: {rf.name} written ({status}) ES={sc['ES']} P={sc['P']}")
    if host_fid and status != "error":
        from . import host_fidelity
        try: hf = host_fidelity.run_trial(d, et.parent.id, et.game, force=True, modality=et.parent.modality, tag=tag)
        except Exception as x:
            log(f"{et.id}: host fidelity failed: {type(x).__name__}: {x}"); hf = {"status": "error", "score": None, "reason": str(x)}
        from .scoring import preservation
        p = preservation(hf)
        sc = edit_scores(items, False if gp is False else gp, p["score"])
        res = {**res, "scores": {**sc, "P_fidelity_status": p["status"]},
               "host_fidelity": {"status": hf.get("status"), "score": hf.get("score"), "reason": hf.get("reason"),
                                 "file": host_fidelity.paths(d, tag)[2].name}}
        wjson(rf, res)
    log(f"{et.id}: {tag}: ES={res['scores']['ES']} P={res['scores']['P']} P_fidelity={res['scores']['P_fidelity']} -> overall {res['scores']['overall']}")
    return res

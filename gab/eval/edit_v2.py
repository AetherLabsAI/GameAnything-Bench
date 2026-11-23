"""Edit item scoring used by `gab eval-edit` (gab/eval/edit.py builds on the verifiers defined there):

  1. rubric-guided search of the replay videos: a LOCATE call sees, per replay, coarse contact sheets with the time burnt into
     every tile (one tile per 3 s), the trace's steps with their approximate times and the claims, and names for every item
     the time segments where its evidence should be visible; the harness cuts frames at up to 2 fps from those segments, and
     the scoring calls look at them first. An item may score 0 for "not shown" only when this search found nothing. The
     segments are recorded in each item's verdict (evidence.segments [{video, t0, t1}]).
  2. one more ES item, ESQ "completion quality" (judged visually on the AFTER evidence with BEFORE as the style reference),
     one ES item among the others with equal weight.
  All items (ES, ESQ, P) use the rubric's 3 levels 1 / 0.5 / 0. Overall: ES x (0.5 + 0.4 P + 0.1 P_fidelity)."""
import json, shutil, subprocess
from pathlib import Path

from ..util import claude, log, rjson
from . import functions, judge

LEVELS_ES = (0.0, 0.5, 1.0)
ES_LEVELS_VERSION = 3                   # eval_settings.es_levels of a v2 result
LEVELS_P = (0.0, 0.5, 1.0)
SHEET_STEP, SHEET_TILES = 3, (5, 4)                 # a tile every 3 s, 5 x 4 tiles = 60 s per sheet
SEG_MAX_S, SEG_FRAMES, CALL_FRAMES = 30, 20, 160    # a segment: at most 30 s and 20 frames (2 fps or sparser); per call at most 160
PAD_BEFORE, PAD_AFTER = 4.0, 2.0                    # every located segment is widened (an event can come in the step before the one
                                                    # the locator picked)
QUALITY_ID = "ESQ"


def snap(x, levels):
    return None if x is None else min(levels, key=lambda v: abs(v - float(x)))


def quality_item(et):
    from ..game import base as gbase
    t = gbase.title(et.game)
    return {"id": QUALITY_ID, "judge": "visual", "added": "quality", "evidence": ["edit-request"],
            "item": f"Completion quality (完成质量) of the changed content: it fits {t}'s own art style and scale, its materials and "
                    f"colours match the surroundings, and it has no clipping, missing faces, z-fighting, placeholder art, broken "
                    f"animation or text / UI glitches",
            "anchors": {"1": f"the changed content looks like it was made for {t}: style, scale and materials fit, no visible defect",
                        "0.5": "recognisably part of the game but with visible defects (e.g. clipping, flat placeholder colours, a "
                               "wrong scale, a slightly off material)",
                        "0": "crude, placeholder-like or broken (missing faces, z-fighting, broken animation, garbled UI), or the "
                             "change is not visible at all"}}


def items_text(et, F):
    from .edit import parent_item
    L = []
    for f in F:
        es = f["id"].startswith("ES") or f.get("moved_from")
        a = f["anchors"]
        an = " | ".join(f"{k} = {a[k]}" for k in ("1", "0.5", "0") if k in a)
        s = f"{f['id']} ({'edit' if es else 'preservation'}, 3 levels): {f['item']}\n  anchors: {an}"
        rel = [r for r in (parent_item(et, e) for e in f.get("evidence") or [] if str(e).startswith("parent:")) if r]
        if rel: s += "\n  the original project's requirement it relates to: " + " | ".join(rel)
        L.append(s)
    return "\n".join(L)


# ---------------------------------------------------------------- the videos: contact sheets, steps, frames
def _duration(v):
    try: return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(v)],
                                     capture_output=True, text=True, timeout=60).stdout.strip() or 0)
    except (ValueError, subprocess.TimeoutExpired): return 0.0


def videos(R, fn, out):
    """per replay with a recording: {stem: {video, duration, sheets, steps [(t, text)], claims}}"""
    V = {}; sd = Path(out) / "sheets"; sd.mkdir(parents=True, exist_ok=True)
    for r in R:
        stem = Path(r["trace"]).stem; v = Path(fn) / "replay" / f"{stem}.mp4"
        if not v.exists(): continue
        cols, rows = SHEET_TILES
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(v), "-vf",
                        f"fps=1/{SHEET_STEP},scale=320:-2,drawtext=text='%{{pts\\:hms}}':x=4:y=4:fontsize=20:fontcolor=yellow:box=1:"
                        f"boxcolor=black@0.6,tile={cols}x{rows}", "-q:v", "4", str(sd / f"{stem}_%02d.jpg")], capture_output=True, timeout=600)
        steps, t = [], 0.0
        for x in (rjson(Path(fn) / "replay" / stem / "replay.json", {}) or {}).get("log") or []:
            if isinstance(x, dict) and isinstance(x.get("step"), dict):
                st = x["step"]; steps.append((round(t, 1), " ".join(f"{k}={v}" for k, v in st.items())[:90])); t += float(x.get("s") or 0)
        V[stem] = {"video": str(v), "duration": round(_duration(v), 1), "sheets": [str(p) for p in sorted(sd.glob(f"{stem}_*.jpg"))],
                   "steps": steps, "claims": r.get("claims") or [], "trace": r["trace"]}
    return V


def videos_text(V, step_chars=2500):
    L = []
    for stem, x in V.items():
        st = "; ".join(f"t~{t:g}s {s}" for t, s in x["steps"]); st = st if len(st) <= step_chars else st[:step_chars] + " ..."
        L.append(f"- video {stem} ({x['duration']:g} s), trace {x['trace']}, claims {json.dumps(x['claims'], ensure_ascii=False)}\n"
                 f"  contact sheets (a tile every {SHEET_STEP} s, the time in the top left corner): {', '.join(x['sheets']) or 'none'}\n"
                 f"  steps (approximate start times): {st or '-'}")
    return "\n".join(L)


def frames(V, seg, out):
    """frames (jpg, 960 px) of a segment {video, t0, t1}, at most SEG_FRAMES at up to 2 fps -> [(t, path)]"""
    x = V.get(seg["video"])
    if not x: return []
    t0 = max(0.0, float(seg["t0"]) - PAD_BEFORE)
    t1 = min(float(seg["t1"]) + PAD_AFTER, x["duration"] or float(seg["t1"]) + PAD_AFTER, t0 + SEG_MAX_S)
    if t1 <= t0: t1 = min(t0 + 4, x["duration"] or t0 + 4)
    n = max(2, min(SEG_FRAMES, int((t1 - t0) * 2) + 1)); dt = (t1 - t0) / (n - 1)
    d = Path(out) / "frames" / f"{seg['video']}_{t0:06.1f}-{t1:06.1f}"; d.mkdir(parents=True, exist_ok=True); got = []
    for i in range(n):
        t = t0 + i * dt; p = d / f"t{t:06.1f}s.jpg"
        if not p.exists():
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", x["video"], "-frames:v", "1", "-vf",
                            "scale=960:-2", "-q:v", "4", str(p)], capture_output=True, timeout=120)
        if p.exists(): got.append((round(t, 1), str(p)))
    return got


# ---------------------------------------------------------------- 1. locate
LOCATE = """You are the EVIDENCE LOCATOR of a game-modding benchmark (editing track). {intro}

WE replayed each player-input trace of the changed build and recorded the screen. Below, per recording: its contact sheets
(one tile every {step} s; the time of each tile is written in its top left corner; read the sheets with the Read tool), the
trace's steps with their approximate start times, and the developer's claims. Your only job: for every item below, find the
time segment(s) of the recordings where the evidence that decides the item should be visible (the moment a mechanic acts and
its result, the view of the changed object, the place the item names), so that a verifier can look at dense frames of them.
Use the steps' times and the claims to find candidate moments, then ALWAYS confirm them on the sheets: the step times are
estimates, and a moment often happens during a neighbouring step (e.g. a fall during the walk before the step whose shot names
it); move the segment to where the sheets show it. Give 1-3 segments per item,
each 4-{segmax} s long, as tight as you can; an item that no recording reaches gets an empty list (say why in "note").

ITEMS:
{items}

RECORDINGS:
{videos}

Answer with ONLY a JSON object: {{"items": [{{"id": "ES1", "segments": [{{"video": "<recording name>", "t0": 140, "t1": 152}}], "note": "<= 25 words"}}, ...]}}"""


def locate(et, F, V, out, cost, intro):
    if not F or not V: return {f["id"]: [] for f in F}
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    ids = [f["id"] for f in F]
    p = LOCATE.format(intro=intro, step=SHEET_STEP, segmax=SEG_MAX_S, items=items_text(et, F), videos=videos_text(V))
    def chk(txt, rec):
        data = functions.parsed(txt); got = {}
        for i in (data or {}).get("items") or []:
            if isinstance(i, dict) and i.get("id") in ids and isinstance(i.get("segments"), list):
                segs = [s for s in i["segments"] if isinstance(s, dict) and s.get("video") in V and _num(s.get("t0")) is not None and _num(s.get("t1")) is not None]
                got[i["id"]] = {"segments": [{"video": s["video"], "t0": _num(s["t0"]), "t1": _num(s["t1"])} for s in segs][:3], "note": i.get("note")}
        miss = [x for x in ids if x not in got]
        return got, (None if not miss else f"items missing: {', '.join(miss)}")
    run1 = lambda n: claude(p, out, "locator" + ("" if n == 1 else f".try{n}"), tools=("Read",), allowed=("Read",), add_dirs=(out,), max_turns=80, cwd=out)
    got, bad = judge.run(cost, "edit_locator", run1, chk)
    if bad: log(f"{et.id}: locator: {bad} (items without segments are scored from the screenshots only)")
    return {i: (got.get(i) or {"segments": [], "note": f"locator failed: {bad}"}) for i in ids}


def _num(x):
    try: return round(float(x), 1)
    except (TypeError, ValueError): return None


def _frames_for(V, loc, F, out, budget=CALL_FRAMES):
    """item -> [(segment, [(t, path)])] for the items F, within a frame budget for the call"""
    res, used = {}, 0
    for f in F:
        res[f["id"]] = []
        for s in (loc.get(f["id"]) or {}).get("segments") or []:
            if used >= budget: break
            fr = frames(V, s, out); fr = fr[:max(0, budget - used)]; used += len(fr)
            if fr: res[f["id"]].append((s, fr))
    return res


def _frames_text(fr, root):
    L = []
    for i, segs in fr.items():
        if not segs: L.append(f"- {i}: no segment located (score from the screenshots; 0 for 'not shown' is allowed only now)"); continue
        for s, xs in segs:
            L.append(f"- {i}: video {s['video']} {s['t0']:g}-{s['t1']:g} s: " + ", ".join(f"{Path(p).relative_to(root)} (t={t:g}s)" for t, p in xs))
    return "\n".join(L)


# ---------------------------------------------------------------- 2. score
RULE = """LOCATED SEGMENTS: an evidence search over the replay recordings named, for each item, the time segments where its evidence
should be visible; their frames (up to 2 per second, the time in the file name) are listed under LOCATED FRAMES. Look at an
item's frames first, then the screenshots. An item may score 0 because something is "not shown" only when neither its located
frames nor the screenshots show it. All items (EDIT items ES / ESQ and PRESERVATION items P) use the three levels
0 / 0.5 / 1. In every verdict list the segments you used as "segments": [{"video": ..., "t0": ..., "t1": ...}]."""

REPLAY2 = """You are the REPLAY VERIFIER of a game-modding benchmark (editing track). {intro}

WE replayed each player-input trace of the changed build with real input on a clean install, recorded the screen and captured
the screenshots and the game's structured state listed below. Score each item with its anchors, using ONLY these captures
(read the images with the Read tool). EDIT items (ES) ask whether the change was made as asked; PRESERVATION items (P) ask
whether something the project had before the change still works or is still there. A claim the captures do not show counts
for nothing; set "could_not_test": true only if the replay itself broke (e.g. the game did not start).
{rule}
{note}
{staterule}

ITEMS:
{items}

LOCATED FRAMES:
{frames}

REPLAYS (AFTER: the changed build):
{replays}
{before}
Answer with ONLY a JSON object: {{"items": [{{"id": "ES1", "score": 0.5, "could_not_test": false, "segments": [{{"video": "...", "t0": 0, "t1": 0}}], "evidence": ["<frame or screenshot>: what it shows"], "reason": "<= 40 words"}}, ...]}}"""

VISUAL2 = """You are the VISUAL VERIFIER of a game-modding benchmark (editing track). {intro}

For each item, find the candidate screenshot(s) and located frames of the changed build that best show what it asks about, then
score it with its anchors (read the images with the Read tool). EDIT items ask whether the change was made as asked and looks
like part of {game}; ESQ judges the completion quality of the changed content; PRESERVATION items ask whether something the
project had before still looks as it did. Our replay screenshots and frames show the changed build as it actually runs; the
developer's screenshots may be outdated: where they disagree, ours decide.
{rule}
BEFORE pictures (the project before the change) are the reference for what changed and for the game's own style; score only
what the AFTER material shows.

ITEMS:
{items}

LOCATED FRAMES:
{frames}

CANDIDATE SCREENSHOTS (AFTER: the changed build):
{candidates}
{before}
Answer with ONLY a JSON object: {{"items": [{{"id": "ES1", "score": 0.5, "best": ["<candidate or frame path>"], "segments": [{{"video": "...", "t0": 0, "t1": 0}}], "reason": "<= 40 words"}}, ...]}}"""


def judge_all_v2(et, F, dv, R, out, cost, before=None):
    """v2 judging of the ES (+ ESQ) and P items -> {id: verdict} with evidence segments. out = the evaluation's functions dir"""
    from .edit import INTRO, BEFORE_NOTE, _replays_text, _cands
    from ..game import base as gbase
    out = Path(out); w = out / "v2"; w.mkdir(parents=True, exist_ok=True)
    intro = INTRO.format(game=gbase.title(et.game), parent=et.parent_instruction(), edit=et.edit_instruction())
    V = videos(R, out, w)
    loc = locate(et, [f for f in F if f["id"] != QUALITY_ID], V, w, cost, intro)   # w holds the sheets
    vis = [f for f in F if f.get("judge") == "visual"]; ply = [f for f in F if f.get("judge") != "visual"]
    v = {}
    if ply:                                                            # replay verifier (its folder holds the replays)
        rd = out / "replay"; fr = _frames_for(V, loc, ply, rd / "v2")
        bt = ("\nBEFORE (the project before the change; comparison only):\n" + _replays_text(before[2], 3000) + "\n") if before else ""
        pr = lambda FF: REPLAY2.format(intro=intro, rule=RULE, note=BEFORE_NOTE.format(before=rd / "before") if before else "",
                                       staterule=functions.STATE_RULE, items=items_text(et, FF),
                                       frames=_frames_text({k: fr[k] for k in fr if k in {f["id"] for f in FF}}, rd),
                                       replays=_replays_text(R), before=bt)
        run1 = lambda p, name: claude(p, rd, name, tools=("Read",), allowed=("Read",), add_dirs=(rd,), max_turns=160, cwd=rd, timeout=2400)
        fu = functions.followup_call("edit_replay_verifier_v2", lambda miss, name: run1(pr([f for f in ply if f["id"] in miss]), name), functions.json_judge)
        got, bad = judge.run(cost, "edit_replay_verifier_v2", lambda n: run1(pr(ply), "edit_replay_verifier_v2" + ("" if n == 1 else f".try{n}")),
                             functions.json_judge([f["id"] for f in ply]), followup=fu)
        for f in ply: v[f["id"]] = _with_segments(got.get(f["id"]) or judge.placeholder("edit_replay_verifier_v2", bad), loc.get(f["id"]))
    if vis:
        vd = out / "visual_v2"; vd.mkdir(parents=True, exist_ok=True)
        C = _cands(dv, R, vd, stale=(Path(before[0]) / "shots") if before else None)
        B = _cands(before[0], before[1], vd, sub="before", max_replay=4) if before else []
        loc_q = {**loc, QUALITY_ID: {"segments": [s for f in vis if f["id"] != QUALITY_ID for s in (loc.get(f["id"]) or {}).get("segments") or []][:2]}}
        fr = _frames_for(V, loc_q, vis, vd)
        bt = ("\nBEFORE (the project before the change; comparison and style reference):\n" + "\n".join(B) + "\n") if B else ""
        pr = lambda FF: VISUAL2.format(intro=intro, game=gbase.title(et.game), rule=RULE, items=items_text(et, FF),
                                       frames=_frames_text({k: fr[k] for k in fr if k in {f["id"] for f in FF}}, vd),
                                       candidates="\n".join(C) or "(none)", before=bt)
        run1 = lambda p, name: claude(p, vd, name, tools=("Read",), allowed=("Read",), add_dirs=(vd,), max_turns=160, cwd=vd, timeout=2400)
        fu = functions.followup_call("edit_visual_judge_v2", lambda miss, name: run1(pr([f for f in vis if f["id"] in miss]), name),
                                     lambda miss: functions.json_judge(miss, need_score=True))
        got, bad = judge.run(cost, "edit_visual_judge_v2", lambda n: run1(pr(vis), "edit_visual_judge_v2" + ("" if n == 1 else f".try{n}")),
                             functions.json_judge([f["id"] for f in vis], need_score=True), followup=fu)
        for f in vis: v[f["id"]] = _with_segments(got.get(f["id"]) or judge.placeholder("edit_visual_judge_v2", bad), loc_q.get(f["id"]))
    (w / "located.json").write_text(json.dumps(loc, ensure_ascii=False, indent=1))
    return v


def _with_segments(x, located):
    """the verdict + evidence.segments: the segments the judge says it used, else the located ones"""
    x = dict(x); used = [s for s in x.get("segments") or [] if isinstance(s, dict) and s.get("video")]
    x["located"] = (located or {}).get("segments") or []
    x["segments"] = used or x["located"]
    if (located or {}).get("note"): x["locator_note"] = located["note"]
    return x

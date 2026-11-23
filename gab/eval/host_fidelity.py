"""Host fidelity: a separately judged score 1 / 0.5 / 0 -- is the adapted experience BUILT ON and PLAYED THROUGH the host game
(its world / engine rendering, avatar / controls / camera, its own systems reused as building blocks, its look), or a
mod-drawn replacement that bypasses the host and merely boots inside it? The host's genre activities are NOT required (tasks
ask for a different experience built in the host). It is P = Preservation in Overall (gab/eval/scoring.py: 0.3 CR + 0.3 FP +
0.3 VP + 0.1 P; `gab eval` runs this judge after CR / FP / VP unless config host_fidelity_in_eval is off). Judged from saved
evidence, no game:

  replay captures (eval/functions/replay/<trace>/: screenshots + state/ snapshots), the play verifier's record
  (eval/functions/play/actions.jsonl + shots/ with <shot>.state.json) and the task's developer instruction
  (work/task/instruction_fine.md; not the reference media) are copied to fine/eval@host/, plus ONE vanilla screenshot of the host
  (style / perspective reference only); a judge (config judge_model, Read restricted to that copy) marks the game's five
  identity features ID1..ID5 yes / partial / no / not_applicable / unknown and scores the trial.

The checklist (per game: ID1 played in the host's world, driven by host gameplay; ID2 controls & perspective; ID3/ID4 host systems
reused as building blocks; ID5 look) and the shared scoring anchor are judge material in benchmark/host_fidelity/ (config
host_fidelity_dir, in every agent's deny list and in the leak audit, like the rubrics); the vanilla screenshots come from the
dataset (`gab media fetch`).

The official score is DERIVED from the feature verdicts by a fixed rule (RULE, rule_score()); the judge's holistic score is
kept as judge_score, a cross-check (flag rule_vs_judge_disagree). "insufficient" = fewer than 2 features marked yes / partial /
no (excluded from averages, counted). `--rescore-only` reapplies the rule to saved verdicts, no judge. A trial whose regression
gate did not pass gets status gate_failed and no score.

-> fine/host_fidelity.json (this module does not touch result.json / result@*.json; gab/eval/run.py add_preservation does).
With --tag T: the evidence of a tagged evaluation (result@T.json, eval@T/) -> fine/host_fidelity@T.json (judge copy in
fine/eval@host-T/).

  python -m gab.eval.host_fidelity --from-file LIST [--tag T] [--workers 4] [--force]
LIST: lines run/task (level fine)."""
import argparse, json, shutil, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import __version__, config
from ..util import claude, log, rjson, wjson
from . import functions, judge

USED = ("yes", "partial", "no", "not_applicable", "unknown")
NOT_JUDGEABLE = ("unknown", "not_applicable")
PLAY_CHARS, PLAY_ENTRY_CHARS = 9000, 400
MIN_SHOTS = 12

PROMPT = """You are the HOST-FIDELITY JUDGE of a game-modding benchmark. A developer agent modded the host game {title} ({game})
to make the experience a task asked for. You judge ONLY host fidelity: whether the adapted experience is built on and played
through {title}. You do not judge whether the task or its reference was realized well.

{anchor}

THE TASK (reference modality: {modality}; the reference itself is not given). The developer instruction, also in
{instruction_path}:
<<<
{instruction}
>>>
Use it only to know what experience was asked for, and so which host features are applicable.

IDENTITY FEATURES OF {game_uc} (mark each one used = yes / partial / no / not_applicable / unknown, as defined above: "no" only
when the experience actively replaces / bypasses that host layer; "not_applicable" when the task does not call for it; "unknown"
when the evidence never reaches it):
{features}
{notes}
VANILLA REFERENCE: {vanilla}

EVIDENCE (copies in this folder; there is no game now). Read the screenshots with the Read tool: open the vanilla screenshot and
at least {min_shots} of the evidence screenshots, spread over every replay and the play session (beginning, middle, end), to
know what the player spends the session on -- the timelines alone are not enough; open full state snapshots when a diff is
not enough. A developer's claim counts for nothing unless the captures show it.

1. REPLAYS of the developer's own traces (real input on a clean install of the mod):
{replays}

2. PLAY SESSION of an independent verifier on the same build (actions in order; the screenshot taken after each; full state in
<shot>.state.json next to it):
{play}

Answer with ONLY a JSON object:
{{"features": [{{"id": "{ex}", "used": "yes", "evidence": ["replay 01_x/003_y.png: what it shows", "play a012: ..."], "reason": "<= 30 words"}}, ... all five ...],
 "score": 1, "reason": "ONE line, <= 40 words: why this score", "screenshots_read": ["replay/01_x/003_y.png", "play/shots/a012.png", ...]}}
"score" is 1, 0.5, 0, or "insufficient" (fewer than two features judgeable: marked yes / partial / no)."""


def cut_record(play, out, k):
    """copy actions 1..k of a play record (and their screenshots / state snapshots) to out -> number of actions copied"""
    out.mkdir(parents=True, exist_ok=True); (out / "shots").mkdir(exist_ok=True)
    acts = [json.loads(l) for l in (play / "actions.jsonl").read_text().splitlines() if l.strip()][:k]
    lines = []
    for a in acts:
        s = a.get("shot")
        if s and Path(s).exists():
            shutil.copy(s, out / "shots" / Path(s).name)
            st = Path(s).with_suffix(".state.json")
            if st.exists(): shutil.copy(st, out / "shots" / st.name)
            a = {**a, "shot": str(out / "shots" / Path(s).name)}
        lines.append(json.dumps(a, ensure_ascii=False))
    (out / "actions.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))
    return len(acts)


def material_dir():
    return config.path("host_fidelity_dir")


def material(game, mdir=None):
    """-> (anchor text, the game's checklist entry, vanilla screenshot path or None, checklist version). The vanilla screenshot
    (optional) comes from <media_dir>/_host_fidelity/vanilla/<game>.png (`gab media fetch`), else <material dir>/vanilla/"""
    mdir = Path(mdir or material_dir()); cl = json.loads((mdir / "checklist.json").read_text())
    if game not in cl["games"]: raise KeyError(f"host fidelity: no checklist for game {game}")
    v = next((p for p in (config.path("media_dir") / "_host_fidelity" / "vanilla" / f"{game}.png", mdir / "vanilla" / f"{game}.png")
              if p.exists()), None)
    return (mdir / "anchor.md").read_text().strip(), cl["games"][game], v, cl.get("version")


def copy_evidence(ev, out):
    """the saved captures of an evaluation (ev = fine/eval) -> copies under out (replay/<trace>/, play/); -> summary dict with the
    copied replays (shots, timeline, state dir) and play actions. Nothing else of the evaluation (verdicts, notes, reference
    material, prompts) is copied."""
    ev, out = Path(ev), Path(out); fn = ev / "functions"
    R = []
    try: fj = json.loads((fn / "functions.json").read_text())
    except Exception: fj = {}
    for r in fj.get("replays") or []:
        stem = Path(r["trace"]).stem; src = fn / "replay" / stem; dst = out / "replay" / stem
        dst.mkdir(parents=True, exist_ok=True); shots = []
        for f in r.get("shots") or []:
            p = Path(f)
            if not p.exists(): p = src / p.name                   # saved under another root: same folder name
            if p.exists(): shutil.copy(p, dst / p.name); shots.append(str(dst / p.name))
        if (src / "state").is_dir() and not (dst / "state").exists(): shutil.copytree(src / "state", dst / "state")
        R.append({"trace": r["trace"], "ok": r.get("ok"), "claims": r.get("claims") or [], "shots": shots, "timeline": r.get("timeline") or [],
                  "state_dir": str(dst / "state") if (dst / "state").is_dir() else None})
    play = fn / "play"; acts = []
    if (play / "actions.jsonl").exists():
        cut_record(play, out / "play", 10 ** 9)
        acts = [json.loads(l) for l in (out / "play" / "actions.jsonl").read_text().splitlines() if l.strip()]
    return {"replays": R, "play": acts,
            "counts": {"replay_traces": len(R), "replay_shots": sum(len(r["shots"]) for r in R),
                       "replay_states": sum(len(list(Path(r["state_dir"]).glob("*.json"))) for r in R if r["state_dir"]),
                       "play_actions": len(acts), "play_shots": sum(1 for a in acts if a.get("shot") and Path(a["shot"]).exists())}}


def play_text(acts, limit=PLAY_CHARS):
    from ..game.base import diff_brief
    L = [f"    a{a.get('n', '?'):>03} {a.get('cmd')} {' '.join(map(str, a.get('args') or []))[:80]}"
         f" -> {Path(a['shot']).name if a.get('shot') else '(no screenshot)'} | state: {diff_brief(a.get('state_diff'), PLAY_ENTRY_CHARS)}"
         for a in acts]
    s = "\n".join(L)
    if len(s) <= limit: return s
    head, tail = [], []
    for x in L:
        if sum(map(len, head)) + len(x) > limit * 0.6: break
        head.append(x)
    for x in reversed(L[len(head):]):
        if sum(map(len, tail)) + len(x) > limit * 0.35: break
        tail.insert(0, x)
    return "\n".join(head + [f"    ... {len(L) - len(head) - len(tail)} actions left out of this list (all in play/actions.jsonl) ..."] + tail)


def prompt(game, mat, E, out, vanilla_copy, instruction=None, modality=None):
    anchor, g, _, _ = mat
    feats = "\n".join(f"- {f['id']}: {f['trait']}\n  Evidence: {f['evidence']}" for f in g["features"])
    notes = "".join(f"Note: {n}\n" for n in g.get("notes") or [])
    rep = []
    for r in E["replays"]:
        rep.append(f"- trace {r['trace']}: replay {'ran' if r.get('ok') else 'FAILED TO RUN (an evaluation fault, not evidence against the mod)'}; "
                   f"claims {json.dumps(r['claims'], ensure_ascii=False)[:600]}\n"
                   f"  folder: {Path(out) / 'replay' / Path(r['trace']).stem} ({len(r['shots'])} screenshots"
                   + (f"; full state snapshots in {r['state_dir']}" if r['state_dir'] else "") + ")\n"
                   f"  timeline (screenshot | input since the previous one | state diff):\n{functions.timeline_text(r['timeline']) or '    (none)'}")
    van = (f"{vanilla_copy} -- one stock, unmodded in-game screenshot of {g['title']} (what the host looks like; not a target)"
           if vanilla_copy else "none available for this game")
    return PROMPT.format(title=g["title"], game=game, game_uc=game.upper(), anchor=anchor, features=feats, notes=notes, vanilla=van,
                         replays="\n".join(rep) or "  (no replays saved)",
                         play=(f"  folder: {Path(out) / 'play'}\n" + play_text(E["play"])) if E["play"] else "  (no play session saved)",
                         modality=modality or "unknown", instruction_path=Path(out) / "instruction_fine.md",
                         instruction=(instruction or "(no instruction saved)").strip()[:6000], ex=g["features"][0]["id"], min_shots=min(MIN_SHOTS, E["counts"]["replay_shots"] + E["counts"]["play_shots"]))


def norm_score(s):
    if isinstance(s, str):
        if s.strip().lower().startswith("insufficient"): return "insufficient"
        try: s = float(s)
        except ValueError: return None
    if isinstance(s, (int, float)) and float(s) in (0.0, 0.5, 1.0): return float(s)
    return None


def check(ids):
    """judge.run check: (answer text, record) -> ({"verdict": data} | {}, None | reason)"""
    def f(txt, rec):
        data = functions.parsed(txt)
        if not isinstance(data, dict):
            return {}, ("timeout" if rec.get("timeout") else f"is_error: {str(rec.get('answer'))[:200]}" if rec.get("is_error") else "no parseable JSON")
        fs = {str(x.get("id")): x for x in data.get("features") or [] if isinstance(x, dict)}
        miss = [i for i in ids if i not in fs or str(fs[i].get("used", "")).lower().replace("not applicable", "not_applicable") not in USED]
        if miss: return {}, f"features missing or without a valid 'used': {', '.join(miss)}"
        if norm_score(data.get("score")) is None: return {}, f"invalid score {data.get('score')!r}"
        return {"verdict": data}, None
    return f


RULE = ("official score = rule over the feature verdicts; not_applicable / unknown ignored, J = features marked "
        "yes/partial/no: |J| < 2 -> insufficient; ID1 no or ID2 no or no 'yes' in J -> 0; any 'no' in J or #partial > |J|/2 -> 0.5; else 1")


def rule_score(used, ids):
    """used {id: yes|partial|no|not_applicable|unknown} -> 1.0 | 0.5 | 0.0 | "insufficient" (the official score; RULE)"""
    J = [used[i] for i in ids if used[i] not in NOT_JUDGEABLE]
    if len(J) < 2: return "insufficient"
    if used[ids[0]] == "no" or used[ids[1]] == "no" or "yes" not in J: return 0.0
    if "no" in J or J.count("partial") > len(J) / 2: return 0.5
    return 1.0


def finalize(data, ids):
    """the judge's verdict -> (features, official score | "insufficient", flags). The official score is rule_score() of the
    feature verdicts; the judge's holistic score is kept as a cross-check (flag rule_vs_judge_disagree when they differ)"""
    fs = {str(x["id"]): x for x in data["features"]}
    feats = [{"id": i, "used": str(fs[i].get("used")).lower().replace("not applicable", "not_applicable"), "evidence": fs[i].get("evidence") or [], "reason": fs[i].get("reason") or ""} for i in ids]
    s = rule_score({f["id"]: f["used"] for f in feats}, ids); js = norm_score(data.get("score"))
    return feats, s, ([f"rule_vs_judge_disagree: rule {s}, judge {js}"] if s != js else [])


def apply_rule(res, ids=None):
    """a host_fidelity.json record with feature verdicts -> the record rescored by RULE (no judge call)"""
    if not res.get("features"): return res
    ids = ids or [f["id"] for f in res["features"]]
    _, s, flags = finalize({"features": res["features"], "score": res.get("judge_score")}, ids)
    return {**res, "status": "insufficient" if s == "insufficient" else "scored", "score": None if s == "insufficient" else s,
            "flags": flags, "rule_vs_judge_disagree": bool(flags), "rule": RULE}


def paths(d, tag=None):
    """-> (source result, source eval dir, output record, judge copy dir) of the main evaluation or of eval tag `tag`"""
    d = Path(d)
    if not tag: return d / "result.json", d / "eval", d / "host_fidelity.json", d / "eval@host"
    from .run import eval_tag
    tag = eval_tag(tag)
    return d / f"result@{tag}.json", d / f"eval@{tag}", d / f"host_fidelity@{tag}.json", d / f"eval@host-{tag}"


def run_trial(d, task_id, game, force=False, mdir=None, call=None, modality=None, tag=None):
    """one trial dir d (runs/<run>/<task>/fine) -> the host_fidelity.json record (with tag: from result@tag.json + eval@tag/ ->
    host_fidelity@tag.json). call(prompt, out, name) -> (text, record) replaces the claude CLI (tests)"""
    d = Path(d); src_r, src_e, hf, out = paths(d, tag)
    if hf.exists() and not force: return rjson(hf)
    trial = f"{d.parent.parent.name}/{d.parent.name}"
    r = rjson(src_r); tj = rjson(d / "trial.json") or {}
    base = {"trial": trial, "task": task_id, "game": game, "model": tj.get("model"), "agent": tj.get("agent"),
            "overall": ((r or {}).get("scores") or {}).get("overall_cfv", ((r or {}).get("scores") or {}).get("overall")), "gab": __version__, "judge_model": config.get("judge_model"),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "eval_tag": tag}
    if not r or r.get("status") in ("infra_error", "error"):
        res = {**base, "status": "no_result", "score": None, "reason": f"no scored {src_r.name} ({(r or {}).get('status')})"}
        wjson(hf, res); return res
    gate = r.get("gate") or {}
    if gate.get("pass") is not True:
        res = {**base, "status": "gate_failed", "score": None, "reason": f"regression gate not passed: {gate.get('fail') or gate.get('reason')}"}
        wjson(hf, res); log(f"{trial}: host fidelity: gate failed, no score"); return res
    mat = material(game, mdir); ids = [f["id"] for f in mat[1]["features"]]
    shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    t0 = time.time(); E = copy_evidence(src_e, out)
    van = None
    if mat[2]: van = out / f"vanilla_{game}.png"; shutil.copy(mat[2], van)
    ins = d / "work" / "task" / "instruction_fine.md"; instruction = None
    if ins.exists(): shutil.copy(ins, out / "instruction_fine.md"); instruction = ins.read_text(errors="replace")
    p = prompt(game, mat, E, out, van, instruction, modality); cost = {}; cached = [0]
    def run1(n):
        name = "host_judge" + ("" if n == 1 else f".try{n}")
        if call: txt, rec = call(p, out, name)
        else:
            txt, rec = claude(p, out, name, tools=("Read",), allowed=("Read",), add_dirs=(out,), max_turns=80, cwd=out,
                              extra=["--disallowedTools", *[f"Read(/{x}/**)" for x in config.get("agent_deny_read")]])
        cached[0] += rec.get("tokens_cached") or 0
        return txt, rec
    got, bad = judge.run(cost, "host_judge", run1, check(ids))
    wall = round(time.time() - t0, 1)
    c = {"wall_s": wall, "judge_wall_s": round(cost.get("wall_s", 0), 1), "tokens_in": cost.get("tokens_in", 0),
         "tokens_out": cost.get("tokens_out", 0), "tokens_cached": cached[0], "usd": cost.get("usd", 0)}
    ev = {**E["counts"], "vanilla": van is not None, "instruction": instruction is not None, "modality": modality}
    if bad:
        res = {**base, "status": "judge_failed", "score": None, "reason": bad, "evidence": ev, "judges": judge.summary(cost), "cost": c}
        wjson(hf, res); log(f"{trial}: host fidelity: JUDGE FAILED ({bad})"); return res
    data = got["verdict"]; feats, s, flags = finalize(data, ids)
    res = {**base, "status": "insufficient" if s == "insufficient" else "scored", "score": None if s == "insufficient" else s,
           "judge_score": norm_score(data.get("score")), "reason": data.get("reason") or "", "flags": flags, "rule_vs_judge_disagree": bool(flags),
           "rule": RULE, "features": feats, "screenshots_read": len(data.get("screenshots_read") or []), "checklist_version": mat[3], "evidence": ev, "judges": judge.summary(cost), "cost": c}
    wjson(hf, res); log(f"{trial}: host fidelity {s} ({wall:.0f}s, ${c['usd']})")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-file", required=True); ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--tag", help="judge the evidence of eval tag T (result@T.json, eval@T/) -> host_fidelity@T.json")
    ap.add_argument("--rescore-only", action="store_true", help="recompute the official score from the saved feature verdicts (no judge)")
    a = ap.parse_args(argv)
    if a.rescore_only:
        runs = Path(config.get("runs_dir"))
        for j in [l.strip() for l in Path(a.from_file).read_text().splitlines() if l.strip() and not l.startswith("#")]:
            f = paths(runs / j / "fine", a.tag)[2]; r = rjson(f)
            if not r or not r.get("features"): log(f"{j}: rescore: no feature verdicts ({(r or {}).get('status')})"); continue
            n = {**apply_rule(r), "rescored_at": time.strftime("%Y-%m-%dT%H:%M:%S")}; wjson(f, n)
            log(f"{j}: rescore {r.get('score')} -> {n['score']} (judge {n.get('judge_score')}{', DISAGREE' if n['rule_vs_judge_disagree'] else ''})")
        return
    from ..run.task import Task
    runs = Path(config.get("runs_dir"))
    jobs = [l.strip() for l in Path(a.from_file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    def one(j):
        try:
            tid = j.split("/")[1]; T = Task(tid); run_trial(runs / j / "fine", tid, T.game, a.force, modality=T.modality, tag=a.tag)
        except Exception as e: log(f"{j}: host fidelity failed: {type(e).__name__}: {e}")
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(one, jobs))


if __name__ == "__main__":
    main()

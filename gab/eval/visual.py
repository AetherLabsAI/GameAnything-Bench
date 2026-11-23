"""Visual & Presentation column: the visual judge (docs/evaluation.md, rubric v4). Per item, the reference it is judged against
(copied into the trial's private eval dir): the keyframes of the reference clip it links, the reference images its evidence
names, the passages of the reference text its evidence cites (eval/evidence.py). Candidates: the deliverable's shots, our replay
captures and the play verifier's screenshots. The judge picks the best match per item and scores with the anchors, in the
target game's own style. Prompt adapted from an earlier video-to-game visual judge."""
import json, shutil, subprocess
from pathlib import Path

from ..util import claude, wjson
from . import judge
from .evidence import keyframes, resolve

PROMPT = """You are the VISUAL VERIFIER for a benchmark where an agent rebuilt the experience of a reference (a video, a text and / or
images) inside {game}. For each visual rubric item you get what it is judged against -- keyframes of the reference video and / or
reference images (files under ref/), and / or passages of the reference text that describe how something looks -- and a pool of
screenshots from the rebuilt game. Find the target screenshot(s) that best correspond to the item, then score 0 / 0.5 / 1 with the
anchors. Against pictures: judge whether the moment, its visual properties and its spatial composition carry over. Against a
passage: judge whether the screenshots show what the text describes visually (the look of the place, entity or moment, the
spatial relations it states). Always rendered in {game}'s own style: a different art style is expected and is not a fault. If no
screenshot shows the item, the score is 0. Read every reference picture and every candidate screenshot with the Read tool before
scoring.

VISUAL ITEMS:
{items}

CANDIDATE TARGET SCREENSHOTS:
{candidates}

Answer with ONLY a JSON object: {{"items": [{{"id": "VP1", "score": 0, "best": ["<candidate path>"], "reason": "<= 40 words"}}, ...]}}"""

MAX_PLAY = 16


def evaluate(task, game, dv, replays, play_dir, out, cost):
    out = Path(out); shutil.rmtree(out / "cand", ignore_errors=True); (out / "cand").mkdir(parents=True)
    V = task.items("visual_presentation"); cands = []; refs = {}
    def add(src, tag, what):
        dst = out / "cand" / f"{tag}.jpg"
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vf", "scale=960:-2", "-q:v", "4", str(dst)])
        if r.returncode == 0: cands.append(f"cand/{dst.name}  ({what})")
    for i, f in enumerate(sorted((Path(dv) / "shots").glob("*.png"))[:6]): add(f, f"dev_{i:02d}", f"developer's screenshot {f.name}")
    for r in replays or []:
        for j, s in enumerate(r.get("shots") or []): add(s, f"replay_{Path(r['trace']).stem}_{j:02d}",
                                                              f"our replay of trace {r['trace']}" + (" (automatic)" if "_auto" in Path(s).name else
                                                              " (end)" if Path(s).name == "zz_end.png" else " (the developer's shot step)"))
    ps = sorted((Path(play_dir) / "shots").glob("a*.png")) if play_dir and (Path(play_dir) / "shots").exists() else []
    step = max(1, len(ps) // MAX_PLAY)
    for s in ps[::step][:MAX_PLAY]: add(s, f"play_{s.stem}", f"play verifier screenshot, action {int(s.stem[1:])}")
    items = []
    for v in V:
        kfs = keyframes(task, v, out)
        ref, files = resolve(task, v, out, frames=not kfs)          # images + passages (+ clip frames when no keyframes are linked)
        items.append({"id": v["id"], "item": v["item"], "anchors": v["anchors"], "reference_keyframes": kfs,
                      "reference_evidence": ref or None})
        refs[v["id"]] = kfs + files
    if not V: wjson(out / "visual.json", {"n_candidates": len(cands), "items": []}); return []
    if not cands:
        res = [{"id": v["id"], "column": "visual_presentation", "score": 0.0, "could_not_test": False, "evidence": "no screenshots at all", "judge": None} for v in V]
        wjson(out / "visual.json", res); return res
    from .functions import followup_call, json_judge
    prompt = PROMPT.format(game=game, items=json.dumps(items, ensure_ascii=False, indent=1), candidates="\n".join(cands))
    model = []
    def call(n):
        txt, rec = claude(prompt, out, "visual_judge" + ("" if n == 1 else f".try{n}"), tools=("Read",), allowed=("Read",),
                          add_dirs=(out,), max_turns=150, cwd=out, timeout=2400)
        model.append(rec.get("model")); return txt, rec
    def more(miss, name):     # follow-up: only the items the verdict lacked
        sub = [x for x in items if x["id"] in miss]
        txt, rec = claude(PROMPT.format(game=game, items=json.dumps(sub, ensure_ascii=False, indent=1), candidates="\n".join(cands)), out, name,
                          tools=("Read",), allowed=("Read",), add_dirs=(out,), max_turns=150, cwd=out, timeout=2400)
        model.append(rec.get("model")); return txt, rec
    got, bad = judge.run(cost, "visual_judge", call, json_judge([v["id"] for v in V], need_score=True),
                         followup=followup_call("visual_judge", more, lambda miss: json_judge(miss, need_score=True)))
    # a judge that failed for good: its items carry judge_failed, the trial becomes infra_error judge_failed (not scored)
    res = [{"id": v["id"], "column": "visual_presentation", "score": float(got[v["id"]]["score"]) if v["id"] in got else 0.0,
            "could_not_test": v["id"] not in got, "evidence": got.get(v["id"]) or judge.placeholder("visual_judge", bad),
            "reference_files": refs.get(v["id"]), "judge": model[-1] if model else None} for v in V]
    wjson(out / "visual.json", {"n_candidates": len(cands), "items": res})
    return res

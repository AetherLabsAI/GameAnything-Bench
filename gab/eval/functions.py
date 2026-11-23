"""Content Realization + Functions & Playability columns (docs/evaluation.md, rubric v4): two independent verifiers on a fresh eval
instance score the CR items (is the content there, in the right relation: decided by finding / seeing it) and the FP items
(does it work: decided by acting and watching the result), then arbitration. Every item comes with its reference evidence
(eval/evidence.py: quoted passages of the text, reference images, frames of the clip) so the verifiers know what to look for.
  replay verifier: we replay the deliverable's traces (real input) and capture screenshots + state; an LLM judge scores the CR / FP
                   items from those captures and the trace claims (it cannot be fooled by the developer's own screenshots).
                   State (docs/STATE_SPEC.md) is read live at every screenshot point; the judge gets the timeline of diffs.
  play verifier:   a Claude Code agent plays the build through `play` (player input only, a screenshot and the state diff after
                   every action, an action budget). Phase 1 blind (instruction + CR / FP items); `play phase2` unlocks ACCESS.md, the
                   traces and the rubric's play_suggestions. Prompt adapted from an earlier video-to-game play verifier.
  arbitration:     equal scores stand; if one verifier could not test, the other stands; otherwise an arbiter call sees both
                   verdicts (reasons + evidence) and picks the score.
Every judge call is checked and retried once (eval/judge.py); a verifier / arbiter that still fails gives no scores: its items are
flagged judge_failed (never "one verifier only") and the trial becomes infra_error judge_failed. A verdict that only lacks some
items first gets one follow-up call for those items alone (no new play session), then the full retry.
Evaluation modes (config `evidence`, env GAB_EVIDENCE; `gab eval --lite`):
  full                the protocol above (default): replay verifier + play verifier + adjudication
  lite                = shots+state+replay: the replay verifier alone (no play verifier, no arbitration; it does not see the
                      developer's screenshots). Cheaper and faster, a little harsher than Full.
Other values reproduce the paper's evidence ablation (what the CR / FP judging sees): shots (the developer's screenshots + the
trace claims), shots+state, shots+replay, shots+replay+state, replay-shots (our replays' screenshots only), replay-state (our
replays' state timelines only)."""
import json, os, shutil, time
from pathlib import Path

import yaml

from .. import config
from ..util import claude, log, loose_json, wjson
from . import judge
from .evidence import resolve

COLUMN = {"CR": "content_realization", "FP": "functions_playability"}

PLAY_ACTIONS, PLAY_MINUTES = 40, 30          # play verifier budget; defaults of config play_actions / play_minutes


LITE = "shots+state+replay"                  # `lite`: the replay verifier alone
MODES = ("shots", "shots+state", "shots+replay", "shots+replay+state", "replay-shots", "replay-state", "shots+state+replay", "full")
REPLAY_PART_MODES = {"replay-shots": (False, True), "replay-state": (True, False)}   # (state, replay screenshots)
SHOTS_MODES = {"shots": (False, False), "shots+state": (True, False), "shots+replay": (False, True), "shots+replay+state": (True, True)}   # (state, replay screenshots)


def evidence_mode(mode=None):
    """the CR / FP evidence mode: argument, else config `evidence` (env GAB_EVIDENCE), default full"""
    m = (mode or config.get("evidence") or "full").strip()
    if m == "lite": m = LITE
    if m not in MODES: raise ValueError(f"evidence mode {m!r}: one of {', '.join(MODES)}")
    return m


def play_budget():
    """-> (actions, minutes) of the play verifier: config `play_actions` / `play_minutes` (env GAB_PLAY_ACTIONS / GAB_PLAY_MINUTES)"""
    a, m = config.get("play_actions"), config.get("play_minutes")
    return int(PLAY_ACTIONS if a in (None, "") else a), float(PLAY_MINUTES if m in (None, "") else m)

STATE_RULE = """Evidence = screenshots + state changes. For mechanics / narrative items, a 1 needs an interaction followed by a
matching state change that is consistent with the screenshots (e.g. pressing use near a door -> the door's state flips and the
next screenshot shows it open; talking to an NPC -> a quest variable is set and its dialog text is on screen). Screenshots
alone are usually not full marks for such items. Judge each item on its own, do not apply this mechanically: a purely visual
item may need no state at all, and a state change that the screenshots contradict proves nothing. State values are evidence;
names of variables and entities are chosen by the mod, so interpret them with care (a variable called "quest_done" proves
nothing by its name; its change right after the right action does)."""

REPLAY = """You are the REPLAY VERIFIER of a game benchmark. A developer agent built a mod for {game} that should recreate the
experience of a reference (a video, a text and / or images). The developer handed in player-input traces with claims of what
they show. WE replayed each trace with real input on a clean install of the build and captured the screenshots listed below; at
every screenshot we also read the game's structured state from the running game (engine fields, what mods created inside the
engine, on-screen text). Score each item 1 / 0.5 / 0 with its anchors, using ONLY what these captures show (read every
screenshot with the Read tool). CONTENT items (CR) ask whether the content the reference requires is present in the game and in
the right relation (entities and what tells them apart, places and how they connect, roles, events and their order): decided by
finding / seeing it. FUNCTIONAL items (FP) ask whether it works: decided by an action and the result that follows.
{refnote} A claim the captures do not show counts for nothing. If the captures cannot show an item at all, score it 0 and
say so; set "could_not_test": true only if the replay itself broke (e.g. the game did not start).

The timeline of each replay lists, per screenshot, the input since the previous one and the state diff (changed old -> new,
+added, -removed). Full snapshots are in each replay folder (state/*.json) if you need more than a diff.
{rule}

ITEMS:
{items}

REPLAYS:
{replays}

Answer with ONLY a JSON object: {{"items": [{{"id": "CR1", "score": 0, "could_not_test": false, "evidence": ["<screenshot>: what it shows / which state change"], "reason": "<= 40 words"}}, ...]}}"""

PLAY = """You are an independent PLAY VERIFIER for a game-modding benchmark. A developer agent built a mod for {game} that should
recreate an experience described by a user from a reference (a video, a text and / or images). You have NOT seen the developer's
notes. You play the build like a human player, then score a checklist, using only your OWN play as evidence. CONTENT items (CR):
is the content there, in the right relation -- find it and look (walk through the places, inspect entities, follow the events).
FUNCTIONAL items (FP): does it work -- act and watch what happens.
{refnote}

How you play: only through the `play` command (run `play help` first). Every action is real player input with the game's
normal rules and returns a screenshot path (Read it, the image is your main evidence) and `state_diff`: how the game's
structured state (read from the running game) changed since your previous action. `play state` gives the full state.
`play start` / `play reset` give a fresh start. Use `play seq` for routine sequences. Budget: {actions} actions and {minutes}
minutes (`play budget`).
Run every `play` command as its own Bash call, exactly `play <command> [args]`: no `cd`, no `&&` / `;` chaining, no pipes, no
redirects (`2>&1`, `>`), no variables or subshells. Only such bare `play` commands are permitted; a denied command means it broke
this rule: run it again as a bare `play ...` command and go on (never stop because of a denial). Read files with Read, write
notes.md and verdict.json with Write.

{rule}

The user asked the developer for:
{instruction}

PHASE 1 (blind). Find the experience from a fresh start and play it as a player would. Write observations to notes.md as you
go (action numbers, what you saw). Use about half of your budget.
PHASE 2. Run `play phase2`: it unlocks phase2/ (the developer's ACCESS.md and traces, and test suggestions per item). Use them to
reach what you could not find, and to probe behaviour the traces do not show.

Then write verdict.json in your working directory and stop:
{{"items": [{{"id": "CR1", "score": 0, "could_not_test": false, "evidence": ["action 12: what the screenshot / state diff shows"], "reason": "<= 40 words"}}, ...],
  "summary": "<= 120 words"}}
Score EVERY item with its anchors. "could_not_test": true only when your own limits (tools, budget) kept you from testing an item
the build may well support; a build that cannot be started, entered or reached scores 0.

ITEMS (score 0 / 0.5 / 1 with the anchors):
{items}"""

ARBITER = """Two independent verifiers scored the same content / functional items of a game build differently. The replay verifier saw screenshots of
the developer's own input traces replayed by us; the play verifier played the build itself. Decide each item's final score (1 / 0.5 / 0)
with its anchors, from the evidence both give. Evidence of a working function from real play or a real replay outweighs its absence
in the other (the other may simply not have tried it), unless the other shows it breaking.

ITEMS AND VERDICTS:
{rows}

Answer with ONLY a JSON object: {{"items": [{{"id": "CR1", "score": 0, "reason": "<= 30 words"}}, ...]}}"""

TIMELINE_CHARS, TIMELINE_ENTRY_CHARS = 7000, 700


REFNOTE = """Each item lists the reference evidence it was written against (passages of the reference text, reference images and frames
of the reference video under ref/; Read the images). Use it to know what to look for: the game realises the content in its own
style and need not copy the reference literally; the anchors decide the score."""


SHOTS = """You are the EVIDENCE VERIFIER of a game benchmark. A developer agent built a mod for {game} that should recreate the
experience of a reference (a video, a text and / or images). The developer handed in the screenshots listed below and input traces
with claims of what they show. Score each item 1 / 0.5 / 0 with its anchors, using ONLY this evidence (read every screenshot with
the Read tool). CONTENT items (CR) ask whether the content the reference requires is present in the game and in the right relation
(entities and what tells them apart, places and how they connect, roles, events and their order): decided by finding / seeing it.
FUNCTIONAL items (FP) ask whether it works: decided by an action and the result that follows.
{refnote} A claim the evidence does not show counts for nothing. If the evidence cannot show an item at all, score it 0 and say so.
{statenote}
ITEMS:
{items}

DEVELOPER SCREENSHOTS:
{shots}

TRACE CLAIMS:
{claims}
{states}
Answer with ONLY a JSON object: {{"items": [{{"id": "CR1", "score": 0, "could_not_test": false, "evidence": ["<screenshot or state change>: what it shows"], "reason": "<= 40 words"}}, ...]}}"""

STATENOTE = """WE also replayed each trace with real input on a clean install of the build and read the game's structured state at every
snapshot (engine fields, what mods created inside the engine, on-screen text); the timelines below list, per snapshot, the input
since the previous one and the state diff (changed old -> new, +added, -removed). The replay's screenshots are not given.
"""
RSHOTNOTE = """WE also replayed each trace with real input on a clean install of the build and took a screenshot at every snapshot; the
timelines below list, per screenshot, the input since the previous one{state}. Read the replay screenshots with the Read tool too.
"""

FOLLOWUP = """FOLLOW-UP. Your previous answer did not score these items: {ids}. Score ONLY these items now, from the same evidence, with
the same rules, and answer with ONLY the JSON object for them."""

PLAY_FOLLOWUP = """You are the PLAY VERIFIER of a game-modding benchmark and you already played this build: your record is in this folder
(actions.jsonl = every `play` action with its screenshot path and state diff, shots/ = the screenshots, notes.md = your notes if
any, verdict.json = your verdict so far). The verdict did not score these items: {ids}. Do NOT play again (there is no game now):
score ONLY these items from what your recorded session shows, with the same rules and anchors. "could_not_test": true only if
your session never reached what the item needs although the build may support it.
{rule}

ITEMS:
{items}

Answer with ONLY a JSON object: {{"items": [{{"id": "CR1", "score": 0, "could_not_test": false, "evidence": ["action 12: what it shows"], "reason": "<= 40 words"}}, ...]}}"""


def followup_call(stage, make, ids_check):
    """judge.run followup: make(missing ids) -> (answer text, record); checked against only the missing ids"""
    def fu(miss, n):
        txt, rec = make(miss, f"{stage}.followup{'' if n == 1 else n}")
        got, reason = ids_check(miss)(txt, rec)
        return txt, rec, got, reason
    return fu


def items_text(F, task=None, out=None):
    """items for a prompt; with task + out, each item's reference evidence (files copied into out/ref/)"""
    L = []
    for f in F:
        s = (f"{f['id']} ({'content' if f['id'].startswith('CR') else 'functional'}{', joins references' if f.get('cross') else ''}): {f['item']}\n"
             f"  anchors: 1 = {f['anchors']['1']} | 0.5 = {f['anchors']['0.5']} | 0 = {f['anchors']['0']}")
        if task is not None and out is not None:
            ref, _ = resolve(task, f, out)
            if ref: s += f"\n  reference evidence:\n    {ref}"
        L.append(s)
    return "\n".join(L)


def scored_items(task):
    """the items these verifiers score: CR then FP"""
    return task.items("content_realization") + task.items("functions_playability")


def act_text(acts):
    """the steps since the previous snapshot -> short text"""
    if not acts: return "-"
    f = lambda a: " ".join(f"{k}={v}" for k, v in a.items() if k != "do")[:60]
    t = ", ".join(f"{a.get('do')} {f(a)}".strip() for a in acts[:6])
    return t + (f", ... ({len(acts)} steps)" if len(acts) > 6 else "")


def timeline_text(tl, limit=TIMELINE_CHARS, state=True):
    """replay timeline -> text lines for the judge: screenshot, input since the previous one, state diff (diffs only; the whole
    is cut at `limit` chars, keeping the start and the end)"""
    from ..game.base import diff_brief
    L = [f"    {e.get('screenshot') or '(no screenshot)'} after step {e.get('step')}: input {act_text(e.get('actions'))}"
         + (f" | state: {diff_brief(e.get('diff'), TIMELINE_ENTRY_CHARS)}" if state else "") for e in tl or []]
    s = "\n".join(L)
    if len(s) <= limit: return s
    head, tail = [], []
    for x in L:
        if sum(map(len, head)) + len(x) > limit * 0.6: break
        head.append(x)
    for x in reversed(L[len(head):]):
        if sum(map(len, tail)) + len(x) > limit * 0.35: break
        tail.insert(0, x)
    return "\n".join(head + [f"    ... {len(L) - len(head) - len(tail)} entries left out (full: replay.json) ..."] + tail)


def _replay_rec(ad, inst, tr, out, stem):
    """ad.replay, screen-recorded when config eval_record is set (same x11grab / H.264 path as gab/refine/refine.py record_replays):
    -> out/<stem>.mp4, r["video"]. Recording never changes the replay or its result; a recording failure only loses the video."""
    if str(config.get("eval_record") or "") not in ("1", "true", "yes"): return ad.replay(inst, tr, out / stem)
    import subprocess
    w, h = ad.screen; tmp = f"{ad.seat.home}/.gab_evid_{inst}_{stem}"[:200]
    if tr.get("start", "fresh") == "fresh":          # reset first (a fresh start restarts the display), then record (as record_replays)
        sok, sl = ad.reset(inst)
        if not sok: return {"ok": False, "log": [f"start failed: {str(sl)[-300:]}"], "shots": [], "timeline": []}
    try:
        ad._x(inst, f"rm -f {tmp}.mkv {tmp}.pid; nohup ffmpeg -loglevel error -y -f x11grab -framerate 8 -video_size {w}x{h} "
                    f"-i $DISPLAY -c:v mjpeg -q:v 5 {tmp}.mkv >/dev/null 2>&1 & echo $! > {tmp}.pid", timeout=30)
    except Exception: return ad.replay(inst, tr, out / stem, fresh=False)
    try: r = ad.replay(inst, tr, out / stem, fresh=False)
    finally:
        try:
            ad._x(inst, f"kill -INT $(cat {tmp}.pid) 2>/dev/null; sleep 2", timeout=30)
            raw = Path(out) / f"{stem}.mkv"; mp4 = Path(out) / f"{stem}.mp4"; Path(out).mkdir(parents=True, exist_ok=True)
            if ad.seat.copy_out(f"{tmp}.mkv", raw) and raw.exists():
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-c:v", "libx264", "-preset", "veryfast",
                                "-crf", "28", "-pix_fmt", "yuv420p", str(mp4)], capture_output=True)
                raw.unlink(missing_ok=True)
            ad.seat.exec(f"rm -f {tmp}.mkv {tmp}.pid")
        except Exception: pass
    mp4 = Path(out) / f"{stem}.mp4"
    return {**r, "video": str(mp4)} if mp4.exists() else r


def replays(ad, inst, dv, out):
    """replay every deliverable trace (at most 4) on the eval instance"""
    R = []
    for f in sorted((Path(dv) / "traces").glob("*.yaml"))[:4]:
        try: tr = yaml.safe_load(f.read_text()) or {}
        except Exception as e: R.append({"trace": f.name, "ok": False, "log": [f"not YAML: {e}"], "shots": [], "claims": []}); continue
        r = _replay_rec(ad, inst, tr, out, f.stem)
        if not r.get("ok") and "start failed" in str(r.get("log")):     # the game did not come up: an evaluation fault, retry once
            shutil.rmtree(out / f.stem, ignore_errors=True)            # (seen when several 3D trials share one machine)
            r = {**_replay_rec(ad, inst, tr, out, f.stem), "retried": True}
        R.append({"trace": f.name, "claims": tr.get("claims") or [], **r})
    try: ad.stop(inst)
    except Exception: pass
    return R


def replay_prompt(game, F, R, task=None, out=None):
    rep = []
    for r in R:
        rep.append(f"- trace {r['trace']}: claims {json.dumps(r['claims'], ensure_ascii=False)}; replay {'ran' if r.get('ok') else 'FAILED TO RUN (an evaluation fault, not evidence against the mod; an item that only this trace would show is could_not_test): ' + str(r.get('log'))[:200]}\n"
                   f"  folder: {Path(r['shots'][0]).parent if r.get('shots') else '-'}\n"
                   f"  screenshots: {', '.join(r.get('shots') or []) or 'none'}\n"
                   f"  timeline (screenshot | input since the previous one | state diff):\n{timeline_text(r.get('timeline')) or '    (none)'}\n"
                   f"  final state: {json.dumps(r.get('state'), ensure_ascii=False, default=str)[:800]}")
    return REPLAY.format(game=game, items=items_text(F, task, out), replays="\n".join(rep), rule=STATE_RULE, refnote=REFNOTE if task is not None else "")


REPLAY_PART = """You are the REPLAY VERIFIER of a game benchmark. A developer agent built a mod for {game} that should recreate the
experience of a reference (a video, a text and / or images). The developer handed in player-input traces with claims of what
they show. WE replayed each trace with real input on a clean install of the build and {captured}. Score each item 1 / 0.5 / 0
with its anchors, using ONLY what these captures show{readnote}. CONTENT items (CR) ask whether the content the reference requires
is present in the game and in the right relation (entities and what tells them apart, places and how they connect, roles, events
and their order): decided by finding / seeing it. FUNCTIONAL items (FP) ask whether it works: decided by an action and the result
that follows.
{refnote} A claim the captures do not show counts for nothing. If the captures cannot show an item at all, score it 0 and
say so; set "could_not_test": true only if the replay itself broke (e.g. the game did not start).

{timelinenote}
{rule}
ITEMS:
{items}

REPLAYS:
{replays}

Answer with ONLY a JSON object: {{"items": [{{"id": "CR1", "score": 0, "could_not_test": false, "evidence": ["<{unit}>: what it shows"], "reason": "<= 40 words"}}, ...]}}"""


def replay_part_prompt(game, F, R, out, task=None, state=True, rshots=False):
    """replay-shots / replay-state: our replays only, one kind of capture; the captures are copied under out/captures/<trace>/"""
    rep = []
    for r in R:
        src = Path(r["shots"][0]).parent if r.get("shots") else None; dst = Path(out) / "captures" / Path(r["trace"]).stem
        if src is not None and not dst.exists():
            dst.mkdir(parents=True, exist_ok=True)
            if rshots:
                for f in r["shots"]:
                    if Path(f).exists(): shutil.copy(f, dst / Path(f).name)
            if state and (src / "state").is_dir(): shutil.copytree(src / "state", dst / "state")
        tl = r.get("timeline") or [] if rshots else [{**e, "screenshot": f"snapshot {k}"} for k, e in enumerate(r.get("timeline") or [])]
        rep.append(f"- trace {r['trace']}: claims {json.dumps(r['claims'], ensure_ascii=False)}; replay {'ran' if r.get('ok') else 'FAILED TO RUN (an evaluation fault, not evidence against the mod; an item that only this trace would show is could_not_test): ' + str(r.get('log'))[:200]}\n"
                   + (f"  folder: {dst}\n  screenshots: {', '.join(str(dst / Path(f).name) for f in r.get('shots') or []) or 'none'}\n" if rshots else
                      f"  full state snapshots: {dst / 'state'}\n" if state and (dst / "state").is_dir() else "")
                   + f"  timeline ({'screenshot' if rshots else 'snapshot'} | input since the previous one{' | state diff' if state else ''}):\n{timeline_text(tl, state=state) or '    (none)'}"
                   + (f"\n  final state: {json.dumps(r.get('state'), ensure_ascii=False, default=str)[:800]}" if state else ""))
    if rshots:
        captured, readnote, unit = "took the screenshots listed below; no game state is given", " (read every screenshot with the Read tool)", "screenshot"
        tln = "The timeline of each replay lists, per screenshot, the input since the previous one."
    else:
        captured, readnote, unit = ("read the game's structured state from the running game at every snapshot (engine fields, what mods "
                                    "created inside the engine, on-screen text); the replay's screenshots are not given"), "", "state change"
        tln = ("The timeline of each replay lists, per snapshot, the input since the previous one and the state diff (changed old -> new, "
               "+added, -removed). Full snapshots are in each replay's state folder if you need more than a diff.")
    return REPLAY_PART.format(game=game, captured=captured, readnote=readnote, timelinenote=tln, rule=(STATE_RULE + "\n") if state else "",
                              items=items_text(F, task, out), replays="\n".join(rep), unit=unit, refnote=REFNOTE if task is not None else "")


def replay_part_verifier(game, F, R, out, cost, task=None, state=True, rshots=False):
    if not R: return {f["id"]: {"score": 0.0, "could_not_test": False, "reason": "no traces handed in", "evidence": []} for f in F}
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    pr = lambda FF: replay_part_prompt(game, FF, R, out, task, state=state, rshots=rshots)
    run1 = lambda p, name: claude(p, out, name, tools=("Read",), allowed=("Read",), add_dirs=(out,), max_turns=80, cwd=out)   # no replay folder
    fu = followup_call("replay_verifier", lambda miss, name: run1(pr([f for f in F if f["id"] in miss]), name), json_judge)
    v, bad = judge.run(cost, "replay_verifier", lambda n: run1(pr(F), "replay_verifier" + ("" if n == 1 else f".try{n}")),
                       json_judge([f["id"] for f in F]), followup=fu)
    return {f["id"]: v.get(f["id"]) or judge.placeholder("replay_verifier", bad) for f in F}


def parsed(txt):
    """the JSON object of an answer, or None"""
    try: return loose_json(txt)
    except Exception: return None


def json_judge(ids, need_score=False):
    """the check of a judge whose answer is the verdict (replay verifier, arbiter, visual judge)"""
    return lambda txt, rec: judge.failure(rec, txt, parsed(txt), ids, need_score=need_score)


def replay_verifier(game, F, R, out, cost, task=None, extra_dirs=()):
    if not R: return {f["id"]: {"score": 0.0, "could_not_test": False, "reason": "no traces handed in", "evidence": []} for f in F}
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    prompt = replay_prompt(game, F, R, task, out)
    run1 = lambda p, name: claude(p, out, name, tools=("Read",), allowed=("Read",), add_dirs=(out, *extra_dirs), max_turns=80, cwd=out)
    fu = followup_call("replay_verifier", lambda miss, name: run1(replay_prompt(game, [f for f in F if f["id"] in miss], R, task, out), name), json_judge)
    v, bad = judge.run(cost, "replay_verifier", lambda n: run1(prompt, "replay_verifier" + ("" if n == 1 else f".try{n}")),
                       json_judge([f["id"] for f in F]), followup=fu)
    return {f["id"]: v.get(f["id"]) or judge.placeholder("replay_verifier", bad) for f in F}


def dev_shots(dv):
    """the developer's screenshots (deliverable/shots)"""
    return sorted(p for p in (Path(dv) / "shots").glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))[:24] if (Path(dv) / "shots").exists() else []


def trace_claims(dv):
    """[(trace file, claims)] of the deliverable's traces (at most 4, as replayed)"""
    out = []
    for f in sorted((Path(dv) / "traces").glob("*.yaml"))[:4]:
        try: tr = yaml.safe_load(f.read_text()) or {}
        except Exception: tr = {}
        out.append((f.name, tr.get("claims") or [] if isinstance(tr, dict) else []))
    return out


def shots_prompt(game, F, dv, R=None, task=None, out=None, state=True, rshots=False):
    """the shots verifier prompt: developer screenshots + claims (+ with R, our replays: their state timelines when state, their
    screenshots when rshots)"""
    shots = "\n".join(f"  {p}" for p in dev_shots(dv)) or "  (none handed in)"
    claims = "\n".join(f"- trace {t}: {json.dumps(c, ensure_ascii=False)}" for t, c in trace_claims(dv)) or "  (no traces handed in)"
    states = ""
    if R is not None:
        rep = []
        for r in R:
            tl = r.get("timeline") or [] if rshots else [{**e, "screenshot": f"snapshot {k}"} for k, e in enumerate(r.get("timeline") or [])]
            hdr = ("screenshot" if rshots else "snapshot") + " | input since the previous one" + (" | state diff" if state else "")
            rep.append(f"- trace {r['trace']}: replay {'ran' if r.get('ok') else 'FAILED TO RUN (an evaluation fault, not evidence against the mod)'}\n"
                       + (f"  screenshots: {', '.join(r.get('shots') or []) or 'none'}\n" if rshots else "")
                       + f"  timeline ({hdr}):\n{timeline_text(tl, state=state) or '    (none)'}"
                       + (f"\n  final state: {json.dumps(r.get('state'), ensure_ascii=False, default=str)[:800]}" if state else ""))
        title = {(True, False): "STATE TIMELINES", (False, True): "SCREENSHOT TIMELINES", (True, True): "SCREENSHOT AND STATE TIMELINES"}[(state, rshots)]
        states = f"\n{title} OF OUR REPLAYS:\n" + ("\n".join(rep) or "  (no replays)") + "\n"
    note = ""
    if R is not None:
        note = (RSHOTNOTE.format(state=" and the state diff (changed old -> new, +added, -removed)" if state else "; no game state is given")
                if rshots else STATENOTE) + (STATE_RULE + "\n" if state else "")
    return SHOTS.format(game=game, items=items_text(F, task, out), shots=shots, claims=claims, states=states,
                        statenote=note, refnote=REFNOTE if task is not None else "")


def shots_verifier(game, F, dv, R, out, cost, task=None, state=True, rshots=False, extra_dirs=()):
    """evidence ablation shots (R None) / with R the replays: shots+state (state), shots+replay (rshots), shots+replay+state: one judge call"""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    if not dev_shots(dv) and R is None:
        return {f["id"]: {"score": 0.0, "could_not_test": False, "reason": "no developer screenshots handed in", "evidence": []} for f in F}
    run1 = lambda p, name: claude(p, out, name, tools=("Read",), allowed=("Read",), add_dirs=(out, Path(dv).resolve(), *(extra_dirs if rshots else ())),
                                  max_turns=80, cwd=out)
    sp = lambda FF: shots_prompt(game, FF, dv, R, task, out, state=state, rshots=rshots)
    fu = followup_call("shots_verifier", lambda miss, name: run1(sp([f for f in F if f["id"] in miss]), name), json_judge)
    v, bad = judge.run(cost, "shots_verifier", lambda n: run1(sp(F), "shots_verifier" + ("" if n == 1 else f".try{n}")),
                       json_judge([f["id"] for f in F]), followup=fu)
    return {f["id"]: v.get(f["id"]) or judge.placeholder("shots_verifier", bad) for f in F}


def play_judge(out, ids):
    """the play verifier's check: verdict.json in its working directory (else a JSON verdict in its answer)"""
    def check(txt, rec):
        vf = out / "verdict.json"
        if vf.exists():
            try: return judge.failure(rec, txt, json.loads(vf.read_text()), ids)
            except ValueError: return judge.failure(rec, txt, parsed(txt), ids, parse_error="verdict.json is not parseable JSON")
        return judge.failure(rec, txt, parsed(txt), ids, parse_error="no verdict.json")
    return check


def play_verifier(task, level, ad, seat, inst, dv, out, cost, actions=None, minutes=None):
    from ..run.trial import tools, write_ctx
    ba, bm = play_budget(); actions = ba if actions is None else actions; minutes = bm if minutes is None else minutes
    minutes = int(minutes) if float(minutes).is_integer() else minutes
    F = scored_items(task)
    out.mkdir(parents=True, exist_ok=True); ph2 = out.parent / "_phase2_src"
    shutil.rmtree(ph2, ignore_errors=True); (ph2 / "traces").mkdir(parents=True)
    if (Path(dv) / "ACCESS.md").exists(): shutil.copy(Path(dv) / "ACCESS.md", ph2 / "ACCESS.md")
    for f in sorted((Path(dv) / "traces").glob("*.yaml"))[:4]: shutil.copy(f, ph2 / "traces" / f.name)
    ps = task.hidden.get("play_suggestions") or {}
    (ph2 / "suggestions.yaml").write_text(yaml.safe_dump({k: v for k, v in ps.items() if k in {f["id"] for f in F} or k == "robustness"}, allow_unicode=True, width=140))
    ctx = out.parent / "_play_ctx" / "ctx.json"   # outside out/ (the verifier may write only there): it cannot change its own
    # action count, phase or tools
    t0 = time.time()

    def attempt(n):          # every attempt: the whole budget, phase 1 again, a fresh deadline
        out.mkdir(parents=True, exist_ok=True)
        write_ctx(ctx, task.game, seat, inst, out.parent.parent, time.time() + minutes * 60, mode="play", mod_dir=str(Path(dv) / "mod"),
                  play={"dir": str(out), "actions": actions, "used": 0, "phase2": False, "phase2_src": str(ph2)})
        T = tools(out, ctx, mode="play")
        (out / ".tmp").mkdir(exist_ok=True)
        env = dict(os.environ, PATH=f"{Path(T['play']).parent}:{os.environ['PATH']}", TMPDIR=str(out / ".tmp"))
        prompt = PLAY.format(game=ad.name, actions=actions, minutes=minutes, instruction=task.instruction(level), items=items_text(F, task, out),
                             rule=STATE_RULE, refnote=REFNOTE)
        try:
            return claude(prompt, out, "play_verifier", tools=("Bash", "Read", "Write"), allowed=("Bash(play:*)", "Read", f"Edit(/{out.resolve()}/**)"),   # Edit rules govern Write too
                          max_turns=actions * 3 + 40, timeout=minutes * 60 + 600, cwd=out, env=env,
                          extra=["--permission-mode", "dontAsk", "--disallowedTools", *[f"Read(/{p}/**)" for p in config.get("agent_deny_read")]])
        finally:
            try: ad.stop(inst)
            except Exception: pass

    def fresh(n):            # the failed attempt moves to play.try<n-1>/ (kept for diagnosis); a fresh instance with the mod
        old = out.with_name(f"{out.name}.try{n - 1}"); shutil.rmtree(old, ignore_errors=True)
        if out.exists(): out.rename(old)
        ad.provision(inst); ad.install_mod(inst, Path(dv) / "mod")

    def recorded(miss, name):  # follow-up: score the missing items from the recorded session, no game, no `play`
        prompt = PLAY_FOLLOWUP.format(ids=", ".join(miss), rule=STATE_RULE, items=items_text([f for f in F if f["id"] in miss], task, out))
        return claude(prompt, out, name, tools=("Read",), allowed=("Read",), add_dirs=(out,),
                      max_turns=60, cwd=out, extra=["--disallowedTools", *[f"Read(/{p}/**)" for p in config.get("agent_deny_read")]])
    v, bad = judge.run(cost, "play_verifier", attempt, play_judge(out, [f["id"] for f in F]), before_retry=fresh,
                       followup=followup_call("play_verifier", recorded, json_judge))
    return {f["id"]: v.get(f["id"]) or judge.placeholder("play_verifier", bad) for f in F}, round((time.time() - t0) / 60, 1)


def arbitrate(F, rv, pv, out, cost):
    """-> {id: (score | None, decision)}. An item a verifier failed on (judge_failed) is not decided by the other alone"""
    final, rows, ids = {}, [], []
    for f in F:
        a, b = rv.get(f["id"]) or {}, pv.get(f["id"]) or {}
        jf = [x["judge_failed"] for x in (a, b) if x.get("judge_failed")]
        if jf: final[f["id"]] = (None, "judge failed: " + " | ".join(jf)); continue
        sa = None if a.get("could_not_test") or a.get("score") is None else float(a["score"])
        sb = None if b.get("could_not_test") or b.get("score") is None else float(b["score"])
        if sa is None and sb is None: final[f["id"]] = (None, "both verifiers could not test")
        elif sa is None or sb is None or sa == sb: final[f["id"]] = (sb if sa is None else sa, "agree" if sa == sb else "one verifier only")
        else: ids.append(f["id"]); rows.append(f"{f['id']}: {f['item']}\n  anchors: 1 = {f['anchors']['1']} | 0.5 = {f['anchors']['0.5']} | 0 = {f['anchors']['0']}\n"
                          f"  replay verifier: {sa} -- {a.get('reason')} | evidence {json.dumps(a.get('evidence'), ensure_ascii=False)[:500]}\n"
                          f"  play verifier: {sb} -- {b.get('reason')} | evidence {json.dumps(b.get('evidence'), ensure_ascii=False)[:500]}")
    if rows:
        prompt = ARBITER.format(rows="\n".join(rows))
        byid = dict(zip(ids, rows))
        fu = followup_call("arbiter", lambda miss, name: claude(ARBITER.format(rows="\n".join(byid[i] for i in miss)), out, name),
                           lambda miss: json_judge(miss, need_score=True))
        got, bad = judge.run(cost, "arbiter", lambda n: claude(prompt, out, "arbiter" + ("" if n == 1 else f".try{n}")), json_judge(ids, need_score=True),
                             followup=fu)
        for i, x in got.items():
            if i in ids and x.get("score") is not None: final[i] = (float(x["score"]), "arbitrated: " + str(x.get("reason")))
        for i in ids: final.setdefault(i, (None, f"arbitration failed: {bad}"))     # bad is set: judge_failed, the trial is not scored
    return final


def single(F, v, who):
    """one verifier's verdict -> {id: (score | None, decision)} (evidence ablations without arbitration)"""
    fin = {}
    for f in F:
        a = v.get(f["id"]) or {}
        if a.get("judge_failed"): fin[f["id"]] = (None, f"judge failed: {a['judge_failed']}")
        elif a.get("could_not_test") or a.get("score") is None: fin[f["id"]] = (None, f"{who} could not test")
        else: fin[f["id"]] = (float(a["score"]), f"{who} only (evidence ablation)")
    return fin


def judge_items(mode, task, F, R, dv, out, cost, game, play=None, extra_dirs=()):
    """CR / FP judging under an evidence mode -> (items, play minutes). play() -> (play verdicts, minutes): only mode full"""
    pm, ev = 0.0, {}
    if mode in SHOTS_MODES:
        st, rs = SHOTS_MODES[mode]
        v = shots_verifier(game, F, dv, R if (st or rs) else None, out / "shots", cost, task, state=st, rshots=rs, extra_dirs=extra_dirs)
        fin = single(F, v, "shots verifier"); ev = {"shots": v}
    elif mode in REPLAY_PART_MODES:
        st, rs = REPLAY_PART_MODES[mode]
        v = replay_part_verifier(game, F, R, out / "replay", cost, task, state=st, rshots=rs)
        fin = single(F, v, f"replay verifier ({mode})"); ev = {"replay": v}
    else:
        rv = replay_verifier(game, F, R, out / "replay", cost, task, extra_dirs=extra_dirs); ev = {"replay": rv}
        if mode == "shots+state+replay": fin = single(F, rv, "replay verifier")
        else:
            log(f"{task.id}: replay verifier done; play verifier starts")
            pv, pm = play(); ev["play"] = pv
            fin = arbitrate(F, rv, pv, out, cost)
    items = [{"id": f["id"], "column": COLUMN[f["id"][:2]], "score": fin[f["id"]][0] if fin[f["id"]][0] is not None else 0.0,
              "could_not_test": fin[f["id"]][0] is None, "judge": config.get("judge_model"),
              "evidence": {**{k: x.get(f["id"]) for k, x in ev.items()}, "decision": fin[f["id"]][1]}} for f in F]
    return items, pm


def evaluate(task, level, ad, seat, inst, dv, out, cost):
    """-> (CR + FP items, replays, game_minutes). The replays always run (visual uses their captures); the evidence mode decides
    what the CR / FP judging sees (only `full` starts the play verifier)"""
    F = scored_items(task); t0 = time.time(); out.mkdir(parents=True, exist_ok=True); mode = evidence_mode()
    R = replays(ad, inst, dv, out / "replay")
    if mode != "full": log(f"{task.id}/{level}: evidence mode {mode}")
    items, _ = judge_items(mode, task, F, R, dv, out, cost, ad.name, play=lambda: play_verifier(task, level, ad, seat, inst, dv, out / "play", cost))
    wjson(out / "functions.json", {"evidence": mode, "replays": [{k: r.get(k) for k in ("trace", "ok", "claims", "shots", "timeline")} for r in R], "items": items})
    return items, R, round((time.time() - t0) / 60, 1)


def saved_replays(src):
    """the replays a saved evaluation captured (src = its functions/ dir): functions.json + each replay folder's replay.json
    (final state) -> R as replays() returns it"""
    fj = json.loads((Path(src) / "functions.json").read_text())
    R = []
    for r in fj.get("replays") or []:
        rj = Path(src) / "replay" / Path(r["trace"]).stem / "replay.json"
        st = json.loads(rj.read_text()).get("state") if rj.exists() else None
        R.append({**r, "state": st})
    return R

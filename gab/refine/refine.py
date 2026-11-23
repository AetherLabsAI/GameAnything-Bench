"""Refine rounds on finished trials (the "+Ours" setting): each refine round is

  1. targets   (once per task, shared by every setting) game-style target pictures generated from the reference with the image
               model (reference frame + a screenshot of the game as style anchor) -> <targets_dir>/<task>/ (the paper's are
               downloaded by `gab media fetch`)
  2. record    replay every trace of the current hand-in on a fresh game instance and record the screen -> refine/rK/replay/*.mp4
  3. critic    a reviewer agent (OpenCode + a vision model, in a container, no game) compares what was built (replay videos,
               shots, traces, ACCESS.md) with the reference and the targets; it extracts frames itself with ffmpeg and writes
               feedback.md (issues by kind, severity, evidence, fix) + scores.json  -> refine/rK/critic/
  4. refine    the trial's own agent continues on its work/ for --minutes with the brief (gab/run/trial.py GUIDE), the targets
               and the critic's feedback; then the after-state is recorded and reviewed again (rK+1) for before/after scores.
               With the experience memory (gab/refine/memory.py; on by default, --no-memory off) the agent also gets work/lessons.md
               (the run's lessons for this game + builder practice, budgeted), and after the after-review 0-3 new lessons are
               extracted from the round and consolidated into the run's memory.

Everything an agent did before is archived by archive_for_resume (attempts/<n>/: transcript, logs, the round-1 deliverable)."""
import base64, json, os, shutil, subprocess, tempfile, time, traceback, urllib.error, urllib.request
from pathlib import Path

import yaml

from .. import config
from ..run import infra
from ..game import base as gbase
from ..util import log, rjson, wjson

KINDS = {   # the critic's issue taxonomy (GameAnything feedback.py KINDS, adapted)
    "missing_content": "a part the request asks for is not shown in the game, or only told by text (see Coverage)",
    "not_like_reference": "does not look like the reference video / images / target pictures (shapes, colours, style, signature elements)",
    "sparse_plain": "bare, empty or crude scene; flat colour blocks, placeholder or procedural stand-in art",
    "color_light": "colour, lighting or palette wrong",
    "mood": "the genre / atmosphere / feeling of the reference is missing",
    "layout_scale": "position, proportion or layout wrong (field or room proportions, object sizes)",
    "unreadable_object": "cannot tell what something is",
    "model_quality": "broken model or texture: stretched, washed out, missing, misaligned",
    "clipping": "clipping, floating objects, overlaps",
    "npc_life": "characters / creatures static, too few, not animated",
    "off_style": "assets do not fit the game's own art style or scale (e.g. photo-real pictures in a pixel-art game)",
    "mechanic_wrong": "rules or mechanics differ from the reference / the request",
    "claim_unproven": "a trace claim or ACCESS.md promise is not visible or not true in the replay (a part the request asks for "
                      "that is only claimed -> also a missing_content issue)",
    "ui_hud": "interface / HUD / state display missing or unreadable (score, timer, prompts)",
    "text_language": "in-game text problems",
    "game_error": "error, crash, freeze, replay broke off",
    "other": "anything else",
}
# the two parts of a review: A = does the game do what the request asks (the coverage check),
# B = does it look like the reference (the original reviewer). Every kind belongs to exactly one part; "other" goes where it fits.
KIND_GROUPS = (
    ("A", "Request coverage and gameplay", ("missing_content", "mechanic_wrong", "claim_unproven", "ui_hud", "text_language",
                                            "game_error")),
    ("B", "Reference likeness and look", ("not_like_reference", "sparse_plain", "color_light", "mood", "layout_scale",
                                          "unreadable_object", "model_quality", "clipping", "npc_life", "off_style")),
)
CRITIC_BRIEF_VERSION = "coverage+reference-v1"   # part A coverage (coverage-v1) + part B reference likeness


def kinds_text():
    """the kinds for the reviewer brief, listed under part A and part B"""
    out = []
    for g, title, ks in KIND_GROUPS:
        out.append(f"  Part {g} ({title}):"); out += [f"    {k}: {KINDS[k]}" for k in ks]
    out.append(f"  Either part: other: {KINDS['other']}")
    return "\n".join(out)
SCORE_AXES = ("reference_likeness", "scene_richness", "style_fit", "gameplay_completeness")


def targets_dir(): return config.path("targets_dir")


def _cfg(k, default=None):
    try: return config.get(k) or default
    except KeyError: return default


def copy_targets(task, w):
    src = targets_dir() / task.id
    if not src.is_dir(): return 0
    (w / "targets").mkdir(parents=True, exist_ok=True); n = 0
    for f in sorted(src.glob("*.png")):
        shutil.copy(f, w / "targets" / f.name); n += 1
    return n


# ---------------------------------------------------------------- 1. targets
TARGET_PROMPT = ("Image 1 is from the reference a player wants recreated as a mod. Image 2 is a screenshot of the video game "
                 "{title}. Draw a NEW in-game screenshot of {title} -- the same art style, rendering, camera, scale and interface "
                 "conventions as image 2 -- that shows the content of image 1 (its characters, places, objects, colours and mood) "
                 "as it would look rebuilt inside {title}. No text, no watermark. What the player asked for: {instr}")
TARGET_PROMPT_TEXT = ("Image 1 is a screenshot of the video game {title}. Draw a NEW in-game screenshot of {title} -- same art style, "
                      "rendering, camera, scale and interface conventions as image 1 -- showing this scene as it would look in a "
                      "mod that recreates it: {instr} No text, no watermark.")
SIZES = {"16:9": "2560x1440", "1:1": "1920x1920", "4:3": "2304x1728"}


def style_anchor(game):
    """a screenshot of the game itself (style reference for the targets): <targets_dir>/_style/<game>.png, else the vanilla
    screenshot of the host-fidelity material (`gab media fetch`), else the newest `gab selfcheck` / `gab get-game` screenshot"""
    for p in (targets_dir() / "_style" / f"{game}.png", config.path("media_dir") / "_host_fidelity" / "vanilla" / f"{game}.png"):
        if p.exists(): return p
    sc = sorted((config.path("runs_dir") / "_selfcheck" / gbase.gdir(game).name.replace("_", "-")).rglob("*.png"),
                key=lambda f: f.stat().st_mtime, reverse=True)
    sc = sc or sorted((config.path("runs_dir") / "_selfcheck" / game).rglob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
    return sc[0] if sc else None


def _data_uri(p):
    p = Path(p); mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    raw = p.read_bytes()
    try:                                                   # Seedream: aspect ratio within [0.4, 2.5] -> pad
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(raw)); w, h = im.size
        if w / h > 2.4 or w / h < 0.42:
            W, H = (w, int(w / 2.4) + 1) if w / h > 2.4 else (int(h * 0.42) + 1, h)
            bg = Image.new("RGB", (W, H), (0, 0, 0)); bg.paste(im.convert("RGB"), ((W - w) // 2, (H - h) // 2))
            out = io.BytesIO(); bg.save(out, "PNG"); raw, mime = out.getvalue(), "image/png"
    except Exception:
        pass
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def _generate(prompt, images, size):
    """one picture from the configured image endpoint (image_base_url / image_key_env / image_model / image_api) -> bytes"""
    from ..agent.incontainer import image_route
    r = image_route()
    if not r: raise RuntimeError("image generation is off (no image key configured)")
    up, env, _, auth = r; up = up.rstrip("/")
    key = os.environ.get(env) if env else None
    model = config.get("image_model") or ""
    from ..host.keyproxy import images_body, as_png
    refs = [_data_uri(i) for i in images][:4]
    if str(config.get("image_api") or "images") == "generations":
        url = up + "/images/generations"
        body = {"model": model, "prompt": prompt[:2400], "size": size, "response_format": "b64_json", "watermark": False}
        if refs: body["image"] = refs
    else:                                                 # the same request gen-image sends through the router
        url = up + "/images"
        body = images_body({"prompt": prompt[:2400], "n": 1, "aspect_ratio": "16:9", "images": refs}, model)
    hd = {"Content-Type": "application/json", "User-Agent": "gameanything-bench-gab", **({"Authorization": "Bearer " + key} if key else {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=hd)
    try:
        with urllib.request.urlopen(req, timeout=300) as r_: d = json.loads(r_.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"image model HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from None
    png, mt = as_png(d["data"][0]["b64_json"])           # target_NN.png must be a PNG (the API answers JPEG)
    if mt != "image/png": raise RuntimeError("the image model answered no image Pillow can convert to PNG")
    return base64.b64decode(png)


def _frames(clip, n, out):
    """n frames evenly over the clip (20%..80%) -> paths"""
    try: dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
                                    capture_output=True, text=True).stdout.strip() or 0)
    except ValueError: dur = 0
    paths = []
    for i in range(n):
        t = dur * (0.2 + 0.6 * (i / max(n - 1, 1))) if dur else 0
        p = out / f"{Path(clip).stem}_f{i + 1}.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(clip), "-frames:v", "1",
                        "-vf", "scale=1280:-2", str(p)], capture_output=True)
        if p.exists(): paths.append(p)
    return paths


def make_targets(task, n=3, force=False):
    """2-4 target pictures for a task -> <targets_dir>/<task>/target_NN.png + targets.json (source per picture). The paper's
    pictures are downloaded by `gab media fetch`; only a task without them is generated here (needs the image key)."""
    out = targets_dir() / task.id
    if out.is_dir() and any(out.glob("target_*.png")) and not force: return sorted(out.glob("target_*.png"))
    from ..agent.incontainer import image_route
    if not image_route(): return []                       # off (logged once): the brief then has no target pictures
    out.mkdir(parents=True, exist_ok=True)
    title = gbase.title(task.game); anchor = style_anchor(task.game)
    instr = " ".join(task.instruction("fine").split())[:900]
    srcs = []
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        clips = task.clips()
        if clips:
            per = max(1, n // len(clips)) if len(clips) < n else 1
            for c in clips[:n]: srcs += _frames(c, per, t)
        srcs += [task.mdir / r["file"] for r in task.references("image")][:max(0, n - len(srcs))]
        made, meta = [], []
        jobs = [(s, TARGET_PROMPT.format(title=title, instr=instr), [s] + ([anchor] if anchor else [])) for s in srcs[:n]]
        if not jobs:                                             # text-only task: from the text, n pictures
            jobs = [(None, TARGET_PROMPT_TEXT.format(title=title, instr=instr), [anchor] if anchor else []) for _ in range(min(n, 2))]
        for i, (s, prompt, imgs) in enumerate(jobs, 1):
            try:
                png = _generate(prompt, imgs, SIZES["16:9"])
            except Exception as e:
                if s is None or "content_policy" not in str(e):
                    log(f"{task.id}: target {i} failed: {e}"); continue
                # the reference frame itself is refused (violence / sensitive content): from the text + the style anchor
                log(f"{task.id}: target {i}: reference frame refused, from the text instead")
                s = None
                try: png = _generate(TARGET_PROMPT_TEXT.format(title=title, instr=instr), [anchor] if anchor else [], SIZES["16:9"])
                except Exception as e2:
                    log(f"{task.id}: target {i} failed: {e2}"); continue
            p = out / f"target_{i:02d}.png"; p.write_bytes(png); made.append(p)
            if s is not None and Path(s).exists(): shutil.copy(s, out / f"source_{i:02d}{Path(s).suffix}")
            meta.append({"target": p.name, "source": (f"source_{i:02d}{Path(s).suffix}" if s is not None else "text"),
                         "style_anchor": str(anchor) if anchor else None})
    wjson(out / "targets.json", {"task": task.id, "game": task.game, "made": time.strftime("%Y-%m-%dT%H:%M:%S"), "targets": meta})
    log(f"{task.id}: {len(made)} target pictures in {out}")
    return made


# ---------------------------------------------------------------- 2. record
def record_replays(d, task, level, seat, out, mod=None, traces=None, video=True):
    """replay every trace of the hand-in on a fresh instance with the screen recorded -> out/<trace>.mp4 (+ replay shots under
    out/<trace>/). Recording: MJPEG frames grabbed in the sandbox (x11grab), encoded to H.264 on the host. video=False: the
    replays only (the regression check). Each record.json item keeps the replay's summary (gab/refine/replaycheck.py: final state,
    step of the last state change, places, texts)."""
    from . import replaycheck
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    dv = Path(d) / "deliverable"; mod = Path(mod or dv / "mod")
    traces = traces if traces is not None else sorted((dv / "traces").glob("*.yaml"))
    ad = gbase.get(task.game, seat); inst = f"rec-{task.id[-14:]}-{level}"[:48] + "-" + __import__("hashlib").sha1(str(Path(d).resolve()).encode()).hexdigest()[:8]
    res = {"traces": [], "build": None}
    ok, blog = ad.build(inst, mod)
    res["build"] = bool(ok)
    if not ok:
        (out / "build.log").write_text(str(blog)[-20000:]); return res
    ad.provision(inst); ad.install_mod(inst, mod)
    w, h = ad.screen; tmp = f"{seat.home}/.gab_vid_{inst}"
    try:
        for f in traces:
            item = {"trace": f.name, "video": None, "ok": False}
            try:
                tr = yaml.safe_load(f.read_text())
                if tr.get("start", "fresh") == "fresh":
                    sok, sl = ad.reset(inst)
                    if not sok: item["error"] = f"start failed: {str(sl)[-200:]}"; res["traces"].append(item); continue
                if video: ad._x(inst, f"rm -f {tmp}.mkv {tmp}.pid; nohup ffmpeg -loglevel error -y -f x11grab -framerate 8 -video_size {w}x{h} "
                            f"-i $DISPLAY -c:v mjpeg -q:v 5 {tmp}.mkv >/dev/null 2>&1 & echo $! > {tmp}.pid", timeout=30)
                if video: time.sleep(1)
                r = ad.replay(inst, tr, out / f.stem, fresh=False)
                if video:
                    time.sleep(1.5)
                    ad._x(inst, f"kill -INT $(cat {tmp}.pid) 2>/dev/null; sleep 2", timeout=30)
                raw = out / f"{f.stem}.mkv"
                if video and seat.copy_out(f"{tmp}.mkv", raw) and raw.exists():
                    mp4 = out / f"{f.stem}.mp4"
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-c:v", "libx264", "-preset", "veryfast",
                                    "-crf", "28", "-pix_fmt", "yuv420p", str(mp4)], capture_output=True)
                    raw.unlink(missing_ok=True)
                    if mp4.exists(): item["video"] = mp4.name
                item["ok"] = bool(r.get("ok")); item["shots"] = len(r.get("shots") or [])
                try: item["summary"] = replaycheck.summarize_dir(out / f.stem, tr, name=f.name)
                except Exception as e: item["summary_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            except Exception as e:
                item["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            res["traces"].append(item)
    finally:
        try: ad.stop(inst)
        except Exception: pass
        seat.exec(f"rm -f {tmp}.mkv {tmp}.pid")
    wjson(out / "record.json", res)
    return res


# ---------------------------------------------------------------- 2b. evaluation gate
GATE_WARNING = """## FIRST: the evaluation rejects this hand-in ({fail}) -- it scores 0 on every column until this is fixed

The evaluation's gate (build, install, load, then regression probes that play the ORIGINAL game with your mod installed)
failed on the hand-in this review looked at:
  {reason}
{explain}
Fix this before anything else, then work on the review below. Your mod must add to {title}, not replace it: the stock game,
its start / main scene and its normal play must still work with your mod installed.{probe_hint}

"""
GATE_EXPLAIN = {
    "host_removed": "Meaning: with your mod installed, the stock game itself is gone (e.g. its main scene / start state was replaced or "
                    "freed, or the stock content can no longer be reached). Put your content alongside the stock game instead: new "
                    "scenes / maps / levels reached from it, autoloads that add to the stock scene rather than replacing it.",
    "unstable": "Meaning: with your mod installed, the game crashes, freezes or logs new errors in normal stock play.",
    "build": "Meaning: the mod does not build / pass the structure check (see `game check`).",
    "load": "Meaning: the game does not load with your mod installed.",
}


def eval_gate_warning(d, task, level, seat, round_dir):
    """run the evaluation's gate (gab/eval/run.py gate_check + gab/eval/regression.py, the same code that decides gate_failed /
    host_removed scores) on the current hand-in; if it fails, put a warning on top of round_dir/critic/feedback.md (the file the
    refine agent reads); without it a host_removed hand-in stays at 0 round after round, the agent never learns why. Result kept in round_dir/gate.json; runs once per round."""
    rd = Path(round_dir); gj = rd / "gate.json"; fb = rd / "critic" / "feedback.md"
    if gj.exists(): return rjson(gj)
    from ..eval import regression
    from ..eval.run import gate_check
    mod = Path(d) / "deliverable" / "mod"; e = rd / "gate"
    ad = gbase.get(task.game, seat); inst = f"rgate-{task.id[-14:]}-{level}"[:48] + "-" + __import__("hashlib").sha1(str(Path(d).resolve()).encode()).hexdigest()[:8]
    t0 = time.time()
    g = gate_check(ad, inst, mod, e)
    if g["pass"]:
        gp, _ = regression.evaluate(task, ad, mod, e / "regression")
        g = {**g, "pass": gp["pass"], "fail": gp["fail"], "reason": gp.get("reason") if gp["pass"] is False else None}
    res = {"pass": g["pass"], "fail": g.get("fail"), "reason": g.get("reason"), "errors": g.get("errors"),
           "minutes": round((time.time() - t0) / 60, 1), "checked": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if g["pass"] is False and g.get("fail") in GATE_EXPLAIN and fb.exists():
        txt = fb.read_text()
        if not txt.startswith("## FIRST: the evaluation rejects"):
            reason = str(g.get("reason") or "; ".join(g.get("errors") or []) or g["fail"])[:800]
            hint = "\nBefore you hand in, start the stock game with your mod and play its normal opening; if `game help` lists `game probe`, run the probes it names." \
                if g["fail"] in ("host_removed", "unstable") else ""
            fb.write_text(GATE_WARNING.format(fail=g["fail"], reason=reason, explain=GATE_EXPLAIN[g["fail"]], title=gbase.title(task.game),
                                              probe_hint=hint) + txt)
            res["warned"] = True
    wjson(gj, res)
    log(f"{task.id}/{level}: evaluation gate on the hand-in: {'pass' if g['pass'] else g.get('fail')}{' -> warning added to the review' if res.get('warned') else ''}")
    return res


# ---------------------------------------------------------------- 3. critic
CRITIC_BRIEF = """You are an independent, strict reviewer. A game-development agent was asked (by a user) to build a mod for
{title} that recreates an experience from a reference. You do NOT trust its claims: judge only what you can see.

The user's request:
{instruction}

In {work}:
- reference/   the reference the user means (video clips / images / text){targets}
- built/       what the agent handed in: replay/*.mp4 (screen recordings of its own input traces replayed on a fresh game),
               replay/<trace>/*.png (screenshots taken during those replays), shots/*.png (its key screenshots),
               traces/*.yaml (the traces, each with `claims`), ACCESS.md (how a player reaches the experience)

Tools: ffmpeg / ffprobe and python3 on your PATH; look at images with your Read tool. You cannot watch a video directly:
extract frames and look at them. Every image you read stays in your context, so read few, information-dense images (about 20
in all, never more): one contact sheet per video (e.g. `ffmpeg -i built/replay/1_x.mp4 -vf "fps=1/4,scale=480:-1,tile=4x3"
-frames:v 1 -q:v 3 frames/sheet_1_x.jpg`), side-by-side pairs (`hstack`) of a built frame next to its reference / target
picture, single frames only where you need detail. Save every image you make as .jpg, at most 1600 px on its longest side
(game screenshots as PNG are several MB each and overload your context). Do the same for reference videos. Compare what was
built with the reference and the target pictures, and check every trace claim against its replay.
Never write placeholder content ("in progress", guessed scores): write the files from what you have actually looked at.

Before anything else, check coverage. Split the user's request above into the parts it asks for: each
thing it names (places, characters, events and their order, rules and mechanics, the finish / win
condition) and each "Specifically" requirement, as a short list in the request's own words. For every
part decide, from the replays and shots only:
  shown        it happens / is visible in the game itself;
  partly       it exists but is incomplete, or is only told (a message, a caption, a title card)
               instead of happening in the game;
  not shown    no replay or shot shows it (a trace claim or ACCESS.md promise alone does not count).
A part that is not shown is a `missing_content` issue of severity high; a part that is only partly
shown is `missing_content` medium. Do not split a part more finely than the request does.

Then judge the look: put built frames next to the reference and the target pictures (hstack pairs) and find where the
game does not look like the reference (shapes, colours, lighting, layout, signature elements, richness, style fit).

Write {work}/feedback.md in two parts:
1. "Coverage": one line per part: part | shown / partly / not shown | evidence (file and time).
2. "Part A: request coverage and gameplay": the issues of the part-A kinds below, most important first.
3. "Part B: reference likeness and look": the issues of the part-B kinds below, most important first.
   Each issue: `kind` (one of the list below), `severity` (high / medium / low), `evidence` (a frame or file you looked at,
   with time), what is wrong, and a concrete fix the developer can do in the game. Write both parts; a part with no issue
   says so in one line.
4. "Top fixes": the 3-5 changes that would improve the result the most, from both parts. If the game is broken, game_error
   comes first; then every part that is not shown or partly shown; and, whenever part B has a high or medium issue, at
   least one part-B fix.
Kinds:
{kinds}

Write {work}/scores.json: {{"reference_likeness": 0-5, "scene_richness": 0-5, "style_fit": 0-5, "gameplay_completeness": 0-5,
"notes": "one line",
"coverage": [{{"part": "...", "status": "shown|partly|missing"}}, ...], "missing": ["<part not shown or partly shown>", ...]}}
(scores: 0 = absent / broken, 5 = matches the reference well; "missing" lists every part whose coverage is not "shown").
When both files are complete, create the empty file {work}/REVIEW_DONE -- the review is collected then. You have about
{minutes:.0f} minutes."""


CRITIC_CONTINUE = ("The review is not finished (REVIEW_DONE does not exist). Do not read more images unless you must: write the "
                   "complete feedback.md (Coverage; part A and part B issues with kind, severity, evidence, fix; Top fixes) and scores.json (with coverage and missing) from what you "
                   "have seen, then create REVIEW_DONE.")


# A review session that ends without its files (gemini-3.8-flash answering empty turn after turn once many large images are in its
# context) is not continued forever: after CRITIC_MAX_CONTINUATIONS continuations of the same session
# a FRESH session (an empty context) gets the same brief plus CRITIC_FINISH -- the frames the first session made are kept in
# the work dir -- and writes the files. Same brief and rubric, so the scores stay comparable.
CRITIC_MAX_CONTINUATIONS = 2
CRITIC_FINISH_IMAGES = 6
CRITIC_FINISH = """

NOTE: this review was started by an earlier session that ended before it wrote feedback.md and scores.json (too many large
images in its context). Do not start over. {frames}{drafts}Read at most {n} images in all (prefer the contact sheets and
pairs listed above; make a new .jpg, at most 1600 px, only for a part of the request none of them covers), then write the
complete feedback.md and scores.json right away, as described above, and create REVIEW_DONE."""


def _finish_prompt(brief, w):
    """the brief + CRITIC_FINISH for a fresh session after an unfinished one (its frames and draft files are in the work dir w)"""
    fr = sorted(str(p.relative_to(w)) for p in (w / "frames").rglob("*") if p.is_file() and p.suffix.lower() in
                (".png", ".jpg", ".jpeg", ".webp")) if (w / "frames").is_dir() else []
    frames = (f"The images it made are kept in {w}/frames/: " + ", ".join(fr[:40]) + (" ..." if len(fr) > 40 else "") + ". "
              if fr else "It kept no images: make a few compact .jpg contact sheets first. ")
    dr = []
    for f in ("feedback.md", "scores.json"):                # an unfinished draft is a note, never collected as it is
        if (w / f).exists(): (w / f).replace(w / f"draft_{f}"); dr.append(f"draft_{f}")
    drafts = (f"Its unfinished drafts ({', '.join(dr)}) are notes only: check them against the images. " if dr else "")
    return brief + CRITIC_FINISH.format(frames=frames, drafts=drafts, n=CRITIC_FINISH_IMAGES)


def critic(d, task, level, seat, round_dir, replay_dir, model=None, minutes=None, dv=None, retries=None):
    """the reviewer agent (OpenCode + a vision model through critic_base_url, in the seat's container) -> round_dir/critic/{feedback.md,
    scores.json}; returns the scores dict (or None)"""
    if retries is None: retries = int(config.get("critic_retries") or 3)   # fresh-session retries of an incomplete review (a vision critic sometimes ends with an empty answer)
    from ..agent import base as abase
    from ..agent.incontainer import wrap, bridge_dir
    minutes = float(minutes or config.get("critic_minutes") or 15.0)   # room for the coverage pass over every replay
    rd = Path(round_dir) / "critic"
    if (rd / "scores.json").exists() and (rd / "feedback.md").exists(): return rjson(rd / "scores.json")
    shutil.rmtree(rd, ignore_errors=True); rd.mkdir(parents=True)
    # the reviewer runs in the trial's container, which mounts only <trial>/work, <trial>/agent_home and the bridge: its own
    # run dir lives under agent_home (its logs, HOME and workspace), sharing the trial's bridge; nothing of the trial's agent/ moves
    # a new dir per review: an earlier one holds files of the container user the host cannot remove
    run_dir = Path(d) / "agent_home" / ".critic" / f"{Path(round_dir).name}-{time.strftime('%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
    while run_dir.exists(): run_dir = run_dir.with_name(run_dir.name + "x")   # a retry in the same millisecond
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / ".gab-bridge").write_text(str(bridge_dir(d)) + "\n")
    w = run_dir / "work"; (w / "built").mkdir(parents=True)
    dv = Path(dv or Path(d) / "deliverable")
    for sub in ("shots", "traces"):
        if (dv / sub).is_dir(): shutil.copytree(dv / sub, w / "built" / sub)
    if (dv / "ACCESS.md").exists(): shutil.copy(dv / "ACCESS.md", w / "built" / "ACCESS.md")
    if Path(replay_dir).is_dir(): shutil.copytree(replay_dir, w / "built" / "replay")
    (w / "reference").mkdir()
    for r in task.references(): shutil.copy(task.mdir / r["file"], w / "reference" / r["file"])
    nt = copy_targets(task, w)
    tg = "\n- targets/     target pictures: what the result should look like in this game (generated from the reference)" if nt else ""
    brief = CRITIC_BRIEF.format(title=gbase.title(task.game), instruction=task.instruction(level), work=w, targets=tg,
                                kinds=kinds_text(), minutes=minutes)
    (w / "brief.md").write_text(brief)
    model = model or config.get("critic_model")
    A = abase.get("opencode", model=model, base_url=config.get("critic_base_url"), api_key_env=config.get("critic_key_env"), auth="api")
    from ..host.keyproxy import CRITIC_SHRINK
    inner = wrap(A, seat, run_dir, max_usd=config.get("critic_max_usd"), proxy_shrink=CRITIC_SHRINK)   # images shrunk (keyproxy)
    # gemini-3.8-flash sometimes ends its turn with an empty answer after many images (seen in tests): continue the same
    # session a few times, then a fresh session finishes the review (CRITIC_FINISH)
    from ..agent.until_done import UntilDone
    from ..run import interrupt
    A = UntilDone(inner, marker="REVIEW_DONE", message=CRITIC_CONTINUE, tag="critic", max_continuations=CRITIC_MAX_CONTINUATIONS)
    t0 = time.time()
    try:
        run = A.run(w, brief, abase.Budget(minutes=minutes), {})
    except Exception as e:
        run = abase.AgentRun(exit_reason="crash", wall_s=round(time.time() - t0, 1), extra={"error": f"{type(e).__name__}: {e}"})
    done = lambda: (w / "REVIEW_DONE").exists() and (w / "feedback.md").exists() and (w / "scores.json").exists()
    finish = None
    if not done() and not (run.extra or {}).get("budget_exceeded") and run.exit_reason not in ("crash", "interrupted") \
            and not interrupt.requested():
        tr = run_dir / "agent" / "transcript.jsonl"
        if tr.exists(): tr.replace(tr.with_name("transcript.review.jsonl"))
        (w / "REVIEW_DONE").unlink(missing_ok=True)
        fm = max(6.0, min(10.0, minutes - (time.time() - t0) / 60))
        log(f"{task.id}/{level}: review {Path(round_dir).name} unfinished after {run.wall_s:.0f}s: a fresh session finishes it ({fm:.0f} min)")
        F = UntilDone(inner, marker="REVIEW_DONE", message=CRITIC_CONTINUE, tag="criticfinish", max_continuations=1)
        t1 = time.time()
        try:
            fr = F.run(w, _finish_prompt(brief, w), abase.Budget(minutes=fm), {})
        except Exception as e:
            fr = abase.AgentRun(exit_reason="crash", wall_s=round(time.time() - t1, 1), extra={"error": f"{type(e).__name__}: {e}"})
        finish = {"exit_reason": fr.exit_reason, "wall_s": fr.wall_s, "usd": fr.usd, "complete": done()}
        if tr.with_name("transcript.review.jsonl").exists():          # one transcript: the review session, then the finish session
            with open(tr.with_name("transcript.joined.jsonl"), "w") as out:
                for q in (tr.with_name("transcript.review.jsonl"), tr):
                    if q.exists(): out.write(q.read_text(errors="replace"))
            tr.with_name("transcript.joined.jsonl").replace(tr)
        for f in ("usd", "tokens_in", "tokens_out"):
            if getattr(fr, f) is not None: setattr(run, f, (getattr(run, f) or 0) + getattr(fr, f))
        run.wall_s = round(time.time() - t0, 1)
        if (fr.extra or {}).get("budget_exceeded"): run.extra = {**(run.extra or {}), "budget_exceeded": fr.extra["budget_exceeded"]}
    complete = done()
    for f in ("feedback.md", "scores.json"):      # an unfinished review is kept for inspection, never used (it may hold placeholders)
        if (w / f).exists(): shutil.copy(w / f, rd / (f if complete else f"incomplete_{f}"))
        elif not complete and (w / f"draft_{f}").exists(): shutil.copy(w / f"draft_{f}", rd / f"incomplete_{f}")
    if (w / "frames").is_dir(): shutil.copytree(w / "frames", rd / "frames", dirs_exist_ok=True)
    for f in ("transcript.jsonl", "llm_proxy.log", "command.txt"):
        if (run_dir / "agent" / f).exists(): shutil.copy(run_dir / "agent" / f, rd / f)
    from ..run.interrupt import check
    if not complete: check(f"review {Path(round_dir).name}")   # stopped by the operator: no retry; the review runs again next time
    sc = None
    try: sc = json.loads((rd / "scores.json").read_text()) if (rd / "scores.json").exists() else None
    except Exception: sc = None
    over = (run.extra or {}).get("budget_exceeded")
    wjson(rd / "critic.json", {"model": model, "brief": CRITIC_BRIEF_VERSION, "complete": complete, "exit_reason": run.exit_reason, "wall_s": run.wall_s, "usd": run.usd,
                               "tokens_in": run.tokens_in, "tokens_out": run.tokens_out, "scores": sc,
                               "continuations": (run.extra or {}).get("continuations"), "finish_session": finish, "image_shrink": CRITIC_SHRINK,
                               **({"infra": {"status": "budget_exceeded", "primary": "budget_exceeded", **over}} if over else {})})
    if over: log(f"{task.id}/{level}: review {Path(round_dir).name} stopped at its spend cap ${over.get('max_usd')}")
    if not complete and retries > 0 and not over:     # (a review stopped by its USD cap is not retried)                      # a vision critic sometimes ends with an empty answer:
        log(f"{task.id}/{level}: review {Path(round_dir).name} incomplete, fresh retry ({retries} left)")   # one fresh review
        dst = rd.with_name(f"critic_incomplete_{int(time.time())}")
        while dst.exists(): dst = dst.with_name(dst.name + "x")   # several retries in the same second
        shutil.move(str(rd), str(dst))
        return critic(d, task, level, seat, round_dir, replay_dir, model, minutes, dv, retries - 1)
    return sc


# ---------------------------------------------------------------- 4. refine round
REFINE_MSG = """This is an IMPROVEMENT round of about {minutes:.0f} minutes on a hand-in you made earlier for this task. Your earlier
work is still in {work} (mod, traces, shots, ACCESS.md in {out}); improve it -- you may change anything, logic included.

An independent reviewer replayed your traces, recorded the screen and compared the result with the reference{tg}. Its review is
in {work}/review/feedback.md (issues by kind and severity, with evidence) and review/scores.json; its frames are in
review/frames/ when it kept any.{lessons}
Start with the review's Coverage section: build every part marked "not shown" or "partly", so that it
happens in the game itself (a message alone does not count), and add a trace and a shot that show it.
A missing part costs much more than a rough one. Then fix the rest of the "Top fixes" and the part-B issues
(make it look like the reference), the most severe first.
Do not rebuild or restyle what already works unless an issue names it; before you hand in, replay your
existing traces and check that what worked before still works.

Keep `game check` passing and the hand-in consistent with the mod: update traces, shots and ACCESS.md to what you changed.
{out}/DONE was removed for this round: create it again when you are done -- it is accepted only after minute
{min_done:.0f} of this round (use the time to fix and verify), or the round ends when the time is up.

The original task, as given to you before (with what is new for this round):

{brief}"""


# matched-budget control (`gab refine --ablation nocritic`): the same round -- same minutes, same frozen hand-in,
# same brief, targets and DONE rule -- WITHOUT the reviewer's feedback (and without the experience memory). REFINE_MSG minus its
# review parts, so only the feedback differs.
REFINE_MSG_NOCRITIC = """This is an IMPROVEMENT round of about {minutes:.0f} minutes on a hand-in you made earlier for this task. Your earlier
work is still in {work} (mod, traces, shots, ACCESS.md in {out}); improve it -- you may change anything, logic included.

Keep improving your submission. Start with the parts of the request that the game does not show yet or shows only partly:
build each of them so that it happens in the game itself (a message alone does not count), and add a trace and a shot that
show it. A missing part costs much more than a rough one. Then improve the rest and make it look like the reference{tg},
the most important first.
Do not rebuild or restyle what already works; before you hand in, replay your
existing traces and check that what worked before still works.

Keep `game check` passing and the hand-in consistent with the mod: update traces, shots and ACCESS.md to what you changed.
{out}/DONE was removed for this round: create it again when you are done -- it is accepted only after minute
{min_done:.0f} of this round (use the time to fix and verify), or the round ends when the time is up.

The original task, as given to you before (with what is new for this round):

{brief}"""


LESSONS_LINE = ("\nLessons that earlier tasks with this game wrote down are in {work}/lessons.md: read it; it is advice from other "
                "tasks and may not apply to this one.")    # the experience memory (gab/refine/memory.py)


REFINE_RESUME_MSG = ("Your session was interrupted: the benchmark operator restarted the run (not caused by you). This improvement "
                     "round goes on: your workspace, including {out} and review/, is exactly as you left it, but the game is NOT "
                     "running: start it again (`game start`) before you send input or take screenshots. About {left:.0f} minutes of "
                     "this round are left; {done_rule} Continue where you left off.")
MIN_LEFT = 1.0                      # minutes: an interrupted round with less than this left is over (recorded, reviewed)


def _done_rule(minutes, used_min):
    m = 0.75 * minutes
    if used_min >= m: return "create deliverable/DONE when you are done."
    return f"deliverable/DONE is accepted only after minute {m:.0f} of the round (about {m - used_min:.0f} minutes from now)."


def refine_agent(d, task, level, agent, model, opts, minutes, seat, ad, feedback_dir, round_k=None, resume=None, memory=False,
                 min_done=None, feedback=True):
    """the trial's own agent continues on its work/ (the brief + targets + the critic's review).
    resume: {sid, left, prev, used_min, started} of this round's interrupted agent (gab/run/interrupt.py): the same session goes on
    with the minutes left of the round; review/, DONE and the brief stay as they are. -> the round's run (both segments)
    memory: give the agent the run's lessons (gab/refine/memory.py before_round -> work/lessons.md + a line in the message)
    min_done: minutes before
    which DONE is not accepted (default 3/4 of the round; 0 = none). feedback=False (the matched-budget control, gab refine
    --ablation nocritic): no review/ in the workspace, REFINE_MSG_NOCRITIC; feedback_dir is not read."""
    from ..agent import base as abase
    from ..run.trial import archive_for_resume, write_ctx, tools, clean_name, freeze_deliverable, brief_text, combine_runs, FRESH_NOTE, agent_task
    from ..run import interrupt
    w = Path(d) / "work"
    at = agent_task(task, d=d)                            # the task as the agent sees it (a reference-ablation trial: without them)
    if not (w / "deliverable").is_dir(): raise RuntimeError(f"{d}: no work/deliverable to refine")
    # the logs of the interrupted segment are moved to attempts/<n> too (the harness writes transcript.jsonl at a fixed path);
    # the session itself lives in agent_home and is not touched
    if (Path(d) / "agent").exists() or (Path(d) / "deliverable").is_dir(): archive_for_resume(Path(d))
    nt = (0 if getattr(at, "withhold_targets", False) else copy_targets(task, w)) if not resume else len(list((w / "targets").glob("*.png")))
    if not resume:
        rv = w / "review"; shutil.rmtree(rv, ignore_errors=True)
    if not resume and feedback:
        rv.mkdir()
        fd = Path(feedback_dir)
        for f in ("feedback.md", "scores.json"):
            if (fd / f).exists(): shutil.copy(fd / f, rv / f)
        if (fd / "frames").is_dir(): shutil.copytree(fd / "frames", rv / "frames")
    if not resume:
        (w / "deliverable" / "DONE").unlink(missing_ok=True)
    mrec = None
    if not resume:
        from . import memory as mem
        (w / "lessons.md").unlink(missing_ok=True)       # never a stale one (a round without memory gets none)
        mem.round_file(d, round_k or 0).unlink(missing_ok=True)
        if memory and feedback:
            try: mrec = mem.before_round(d, task, level, round_k or 0, feedback_dir, w)
            except Exception as e:                       # the round goes on without lessons; extraction still runs
                log(f"{task.id}/{level}: lessons not retrieved ({type(e).__name__}: {e})")
                wjson(mem.round_file(d, round_k or 0), {"round": round_k, "retrieval": {"ids": [], "error": f"{type(e).__name__}: {str(e)[:300]}"}})
    left = resume["left"] if resume else minutes; used0 = resume["used_min"] if resume else 0.0
    inst = f"dev-{clean_name(Path(d).parent.parent.parent.name)}-{level}"[:48] + "-" + __import__("hashlib").sha1(str(Path(d).resolve()).encode()).hexdigest()[:8]
    deadline = time.time() + left * 60
    ad.provision(inst)
    write_ctx(Path(d) / "tools" / "ctx.json", task.game, seat, inst, w, deadline, mode="dev")
    T = tools(w, Path(d) / "tools" / "ctx.json")
    A = abase.get(agent, model=model, **{k: v for k, v in opts.items() if k not in ("in_container", "brief", "reference_ablation")})
    if opts.get("in_container"):
        from ..agent.incontainer import wrap
        A = wrap(A, seat, Path(d), max_usd=config.get("refine_max_usd"), spent_usd=((resume or {}).get("prev") or {}).get("usd") or 0.0)
    else:                                                 # an agent on the host: the gen tools (gab/agent/hostgen.py)
        from ..agent.hostgen import wrap as host_gen
        A = host_gen(A, Path(d))
    if opts.get("until_done", True):
        from ..agent.until_done import UntilDone
        # DONE before 3/4 of the round (its total time, both segments of an interrupted round): not accepted
        md = round(0.75 * minutes, 1) if min_done is None else (round(float(min_done), 1) or None)
        A = UntilDone(A, tag=f"refine{round_k or ''}" + ("r" if resume else ""), min_minutes=md, elapsed0=used0)
    if resume and (w / "refine_brief.md").exists(): msg0 = (w / "refine_brief.md").read_text()
    elif not feedback:
        brief = brief_text(at, level, task.game, w, minutes)
        msg0 = REFINE_MSG_NOCRITIC.format(minutes=minutes, min_done=0.75 * minutes, work=w, out=w / "deliverable", brief=brief,
                                          tg=" and the target pictures in targets/" if nt else "")
        (w / "refine_brief.md").write_text(msg0)
    else:
        brief = brief_text(at, level, task.game, w, minutes)
        msg0 = REFINE_MSG.format(minutes=minutes, min_done=0.75 * minutes, work=w, out=w / "deliverable", brief=brief,
                                 tg=" and with the target pictures in targets/" if nt else "",
                                 lessons=LESSONS_LINE.format(work=w) if mrec and mrec.get("ids") else "")
        (w / "refine_brief.md").write_text(msg0)
    seg0 = time.time()
    wjson(Path(d) / "agent" / "refine_running.json", {"round": round_k, "minutes": minutes, "phase": "agent",
                                                       "started": resume["started"] if resume else time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                       "segment_started": seg0, "prev": resume["prev"] if resume else None})
    (Path(d) / "agent" / "interrupted.json").unlink(missing_ok=True)   # superseded by the marker of this segment
    try: interrupt.check(f"{task.id}/{level} refine round before its agent started")
    except interrupt.Interrupted:
        try: ad.stop(inst)
        except Exception: pass
        raise
    if resume: log(f"{task.id}/{level}: refine round {round_k} resumes ({left:.0f} of {minutes:.0f} min left, session {resume['sid'] or 'new'})")
    else: log(f"{task.id}/{level}: refine round starts ({minutes:.0f} min)")
    t0 = time.time()
    try:
        if resume:
            msg = REFINE_RESUME_MSG.format(out=w / "deliverable", left=left, done_rule=_done_rule(minutes, used0))
            run = A.run(w, msg, abase.Budget(minutes=left), T, resume=resume["sid"], fresh=msg0 + "\n\n" + FRESH_NOTE.format(work=w) + "\n" + msg)
        else: run = A.run(w, msg0, abase.Budget(minutes=minutes), T)
    except Exception as e:
        run = abase.AgentRun(exit_reason="crash", wall_s=round(time.time() - t0, 1), extra={"error": f"{type(e).__name__}: {e}",
                                                                                              "tb": traceback.format_exc()[-2000:]})
    finally:
        try: ad.stop(inst)
        except Exception: pass
    if run.usd is None and (run.tokens_in or run.tokens_out): run.usd = abase.price_usd(run.model or model, run.tokens_in, run.tokens_out, run.tokens_cached)
    if interrupt.requested() and run.exit_reason in ("interrupted", "crash"): run.exit_reason = "interrupted"
    if resume and resume.get("prev"): run = combine_runs(resume["prev"], run)
    if run.exit_reason == "interrupted":              # the frozen deliverable stays the last complete one
        log(f"{task.id}/{level}: refine round {round_k} interrupted by the operator after {run.wall_s / 60:.1f} of {minutes:.0f} min")
        return run
    freeze_deliverable(w / "deliverable", Path(d) / "deliverable")
    log(f"{task.id}/{level}: refine round {run.exit_reason} after {run.wall_s:.0f} s; deliverable re-frozen")
    return run


def interrupted_round(d, k, minutes):
    """round k of this trial was stopped while its agent worked -> resume {sid, left, prev, used_min, started} or None.
    Graceful stop: agent/interrupted.json (mode refine, round k). Killed: agent/refine_running.json of round k is still there;
    the time used is read from the agent's files (newest change since the segment started), the session from the transcript."""
    from ..run.trial import session_in, combine_runs
    from ..agent.base import AgentRun
    a = Path(d) / "agent"
    mk = rjson(a / "refine_running.json", None)
    if not mk or mk.get("round") != k or mk.get("phase") != "agent": return None
    it = rjson(a / "interrupted.json", None)
    if it and it.get("mode") == "refine" and it.get("round") == k and it.get("run"):
        prev = it["run"]
    else:                                                 # hard kill: no record of this segment, only its files
        tr = a / "transcript.jsonl"; seg0 = float(mk.get("segment_started") or 0)
        files = [p for p in a.glob("*") if p.is_file() and p.stat().st_mtime >= seg0]
        used_s = max([p.stat().st_mtime for p in files] or [seg0]) - seg0
        part = AgentRun(exit_reason="interrupted", wall_s=round(max(0.0, used_s), 1), transcript=str(tr) if tr.exists() else None,
                        extra={"session_id": session_in(tr) if tr.exists() and tr.stat().st_mtime >= seg0 else None,
                               "usd_unknown": "killed: the requests of this segment were not priced"})
        if part.extra["session_id"] is None and mk.get("prev"): part.extra["session_id"] = (mk["prev"].get("extra") or {}).get("session_id")
        prev = (combine_runs(mk["prev"], part) if mk.get("prev") else part).to_dict()
    sid = (prev.get("extra") or {}).get("session_id")
    used_min = (prev.get("wall_s") or 0) / 60
    return {"sid": sid, "left": minutes - used_min, "prev": prev, "used_min": used_min, "started": mk.get("started")}


def last_round(d):
    """the newest refine/rK of a trial (r0 = the hand-in before any refine round) or None"""
    rs = [p for p in (Path(d) / "refine").glob("r*") if p.name[1:].isdigit()]
    return max(rs, key=lambda p: int(p.name[1:])) if rs else None


def missing_parts(d):
    """the parts of the request the newest review found not shown / partly shown: a list ([] = all covered), or None when
    unknown (no review, or one written under the old brief without a "missing" field)"""
    r = last_round(d)
    sc = rjson(r / "critic" / "scores.json", None) if r else None
    if not isinstance(sc, dict) or not isinstance(sc.get("missing"), list): return None
    return sc["missing"]


def archive_last_review(d):
    """move the newest round's critic/ to critic_old_<time>/ so it is reviewed again (under the current brief; the replays are
    kept). A trial without refine rounds gets an empty refine/r0 (its hand-in is recorded and reviewed). -> the round dir"""
    r = last_round(d)
    if r is None: r = Path(d) / "refine" / "r0"; r.mkdir(parents=True, exist_ok=True); return r
    cj = rjson(r / "critic" / "critic.json", {}) or {}
    if cj.get("brief") == CRITIC_BRIEF_VERSION and (r / "critic" / "scores.json").exists() and (r / "critic" / "feedback.md").exists():
        return r                                   # already reviewed under the current brief (a restart): not paid for again
    if (r / "critic").is_dir(): shutil.move(str(r / "critic"), str(r / f"critic_old_{time.strftime('%Y%m%dT%H%M%S')}"))
    return r


def review_rounds(d, task, level, seat, critic_model=None):
    """(re-)review every refine round whose review is missing or incomplete (replays are reused); agent.json scores updated"""
    rroot = Path(d) / "refine"; out = {}
    for r in sorted(rroot.glob("r*"), key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else -1):
        if not r.name[1:].isdigit(): continue
        if not (r / "replay" / "record.json").exists(): record_replays(d, task, level, seat, r / "replay")
        out[int(r.name[1:])] = critic(d, task, level, seat, r, r / "replay", model=critic_model)
    cur = rjson(Path(d) / "agent" / "agent.json", {}) or {}
    for rr in cur.get("refine_rounds") or []:
        k = rr.get("round")
        if k in out: rr["scores_after"] = out.get(k); rr["scores_before"] = out.get(k - 1, rr.get("scores_before"))
    wjson(Path(d) / "agent" / "agent.json", cur)
    return out


def refine_trial(d, task, level, agent, model, opts, minutes, seat, critic_model=None, after=True, memory=None, feedback=True,
                 setting=None):
    """record + review (rK), refine, record + review again (rK+1). agent.json gets refine_rounds[].
    A round stopped by the operator (gab/run/interrupt.py; or killed) is continued, not started again: its agent goes on in the same
    session with the minutes left (refine_agent resume); a round whose agent finished but whose after-review did not is
    completed. agent/refine_running.json marks a round in progress (phase agent | review).
    memory: the experience memory of the run (gab/refine/memory.py; None = config `memory`, on by default; False = --no-memory).
    feedback=False: the matched-budget control (no review before the round is needed or shown, no memory; gab refine --ablation
    nocritic); the after-state is still recorded and reviewed for the scores. setting: the name recorded in refine_rounds[]."""
    from ..run.trial import check_deliverable
    from ..agent.base import AgentRun
    from ..run import interrupt
    if memory is None:
        from .memory import enabled_default
        memory = enabled_default()
    if not feedback: memory = False                       # the control has no experience memory either
    setting = setting or round_setting(feedback, memory)
    d = Path(d); rroot = d / "refine"; a = d / "agent"
    mk = rjson(a / "refine_running.json", None)
    if mk and mk.get("phase") == "review" and mk.get("run"):          # the agent of round k finished, its review did not
        k = int(mk["round"]); run = AgentRun(**mk["run"])
        sb = rjson(rroot / f"r{k - 1}" / "critic" / "scores.json")
        log(f"{task.id}/{level}: refine round {k}: the agent had finished; completing its review")
        return _after(d, task, level, seat, k, minutes, run, sb, mk.get("started"), critic_model, after, harness=mk.get("harness") or agent,
                      setting=mk.get("setting") or setting)
    k = 1 + max([int(p.name[1:]) for p in rroot.glob("r*") if p.name[1:].isdigit()] or [0]) if rroot.exists() else 1
    before = rroot / f"r{k - 1}"
    if not feedback:                                      # the control: the review before the round is not needed (kept if there)
        sb = rjson(before / "critic" / "scores.json") if (before / "critic" / "scores.json").exists() else None
    elif not (before / "critic" / "scores.json").exists():
        if (before / "replay" / "record.json").exists(): rec = rjson(before / "replay" / "record.json")
        else:
            rec = record_replays(d, task, level, seat, before / "replay")
            if interrupt.requested(): (before / "replay" / "record.json").unlink(missing_ok=True); interrupt.check("record")
        sb = critic(d, task, level, seat, before, before / "replay", model=critic_model)
        if k > 1 and sb is not None:               # round k-1 was (re-)reviewed now (--rereview-last): keep agent.json in step
            cur = rjson(a / "agent.json", {}) or {}
            for rr in cur.get("refine_rounds") or []:
                if rr.get("round") == k - 1: rr["scores_after"] = sb
            wjson(a / "agent.json", cur)
    else:
        rec = rjson(before / "replay" / "record.json", {}); sb = rjson(before / "critic" / "scores.json")
    if feedback and not (before / "critic" / "feedback.md").exists():   # no review: a refine round without it is wasted
        raise RuntimeError(f"{d}: the critic wrote no feedback in {before / 'critic'}; refine not started")
    if feedback:                                          # (the control gets no gate warning either: it is feedback)
        try: eval_gate_warning(d, task, level, seat, before)   # the evaluation's gate on this hand-in -> warning on top of feedback.md
        except Exception as e: log(f"{task.id}/{level}: refine round {k}: evaluation gate check skipped ({type(e).__name__}: {e})")
    interrupt.check(f"{task.id}/{level} refine round {k}")
    ad = gbase.get(task.game, seat)
    rs = interrupted_round(d, k, minutes)
    if rs and rs["left"] < MIN_LEFT:                       # stopped with (almost) nothing left: the round is over
        run = AgentRun(**{f: v for f, v in rs["prev"].items() if f in AgentRun.__dataclass_fields__}); run.exit_reason = "timeout"
        from ..run.trial import freeze_deliverable
        freeze_deliverable(d / "work" / "deliverable", d / "deliverable")
    else:
        run = refine_agent(d, task, level, agent, model, opts, minutes, seat, ad, before / "critic", round_k=k, resume=rs, memory=memory,
                           feedback=feedback)
    started = (rs or {}).get("started") or (rjson(a / "refine_running.json", {}) or {}).get("started")
    if run.exit_reason == "interrupted":
        wjson(a / "interrupted.json", {"mode": "refine", "round": k, "session_id": (run.extra or {}).get("session_id"),
                                       "used_s": run.wall_s, "budget_min": minutes, "started": started,
                                       "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "how": "signal", "run": run.to_dict()})
        return run, sb, None
    mk = rjson(a / "refine_running.json", {}) or {}
    wjson(a / "refine_running.json", {**mk, "round": k, "phase": "review", "started": started, "run": run.to_dict(), "harness": agent,
                                      "setting": setting})
    (a / "interrupted.json").unlink(missing_ok=True)
    return _after(d, task, level, seat, k, minutes, run, sb, started, critic_model, after, harness=agent, setting=setting)


def round_setting(feedback=True, memory=True):
    """the name of a refine round's setting, recorded in refine_rounds[].setting"""
    return "nocritic" if not feedback else "critic+memory" if memory else "critic"


def _after(d, task, level, seat, k, minutes, run, sb, started, critic_model, after, harness=None, setting=None):
    """record + review the after-state of round k, then its refine_rounds entry (time and cost over all its segments)"""
    from ..run.trial import check_deliverable
    from ..run import interrupt
    rroot = d / "refine"; sa = None
    if after:
        aft = rroot / f"r{k}"
        if not (aft / "replay" / "record.json").exists():
            record_replays(d, task, level, seat, aft / "replay")
            if interrupt.requested(): (aft / "replay" / "record.json").unlink(missing_ok=True); interrupt.check("record")
        sa = critic(d, task, level, seat, aft, aft / "replay", model=critic_model)
    # the experience memory (gab/refine/memory.py): a round that started with lessons gets its own extracted and consolidated
    # (never fails the round); the record goes into the refine_rounds entry, its cost apart from the agent's
    from . import memory as mem
    mrec = None
    if (rjson(mem.round_file(d, k), None) or {}).get("retrieval") is not None:
        try: mem.after_round(d, task, level, k, run, rroot / f"r{k - 1}" / "critic", (rroot / f"r{k}" / "critic") if after else None, started)
        except Exception as e: log(f"{task.id}/{level}: refine round {k}: lesson extraction failed ({type(e).__name__}: {e})")
        mrec = rjson(mem.round_file(d, k), None)
    cur = rjson(d / "agent" / "agent.json", {}) or {}
    st = started or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - (run.wall_s or 0)))
    from ..run.trial import guard_incidents
    g = guard_incidents(run.extra, d / "agent" / "llm_proxy.log")   # the key proxy's cost guards that fired in this round
    cur.setdefault("refine_rounds", []).append({"round": k, "started": st, "minutes": minutes, "scores_before": sb, "scores_after": sa,
                                                "check": check_deliverable(d / "deliverable"), **run.to_dict(),
                                                **({"infra": infra.assess(g, run.exit_reason)} if g else {}),
                                                **({"memory": {x: mrec.get(x) for x in ("retrieval", "extraction")}} if mrec else {}),
                                                "harness": harness,    # the harness this round ran with (the trial's own)
                                                **({"setting": setting} if setting else {})})   # critic+memory | critic | nocritic
    wjson(d / "agent" / "agent.json", cur)
    (d / "agent" / "refine_running.json").unlink(missing_ok=True)
    return run, sb, sa


def _rj_ok(a):
    return bool(a) and (a.get("infra") or {}).get("status") != "infra_error" and a.get("exit_reason") not in (None, "interrupted")


def snapshot(d, k):
    """the deliverable of a trial after exactly k refine rounds (k=0: the base hand-in): <trial>/deliverable when the trial has k rounds, else the newest attempts/<n>/deliverable whose agent.json
    has k rounds -> Path or None"""
    d = Path(d); a = rjson(d / "agent" / "agent.json", {}) or {}
    rr = a.get("refine_rounds") or []
    if len(rr) == k and (d / "deliverable").is_dir() and not (d / "agent" / "refine_running.json").exists(): return d / "deliverable"
    if len(rr) < k: return None
    cand = []
    for p in (d / "attempts").glob("*/agent/agent.json"):
        n = p.parent.parent.name; x = rjson(p, None)
        if n.isdigit() and _rj_ok(x) and len(x.get("refine_rounds") or []) == k and (d / "attempts" / n / "deliverable").is_dir():
            cand.append(int(n))
    return d / "attempts" / str(max(cand)) / "deliverable" if cand else None


# ---------------------------------------------------------------- 6. refine-round ablations (gab refine --ablation)
# One refine round under another setting, from the SAME frozen base hand-in (R0) as the source run's R1, in a NEW trial dir of a
# new run name (the source trial is only read), so it never mixes with the source's rounds:
#   nomem     critic review, no experience memory          -> <run>-nomem-s0
#   nocritic  no review, no memory: the matched-budget control (REFINE_MSG_NOCRITIC)   -> <run>-nocritic-s0
#   full      critic review + experience memory  -> <run>-full-s0: the memory is the new run's own, made once
#             as a frozen copy of the source run's memory on this machine, without the current task's own experience at
#             retrieval (gab/refine/memory.py copy_store / exclude_task); its extractions go into the copy only
# The source's refine/r0 (its replays and review, as the source's R1 saw them) is copied, so `nomem` / `full` get the same review.
ABLATIONS = {"nomem": {"feedback": True, "memory": False, "setting": "critic"},
             "nocritic": {"feedback": False, "memory": False, "setting": "nocritic"},
             "full": {"feedback": True, "memory": True, "setting": "critic+memory"}}


def ablation_run_name(name, ablation):
    import re
    if ablation not in ABLATIONS: raise ValueError(f"unknown refine ablation {ablation!r} (one of {', '.join(ABLATIONS)})")
    return re.sub(r"-s(\d+)$", rf"-{ablation}-s\1", name) if re.search(r"-s\d+$", name) else f"{name}-{ablation}"


def seed_ablation_trial(src, new, task, level, new_name, ablation, minutes):
    """the new trial dir for a refine ablation: trial.json (the source's, renamed, + refine_ablation), the public workspace
    (gab/run/trial.py stage), work/deliverable + deliverable = the source's base snapshot (0 refine rounds), agent/agent.json = that
    snapshot's agent record (no refine rounds), refine/r0 = the source's refine/r0 when it has one. -> the base snapshot path"""
    from ..run.trial import stage, agent_task
    src, new = Path(src), Path(new)
    base = snapshot(src, 0)
    if base is None: raise RuntimeError(f"{src}: no base snapshot (0 refine rounds)")
    new.mkdir(parents=True, exist_ok=True)
    tm = rjson(src / "trial.json", {}) or {}
    wjson(new / "trial.json", {**tm, "name": new_name, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "attempt": 1, "attempts": [],
                                "refine_ablation": {**ABLATIONS[ablation], "round_setting": ABLATIONS[ablation]["setting"], "setting": ablation, "source_trial": str(src),
                                                    "base_snapshot": str(base)}})
    w, _ = stage(new, agent_task(task, d=new), level, task.game, minutes)
    shutil.rmtree(w / "deliverable", ignore_errors=True); shutil.copytree(base, w / "deliverable", symlinks=False)
    (w / "deliverable" / "DONE").unlink(missing_ok=True)
    shutil.copytree(base, new / "deliverable", symlinks=False)
    ag = rjson(base.parent / "agent" / "agent.json", {}) or {}
    wjson(new / "agent" / "agent.json", {**{k: v for k, v in ag.items() if k not in ("refine_rounds",)},
                                         "refine_ablation_of": {"trial": str(src), "base_snapshot": str(base)}})
    r0 = src / "refine" / "r0"
    if r0.is_dir():
        shutil.copytree(r0, new / "refine" / "r0", symlinks=False, ignore=shutil.ignore_patterns("critic_old_*", "critic_incomplete_*"))
    return base


def ablation_round(src, new, task, level, agent, model, opts, minutes, seat, new_name, ablation, critic_model=None):
    """one refine round of `ablation` on the source trial's base hand-in, in the new trial dir (seeded once; a later call
    continues an interrupted round like gab refine does) -> refine_trial's (run, scores_before, scores_after)"""
    new = Path(new)
    if not (new / "trial.json").exists(): seed_ablation_trial(src, new, task, level, new_name, ablation, minutes)
    got = ((rjson(new / "trial.json", {}) or {}).get("refine_ablation") or {}).get("setting")
    if got != ablation: raise RuntimeError(f"{new}: seeded for refine ablation {got!r}, not {ablation!r}: refused")
    c = ABLATIONS[ablation]
    return refine_trial(new, task, level, agent, model, opts, minutes, seat, critic_model=critic_model, memory=c["memory"],
                        feedback=c["feedback"], setting=c["setting"])

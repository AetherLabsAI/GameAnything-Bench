"""Task packages (docs/SPEC.md, rubric v4): public part in benchmark/construction/tasks/<id>/; rubric + regression picks in
benchmark/construction/rubrics/<id>/, or $GAB_HIDDEN_DIR/<id>/ (your own private tasks; read first). Reference material in
$GAB_MEDIA_DIR/<id>/: video clips, a text, images (references.yaml lists them by kind). VP keyframes are cut from the published
clips at their source times (media_dir/<id>/keyframes/), so every user scores against the same frames.
Scoring is Construction only (authoring rules v4 / v4.2): Content Realization, Functions & Playability, Visual & Presentation per
task (Overall = their mean); the regression probes are a gate (gab/eval/regression.py); one instruction level (fine)."""
import json, re, subprocess
from pathlib import Path

import jsonschema

from .. import config
from ..util import ryaml, sha256, ts

SCHEMAS = config.REPO / "schemas"
SECTIONS = ("content_realization", "functions_playability", "visual_presentation")      # authored per task
COLS_C = ("content_realization", "functions_playability", "visual_presentation")          # scored columns (Overall = their mean)
PREFIX = {"content_realization": "CR", "functions_playability": "FP", "visual_presentation": "VP"}
LEVELS = ("fine",)
SIZES = {"scene_world": {"CR": (4, 6), "FP": (2, 4), "VP": (3, 5)}, "mechanics_behavior": {"CR": (2, 4), "FP": (4, 7), "VP": (2, 3)},
         "narrative_event": {"CR": (4, 6), "FP": (3, 6), "VP": (2, 4)}}   # authoring rules v4; warnings only

TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$")
TEXT_ANCHOR = re.compile(r"^(c\d+\.)?p\d+$")
IMAGE = re.compile(r"^img\d{2}$")


def schema(name): return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def norm_anchors(a):
    """YAML anchors {1: .., 0.5: .., 0: ..} -> string keys "1", "0.5", "0" (JSON-safe)"""
    return {("0.5" if float(k) == 0.5 else str(int(float(k)))): v for k, v in (a or {}).items()}


def evidence_kind(x):
    """one evidence value -> video | text | image | None. Image evidence may say where: 'img03: the bridge in the centre'"""
    s = str(x).strip()
    if TIME.match(s): return "video"
    if TEXT_ANCHOR.match(s): return "text"
    if IMAGE.match(s.split(":")[0].strip()): return "image"
    return None


def evidence_list(item):
    """the item's evidence entries as [(kind, first, last)] (a single value has first == last)"""
    out = []
    for e in item.get("evidence") or []:
        a, b = (e[0], e[-1]) if isinstance(e, list) else (e, e)
        out.append((evidence_kind(a), str(a).strip(), str(b).strip()))
    return out


class Task:
    def __init__(self, task_id, tasks_dir=None, hidden_dir=None, media_dir=None):
        self.id = task_id
        self.dir = Path(tasks_dir or config.path("tasks_dir")) / task_id
        self.hdir = Path(hidden_dir or config.path("hidden_dir")) / task_id
        if not hidden_dir and not self.hdir.exists(): self.hdir = config.path("rubrics_dir") / task_id
        self.mdir = Path(media_dir or config.path("media_dir")) / task_id
        self.meta = ryaml(self.dir / "task.yaml")
        self.refs = ryaml(self.dir / "references.yaml")
        self._rubric = self._reg = None

    game = property(lambda s: s.meta["target_game"])
    category = property(lambda s: s.meta["category"])
    modality = property(lambda s: s.meta.get("modality", "video"))

    def instruction(self, level="fine"): return (self.dir / f"instruction_{level}.md").read_text().strip()

    def references(self, kind=None): return [r for r in self.refs["refs"] if kind is None or r["kind"] == kind]

    def clips(self): return [self.mdir / c["file"] for c in self.references("video")]

    def files(self): return [self.mdir / r["file"] for r in self.references()]

    @property
    def hidden(self):
        if self._rubric is None: self._rubric = ryaml(self.hdir / "rubric.yaml")
        return self._rubric

    @property
    def regression(self):
        if self._reg is None:
            p = self.hdir / "regression.yaml"
            self._reg = ryaml(p) if p.exists() else {"task_id": self.id, "game": self.game, "status": "pending", "picks": []}
        return self._reg

    def items(self, section):
        """rubric items of a section (content_realization | functions_playability | visual_presentation), anchors normalised"""
        return [{**i, "anchors": norm_anchors(i["anchors"])} for i in self.hidden["rubric"].get(section) or []]

    def keyframe_files(self, item):
        """host paths of a VP item's keyframes (cut from the clips on first use)"""
        return [f for f in (self.keyframe(k) for k in item.get("keyframes") or []) if f]

    def keyframe(self, k):
        """source-video time '01:29.1' -> media_dir/<id>/keyframes/t01m29.1s.jpg, one frame of the clip that covers it; None if none does"""
        f = self.mdir / "keyframes" / kf_name(k)
        if f.exists(): return f
        t = ts(k)
        for c in self.references("video"):
            a, b = c["range"]["start"], c["range"]["end"]; clip = self.mdir / c["file"]
            if a - 1 <= t <= b + 1 and clip.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                try: subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{min(max(t - a, 0), max(b - a - 0.1, 0)):.2f}", "-i", str(clip),
                                     "-frames:v", "1", "-q:v", "2", str(f)], timeout=120)
                except (FileNotFoundError, subprocess.TimeoutExpired): return None      # no ffmpeg on this host / a stuck decode
                return f if f.exists() else None
        return None

    def anchored_text(self):
        """{anchor: paragraph} of the task's text (judges only; from media_dir/<id>/judge/), {} for tasks without a text"""
        out = {}
        for r in self.references("text"):
            p = self.mdir / r.get("judge_file", "")
            if not r.get("judge_file") or not p.exists(): continue
            for para in re.split(r"\n\s*\n", p.read_text()):
                m = re.match(r"^\[((?:c\d+\.)?p\d+)\]\s*", para.strip())
                if m: out[m.group(1)] = para.strip()[m.end():]
        return out

    def image(self, name):
        """'img03' (or 'img03: where') -> host path of that reference image, None if the task has none of that number"""
        n = str(name).split(":")[0].strip()
        return next((self.mdir / r["file"] for r in self.references("image") if r["id"] == n), None)


def kf_name(k):
    """rubric keyframe '01:29.1' -> file name 't01m29.1s.jpg'"""
    m, s = str(k).split(":")[-2:]; return f"t{int(m):02d}m{float(s):04.1f}s.jpg"


def all_task_ids(tasks_dir=None):
    d = Path(tasks_dir or config.path("tasks_dir"))
    return sorted(p.name for p in d.iterdir() if (p / "task.yaml").exists()) if d.exists() else []


def validate(task_id, tasks_dir=None, hidden_dir=None, media_dir=None, check_media=True):
    """-> (errors, warnings) for one task package"""
    E, W = [], []
    try: t = Task(task_id, tasks_dir, hidden_dir, media_dir)
    except Exception as e: return [f"cannot load: {e}"], W
    for name, doc in (("task", t.meta), ("references", t.refs)):
        E += [f"{name}.yaml: {e.message} at {list(e.path)}" for e in jsonschema.Draft202012Validator(schema(name)).iter_errors(doc)]
    if E: return E, W
    if t.meta.get("task_id") != task_id: E.append(f"task.yaml task_id {t.meta.get('task_id')} != folder {task_id}")
    p = t.dir / "instruction_fine.md"
    if not p.exists() or not p.read_text().strip(): E.append("instruction_fine.md missing or empty")
    if (t.dir / "instruction_coarse.md").exists(): E.append("instruction_coarse.md present (rubric v4 has the fine level only)")
    if list(t.meta.get("references") or []) != [r["id"] for r in t.references()]:
        E.append("task.yaml references != references.yaml ids")
    kinds = {r["kind"] for r in t.references()}
    if t.meta.get("modality") != "+".join(k for k in ("video", "text", "image") if k in kinds): E.append(f"modality {t.meta.get('modality')} != reference kinds {sorted(kinds)}")
    leak = [f.name for f in t.dir.iterdir() if f.suffix in (".mp4", ".jpg", ".png", ".md") and f.name != "instruction_fine.md"
            or f.name in ("rubric.yaml", "regression.yaml")]
    if leak: E.append(f"hidden or media files in the public package: {leak}")
    if not t.hdir.exists(): return E + [f"hidden dir {t.hdir} missing"], W
    try: rub = t.hidden
    except Exception as e: return E + [f"rubric.yaml: {e}"], W
    rj = json.loads(json.dumps(rub, default=str))
    for sec in SECTIONS:
        for it in rj.get("rubric", {}).get(sec, []): it["anchors"] = norm_anchors(it.get("anchors"))
    S = [f"rubric.yaml: {e.message} at {list(e.path)}" for e in jsonschema.Draft202012Validator(schema("rubric")).iter_errors(rj)]
    S += [f"regression.yaml: {e.message}" for e in jsonschema.Draft202012Validator(schema("regression")).iter_errors(t.regression)]
    if S: return E + S, W                                     # the cross-file checks below need a well-formed rubric
    R = rub["rubric"]
    ids = [i["id"] for sec in SECTIONS for i in R[sec]]
    if len(set(ids)) != len(ids): E.append("duplicate rubric item ids")
    for r in rub["instructions_meta"]["explicit_requirements"]:
        bad = [c for c in r["covers"] if c not in ids]
        if bad: E.append(f"explicit requirement covers unknown items {bad}")
        if r["text"].strip()[:60].lower() not in t.instruction().lower(): W.append(f"explicit requirement text not found verbatim in the fine instruction: {r['text'][:50]}")
    for g, cov in (rub["instructions_meta"].get("requirement_groups") or {}).items():
        bad = [c for c in cov or [] if c not in ids]
        if bad: E.append(f"requirement group {g!r} covers unknown items {bad}")
    # evidence: each kind must be a reference the task has, and point inside it
    rs = [(c["range"]["start"], c["range"]["end"]) for c in t.references("video")]
    imgs = {r["id"] for r in t.references("image")}
    A = t.anchored_text() if check_media else {}
    for sec in SECTIONS:
        for it in R[sec]:
            w = it["id"]; K = set()
            for kind, a, b in evidence_list(it):
                K.add(kind)
                if kind is None: E.append(f"{w}: evidence {a!r} is not a clip time, a text anchor or an image number"); continue
                if kind not in kinds: E.append(f"{w}: {kind} evidence {a!r} but the task has no {kind} reference"); continue
                if kind == "video":
                    for x in (a, b):
                        if not any(lo - 1 <= ts(x) <= hi + 1 for lo, hi in rs): E.append(f"{w}: time {x} outside the clips")
                elif kind == "image":
                    for x in (a, b):
                        if x.split(":")[0].strip() not in imgs: E.append(f"{w}: {x.split(':')[0]} is not one of the task's images")
                elif A:
                    for x in (a, b):
                        if x not in A: E.append(f"{w}: {x} is not a paragraph anchor of the text")
            if it.get("cross"):
                if "+" not in t.modality: E.append(f"{w}: cross item in a single-reference task")
                elif len(K - {None}) < 2: E.append(f"{w}: a cross item cites at least two kinds of reference")
            if it.get("keyframes"):
                if "video" not in kinds: E.append(f"{w}: keyframes but the task has no clip")
                miss = [k for k in it["keyframes"] if not any(lo - 1 <= ts(k) <= hi + 1 for lo, hi in rs)]
                if miss: E.append(f"{w}: keyframes outside the clips {miss}")
    for v in R["visual_presentation"]:
        K = {k for k, _, _ in evidence_list(v)}
        if "video" in K and not v.get("keyframes") and v.get("cat") != "presentation": W.append(f"{v['id']}: VP item about the clip without keyframes")
        if not v.get("keyframes") and not K & {"image", "text"}: W.append(f"{v['id']}: no keyframes, images or passages (the visual judge gets the item text only)")
    if "+" in t.modality:
        n = sum(1 for sec in ("content_realization", "functions_playability") for i in R[sec] if i.get("cross"))
        if not 1 <= n <= 3: W.append(f"{n} cross items (rules v3.8 / v4: 1-3 in CR / FP)")
    n = {PREFIX[s]: len(R[s]) for s in SECTIONS}
    size_max = rub.get("provenance", {}).get("size_max") or {}
    for k, (lo, hi) in SIZES.get(t.category, {}).items():
        hi = max(hi, size_max.get(k, hi))
        if not lo <= n[k] <= hi and not rub.get("provenance", {}).get("size_waiver"): W.append(f"{n[k]} {k} items (rules v4: {lo}-{hi})")
    if check_media:
        for r in t.references():
            f = t.mdir / r["file"]
            if not f.exists(): W.append(f"{r['kind']} not in the media cache: {f} (gab media fetch)")
            elif sha256(f) != r["sha256"]: E.append(f"{r['file']}: sha256 mismatch")
            if r.get("judge_file"):
                j = t.mdir / r["judge_file"]
                if not j.exists(): W.append(f"judge copy not in the media cache: {j}")
                elif sha256(j) != r.get("judge_sha256"): E.append(f"{r['judge_file']}: sha256 mismatch")
    return E, W

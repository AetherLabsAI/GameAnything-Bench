"""Reference evidence for the judges (rubric v4): an item's `evidence` entries resolved against the task's reference material,
so a judge scores the built game against what the reference actually shows or says.
  clip time / range '01:12' or ['01:12', '01:15']  -> a frame of the reference clip (cut from the clip at that source time)
  text anchor 'c05.p108' / 'p012' (or a range)     -> the paragraphs of the anchored judge copy of the text, quoted
  image number 'img03' (or 'img03: where')         -> the reference image file
Files are copied into <out>/ref/ (the judge's own folder: judges cannot read the media / hidden dirs)."""
import shutil
from pathlib import Path

from ..run.task import evidence_list
from ..util import ts

PARA_CHARS, ITEM_CHARS, MAX_FRAMES = 600, 2400, 3


def fmt_time(t): return f"{int(t // 60):02d}:{t % 60:04.1f}"


def _copy(src, out):
    if not src or not Path(src).exists(): return None
    d = Path(out) / "ref"; d.mkdir(parents=True, exist_ok=True); dst = d / Path(src).name
    if not dst.exists(): shutil.copy(src, dst)
    return f"ref/{dst.name}"


def _passages(task, a, b, A):
    keys = list(A)
    if a not in A: return None
    i = keys.index(a); j = keys.index(b) if b in A else i
    out = []
    for k in keys[i:j + 1]:
        p = A[k]; out.append(f"[{k}] {p[:PARA_CHARS]}{' ...' if len(p) > PARA_CHARS else ''}")
    return "\n      ".join(out)


def resolve(task, item, out, frames=True):
    """-> (text block for the prompt, [ref files]) for one rubric item"""
    A = task.anchored_text() if task.references("text") else {}
    L, files, nfr, used = [], [], 0, 0
    for kind, a, b in evidence_list(item):
        if kind == "text":
            q = _passages(task, a, b, A)
            if q and used < ITEM_CHARS: L.append(f"text {a}{'-' + b if b != a else ''}:\n      {q}"); used += len(q)
        elif kind == "image":
            f = _copy(task.image(a), out)
            if f: L.append(f"image {f}" + (f" ({a.split(':', 1)[1].strip()})" if ":" in a else "")); files.append(f)
        elif kind == "video" and frames and nfr < MAX_FRAMES:
            t = (ts(a) + ts(b)) / 2
            f = _copy(task.keyframe(fmt_time(t)), out)
            if f: L.append(f"video {a}{'-' + b if b != a else ''}: frame {f}"); files.append(f); nfr += 1
            else: L.append(f"video {a}{'-' + b if b != a else ''}")
    return ("\n    ".join(L), files)


def keyframes(task, item, out):
    """a VP item's linked keyframes, copied into <out>/ref/"""
    return [f for f in (_copy(k, out) for k in task.keyframe_files(item)) if f]

"""`gab media fetch`: everything the benchmark needs that is not in the repository, from the Hugging Face dataset named by the
config key `hf_dataset` (repo type dataset), each file checked against its sha256:

  reference media      media/<task>/<file> of every reference (references.yaml: clips, texts + their anchored judge copies,
                       images) -> <media_dir>/<task>/; VP keyframes are then cut from the clips
  editing checkpoints  checkpoints/<key>.tar.gz (benchmark/editing/checkpoints.json) -> unpacked into <edit_store>/<key>/
  target pictures      targets/<task>/target_NN.png (benchmark/construction/targets.json) -> <targets_dir>/<task>/
  host-fidelity refs   host_fidelity/vanilla/<game>.png (benchmark/host_fidelity/vanilla.json) -> <media_dir>/_host_fidelity/vanilla/

A file already in place with the right sha256 is not downloaded again. Authentication is huggingface_hub's own: a token
(HF_TOKEN or a cached `hf auth login`) is used when present, otherwise the download is anonymous; gab never stores a token."""
import json, shutil
from pathlib import Path

from .. import config
from ..util import log, sha256

def repo_id():
    r = str(config.get("hf_dataset") or "").strip().removeprefix("hf://datasets/").strip("/")
    return r or None


def _get(path, dst):
    """one file of the dataset -> dst (raises when it cannot be downloaded)"""
    rid = repo_id()
    if not rid: raise RuntimeError("the dataset is not configured (gab setup --set hf_dataset <owner>/<name>)")
    try: from huggingface_hub import hf_hub_download
    except ImportError: raise RuntimeError("huggingface_hub is not installed (pip install huggingface_hub)") from None
    try: p = hf_hub_download(rid, path, repo_type="dataset")       # a token if present (huggingface_hub), else anonymous
    except Exception as e:
        if any(x in f"{type(e).__name__} {e}" for x in ("401", "403", "GatedRepo", "RepositoryNotFound", "Unauthorized")):
            raise RuntimeError(f"no access to the dataset {rid}: set HF_TOKEN or run `hf auth login` with an account that has "
                               f"access ({type(e).__name__})") from None
        raise
    dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy(p, dst)


KINDS = ("media", "checkpoints", "targets", "host-fidelity refs")
STATS = {}                                                  # {kind: {"checked", "downloaded", "bytes"}} of the last fetch()


def _count(kind, dst, downloaded):
    s = STATS.setdefault(kind, {"checked": 0, "downloaded": 0, "bytes": 0})
    s["checked"] += 1; s["downloaded"] += int(downloaded)
    try: s["bytes"] += Path(dst).stat().st_size
    except OSError: pass


def _ensure(path, dst, want, what, kind="media"):
    """dst with sha256 `want` (downloaded from the dataset path when missing / different) -> True if it is in place"""
    dst = Path(dst)
    if dst.exists() and (not want or sha256(dst) == want): _count(kind, dst, False); return True
    try: _get(path, dst)
    except Exception as e:
        log(f"{what}: not cached and not downloadable ({type(e).__name__}: {str(e)[:160]})"); return False
    if want and sha256(dst) != want:
        log(f"{what}: sha256 MISMATCH after download"); dst.unlink(missing_ok=True); return False
    _count(kind, dst, True)
    return True


def ref_path(r, rel=None):
    """the dataset path of a reference file (references.yaml `hf_path`: media/<task>/<file>)"""
    p = str(r["hf_path"]).removeprefix("hf://datasets/")
    if p.startswith(("media/",)): base = p.rsplit("/", 1)[0]
    else: base = "media/" + "/".join(p.split("/")[-2:-1])              # an old full hf:// path: keep media/<task>
    return f"{base}/{rel or r['file']}"


def fetch_task(t):
    ok = True
    for r in t.references():
        files = [(r["file"], r["sha256"])]
        if r.get("judge_file"): files.append((r["judge_file"], r["judge_sha256"]))
        for rel, h in files: ok &= _ensure(ref_path(r, rel), t.mdir / rel, h, f"{t.id}/{rel}")
    try:
        for v in t.items("visual_presentation"): t.keyframe_files(v)          # VP keyframes cut from the clips
    except FileNotFoundError: pass
    return ok


def fetch_targets(tasks):
    m = (json.loads((config.path("tasks_dir").parent / "targets.json").read_text()) if (config.path("tasks_dir").parent / "targets.json").exists() else {}).get("targets") or {}
    from ..refine.refine import targets_dir
    ok = True
    for t in tasks:
        for f in m.get(t.id) or []: ok &= _ensure(f["path"], targets_dir() / t.id / f["file"], f["sha256"], f"targets/{t.id}/{f['file']}", "targets")
    return ok


def fetch_vanilla(games=None):
    p = config.path("host_fidelity_dir") / "vanilla.json"
    m = (json.loads(p.read_text()) if p.exists() else {}).get("vanilla") or {}
    ok = True
    for g, f in m.items():
        if games and g not in games: continue
        ok &= _ensure(f["path"], config.path("media_dir") / "_host_fidelity" / "vanilla" / f"{g}.png", f["sha256"], f"host_fidelity/{g}", "host-fidelity refs")
    return ok


def fetch_checkpoint(key, force=False):
    """one editing start checkpoint -> unpacked in the store; True when it is there"""
    from ..edit.edit import install_checkpoint, manifest, store_dir, store_root
    if (store_dir(key) / "deliverable" / "mod").is_dir() and not force:
        s = STATS.setdefault("checkpoints", {"checked": 0, "downloaded": 0, "bytes": 0}); s["checked"] += 1
        s["bytes"] += int((manifest().get(key) or {}).get("size_bytes") or 0)
        return True
    m = manifest().get(key)
    if not m: log(f"checkpoint {key}: not in the manifest (benchmark/editing/checkpoints.json)"); return False
    arc = store_root() / ".download" / f"{key}.tar.gz"
    if not m.get("sha256"): log(f"checkpoint {key}: the manifest has no sha256 yet (not published)"); return False
    if not _ensure(m["file"], arc, m["sha256"], f"checkpoint {key}", "checkpoints"): return False
    try: install_checkpoint(key, arc, force=True)
    except Exception as e: log(f"checkpoint {key}: {type(e).__name__}: {e}"); return False
    arc.unlink(missing_ok=True)
    return True


def fetch(tasks, edits=(), targets=True, vanilla=True):
    """reference media of `tasks`, the start checkpoints of `edits`, the target pictures and host-fidelity references -> True
    if everything is in place"""
    if not repo_id(): log("hf_dataset is not set: only the local caches are checked")
    ok = True; STATS.clear()
    for t in tasks: ok &= fetch_task(t)
    for e in edits: ok &= fetch_task(e.parent)
    if targets: ok &= fetch_targets(tasks)
    if vanilla: ok &= fetch_vanilla({t.game for t in tasks} | {e.game for e in edits})
    for k in sorted({e.start_key() for e in edits}): ok &= fetch_checkpoint(k)
    log(summary())
    log("media ok" if ok else "media incomplete (see above)"); return ok


def summary():
    """one line per kind of the last fetch: files checked / downloaded and their total size"""
    def size(b): return f"{b / 1e9:.1f} GB" if b >= 1e9 else f"{b / 1e6:.1f} MB"
    parts = [f"{k} {s['checked']} files ({s['downloaded']} downloaded, {size(s['bytes'])})" for k in KINDS if (s := STATS.get(k))]
    tot = sum(s["bytes"] for s in STATS.values())
    return "checked: " + ("; ".join(parts) if parts else "nothing") + f"; total {size(tot)}"

#!/usr/bin/env python3
"""Pack the editing start checkpoints for the Hugging Face dataset (maintainers): one archive per checkpoint key named in
benchmark/editing/tasks.json, taken from a local checkpoint store, and the sha256 / size of each written into
benchmark/editing/checkpoints.json (what `gab media fetch` checks).

    python scripts/pack_checkpoints.py --store DIR --out OUT [--no-baselines]

DIR/<key>/ is a store entry (work/, deliverable/, eval/, result.json, host_fidelity.json, trial.json, checkpoint.json,
p_baseline/); OUT/checkpoints/<key>.tar.gz is what to upload under checkpoints/ in the dataset. The preservation baselines
(p_baseline/<edit>.json) are included unless --no-baselines (then every user re-judges them once, with their own judge)."""
import argparse, hashlib, json, sys, tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".fetch", ".download", "__pycache__"}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--no-baselines", action="store_true")
    a = ap.parse_args()
    store, out = Path(a.store), Path(a.out) / "checkpoints"; out.mkdir(parents=True, exist_ok=True)
    tasks = json.loads((REPO / "benchmark" / "editing" / "tasks.json").read_text())
    keys = sorted({t["checkpoint"]["key"] for t in tasks})
    mf = REPO / "benchmark" / "editing" / "checkpoints.json"; m = json.loads(mf.read_text())
    missing = [k for k in keys if not (store / k / "deliverable" / "mod").is_dir()]
    if missing: sys.exit(f"not in the store: {', '.join(missing)}")
    for k in keys:
        src = store / k; arc = out / f"{k}.tar.gz"
        def keep(ti):
            parts = Path(ti.name).parts
            if any(p in SKIP_DIRS for p in parts): return None
            if len(parts) > 1 and parts[1] == "p_baseline":
                if a.no_baselines or len(parts) != 3 or not parts[2].endswith(".json"): return None
            ti.uid = ti.gid = 0; ti.uname = ti.gname = ""; ti.mode |= 0o200
            return ti
        with tarfile.open(arc, "w:gz") as t: t.add(src, arcname=k, filter=keep)
        m["checkpoints"][k] = {"file": f"checkpoints/{k}.tar.gz", "sha256": sha256(arc), "size_bytes": arc.stat().st_size}
        print(f"{k}: {arc.stat().st_size / 2**20:.1f} MB")
    mf.write_text(json.dumps(m, indent=1) + "\n")
    print(f"{len(keys)} archives in {out}; manifest updated: {mf}")


if __name__ == "__main__":
    main()

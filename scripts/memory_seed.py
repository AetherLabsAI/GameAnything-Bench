"""Build gab/refine/memory_seed/ (the seed of the refine rounds' experience memory, gab/refine/memory.py; see gab/refine/memory_seed/README.md) from
a GameAnything checkout: the builder section (`## 开发` in GameAnything's Chinese format) of the five commercial games'
LESSONS.md and the builder role file, through a privacy scan. The seed is written in the bench's format (section
`## Development`, English field names symptom / cause / fix / evidence / confidence; gab/refine/memory.py reads both).

    python scripts/memory_seed.py <GameAnything checkout> [--scan PRIVATE_PATTERNS] [--write]

Scan (per field of each entry): GameAnything's private_hits, plus references to the GameAnything user, private project names
and records, and content of a user's own project. A hit in symptom / fix drops the entry; a hit in cause drops that line; a hit
in evidence replaces the evidence by the seed's provenance. Seeded lines never carry a hit. The full hit list (with the matched
text) is printed for the maintainer only; gab/refine/memory_seed/SEED.json records ids and hit categories, never the matched text.
Then the bench filter: scripts/memory_seed_curation.yaml lists every entry that passed the scan: dropped (with
a reason: GameAnything platform tooling, not true in the bench containers, contradicts the game's devkit README) or kept with
its English text (symptom / cause / fix). The English text is scanned again.

SEED.json, per seed file: source_file, source_sha256, section (the source's section name), counts (entries_in_source,
after_privacy_scan, kept), kept [ids], translated ("all" or [ids]), evidence_replaced [ids] (the source's evidence named a
private project or record and was replaced by the seed's provenance), lines_dropped {id: {field: [categories]}} (only for any
other line left out), adapted {id: note}, dropped {id: reason, or [reasons] when several}."""
import argparse, hashlib, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gab.refine import memory as M

GAMES = {"minecraft": "minecraft", "cities_skylines": "cities_skylines", "stardew_valley": "stardew", "terraria": "terraria",
         "valheim": "valheim"}           # bench id -> GameAnything id
SCAN = [(re.compile(r"她|用户本人|用户觉得"), "refers to the GameAnything user (her / the user's own view)")]
# names of private projects / records / a user's own content are not in the repository: give them with --scan FILE (one
# regular expression per line, "# " comments allowed); each becomes a "a private project name or record" check


def load_scan(path):
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"): SCAN.append((re.compile(ln, re.I), "a private project name or record"))


def hits(text):
    out = [(w, None) for w in M.private_hits(text)]
    for rx, what in SCAN:
        m = rx.search(text)
        if m: out.append((what, m.group(0)))
    return out


SOURCE_SECTION = "开发"          # GameAnything's builder section (its Chinese format); parse() maps it to M.SECTION
PRIV = "a private project name or record"


def section(text, name=SOURCE_SECTION):
    """the entries under `## <name>` only (the source's entries, field names mapped to English by M.parse)"""
    name = M.SECTION_ALIAS.get(name, name)
    return [c["entry"] for c in M.parse(text) if "entry" in c and c["entry"].get("_section") == name]


def scrub(es, src_rel, game):
    kept, dropped, report = [], [], []
    for e in es:
        e = {k: v for k, v in e.items() if k not in ("_section",)}; e["_extra"] = []
        fh = {k: hits(str(e.get(k) or "")) for k in ("symptom", "cause", "fix", "evidence")}
        for k, hs in fh.items():
            for what, m in hs: report.append((e["id"], k, what, m))
        prov = f"seed: GameAnything {src_rel} {e['id']}"
        if fh["symptom"] or fh["fix"]:
            dropped.append({"id": e["id"], "why": sorted({w for k in ("symptom", "fix") for w, _ in fh[k]})}); continue
        lines = {}
        if fh["cause"]: e.pop("cause", None); lines["cause"] = sorted({w for w, _ in fh["cause"]})
        if fh["evidence"] or not e.get("evidence"): e["evidence"] = prov; lines["evidence"] = sorted({w for w, _ in fh["evidence"]}) or ["(empty)"]
        else: e["evidence"] = e["evidence"] + "\n" + prov
        if game: e["game"] = game
        assert not hits(" ".join(str(e.get(k) or "") for k in ("symptom", "cause", "fix", "evidence"))), e["id"]
        kept.append({"id": e["id"], **({"lines_dropped": lines} if lines else {})}); e["_kept"] = True
        kept[-1]["_e"] = e
    return kept, dropped, report


CURATION = Path(__file__).resolve().parent / "memory_seed_curation.yaml"


def curate(kept, dropped, rel, src_rel, cur):
    """the bench filter (memory_seed_curation.yaml): drop with a reason, or keep with the English text"""
    c = cur.get(rel) or {}
    missing = [k["id"] for k in kept if k["id"] not in c]
    if missing: raise SystemExit(f"{rel}: not in {CURATION.name}: {missing}")
    out = []
    for k in kept:
        d = c[k["id"]]; e = k["_e"]
        if "drop" in d:
            dropped.append({"id": k["id"], "why": [f"bench filter: {d['drop']}"]}); continue
        d = {M._key(x): v for x, v in d.items()}          # the curation file's keys: symptom / cause / fix (old: 现象 / 原因 / 做法)
        for f in ("symptom", "cause", "fix"):
            if d.get(f): e[f] = " ".join(str(d[f]).split())
            else: e.pop(f, None)
        e["evidence"] = f"seed: GameAnything {src_rel} {k['id']} (translated" + (", adapted to the bench" if d.get("adapted") else "") + ")"
        txt = " ".join(str(e.get(f) or "") for f in ("symptom", "cause", "fix", "evidence"))
        if hits(txt) or M.guard_hits(txt): raise SystemExit(f"{k['id']}: the English text has a hit: {hits(txt) + M.guard_hits(txt)}")
        if any("\u4e00" <= ch <= "\u9fff" for f in ("symptom", "cause", "fix") for ch in str(e.get(f) or "")):
            raise SystemExit(f"{k['id']}: not translated")
        k = {**k, "translated": True, **({"adapted": d["adapted"]} if d.get("adapted") else {})}
        out.append(k)
    return out


def file_record(srel, sha, section_name, n_source, n_priv, kept, dropped):
    """one seed file's SEED.json record from the per-entry kept [{id, lines_dropped?, translated?, adapted?}] and dropped
    [{id, why: [..]}] lists: grouped by fact instead of repeated per entry"""
    ids = [k["id"] for k in kept]
    tr = [k["id"] for k in kept if k.get("translated")]
    rec = {"source_file": srel, "source_sha256": sha, "section": section_name,
           "counts": {"entries_in_source": n_source, "after_privacy_scan": n_priv, "kept": len(kept)},
           "kept": ids, "translated": "all" if tr == ids else tr,
           "evidence_replaced": [k["id"] for k in kept if (k.get("lines_dropped") or {}).get("evidence") == [PRIV]]}
    other = {k["id"]: {f: v for f, v in k["lines_dropped"].items() if not (f == "evidence" and v == [PRIV])}
             for k in kept if k.get("lines_dropped")}
    other = {i: v for i, v in other.items() if v}
    if other: rec["lines_dropped"] = other
    rec["adapted"] = {k["id"]: k["adapted"] for k in kept if k.get("adapted")}
    rec["dropped"] = {d["id"]: (d["why"][0] if len(d["why"]) == 1 else d["why"]) for d in dropped}
    return rec


def dumps(meta):
    """SEED.json text: indented, lists of plain values on one line"""
    txt = json.dumps(meta, ensure_ascii=False, indent=1)
    return re.sub(r"\[\n\s*((?:\"[^\"\n]*\",?\n\s*)+)\]",
                  lambda m: "[" + ", ".join(x.strip().rstrip(",") for x in m.group(1).split("\n") if x.strip()) + "]", txt) + "\n"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("src"); ap.add_argument("--write", action="store_true")
    ap.add_argument("--scan", help="a file of private-name patterns (one regular expression per line)")
    a = ap.parse_args(); src = Path(a.src)
    if a.scan: load_scan(a.scan)
    try: commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    except Exception: commit = None
    out = M.SEED_DIR; meta = {"source": "GameAnything", "commit": commit, "files": {}}
    jobs = [(f"games/{g}.md", f"gameanything/games/{ga}/LESSONS.md", g) for g, ga in GAMES.items()]
    jobs.append(("roles/builder.md", "skills/gameanything/references/lessons/builder.md", None))
    all_report = []
    import yaml
    cur = yaml.safe_load(CURATION.read_text(encoding="utf-8"))
    for rel, srel, game in jobs:
        f = src / srel; text = f.read_text(encoding="utf-8")
        es = section(text) if game else M.entries(text)
        kept, dropped, report = scrub(es, srel, game)
        n_priv = len(kept)
        kept = curate(kept, dropped, rel, srel, cur)
        all_report += [(rel, *x) for x in report]
        head = (M.game_template(game).rstrip("\n").rsplit(f"\n## {M.SECTION}", 1)[0] if game else M.role_template().rstrip("\n"))
        head += (f"\nSeeded from GameAnything {srel}" + (" (builder section)" if game else "") + f" at commit {commit or '?'}, "
                 "privacy-scanned by scripts/memory_seed.py (entries or lines with a hit are left out), filtered to what holds in "
                 "the bench containers and translated to English (scripts/memory_seed_curation.yaml).\n")
        chunks = M.parse(head + (f"\n## {M.SECTION}\n" if game else ""))
        for k in kept: chunks.append({"entry": {x: v for x, v in k.pop("_e").items() if x != "_kept"}})
        for i, c in enumerate(chunks[:-1]):
            if "raw" in c and "entry" in chunks[i + 1] and c["raw"] and c["raw"][-1] != "": c["raw"].append("")
        txt = M.unparse(chunks)
        meta["files"][rel] = file_record(srel, hashlib.sha256(f.read_bytes()).hexdigest(), SOURCE_SECTION if game else None,
                                         len(es), n_priv, kept, dropped)
        print(f"{rel}: {len(es)} entries in the source, {n_priv} after the privacy scan, {len(kept)} kept, {len(dropped)} dropped, "
              f"{sum(1 for k in kept if k.get('lines_dropped'))} with a line left out")
        if a.write: (out / rel).parent.mkdir(parents=True, exist_ok=True); (out / rel).write_text(txt, encoding="utf-8")
    if a.write: (out / "SEED.json").write_text(dumps(meta))
    print("\nhits (maintainer only; not written anywhere):")
    for rel, i, k, what, m in all_report: print(f"  {rel} {i} {k}: {what}" + (f" [{m}]" if m else ""))


if __name__ == "__main__":
    main()

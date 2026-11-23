"""Cross-task experience memory ("lessons") for the +Ours refinement rounds (`gab refine`). Ported from
GameAnything (gameanything/lessons.py, docs/feedback-loop.md; paper section "Cross-Task Experience Memory"). Never used by
`gab run` (the native agent setting); on by default in every refine round, `gab refine --no-memory` turns it off (ablation).

One memory per configuration: <memory_dir>/<run name>/ (default memory_dir = <runs_dir>/../memory), so two models never share
lessons. In GameAnything's markdown entry format, so its tools read the files:

    games/<game>.md      game knowledge, section `## Development`        commercial games seeded from GameAnything's LESSONS.md
                                                                      (gab/refine/memory_seed/: privacy-scanned, bench-filtered, English);
                                                                      open-source games start empty
    roles/builder.md     general development practice (any game)        seeded from GameAnything's builder role file
    SEED.json            where each seeded file came from (source file + sha256, kept / dropped entries; gab/refine/memory_seed/README.md)
    log.jsonl            one line per retrieval and per extraction (the retrieval records: ids given, which ones the round used)

    ### stardew_valley-001
    - date: 2026-11-12
    - game: stardew_valley
    - role: builder
    - symptom: <issue>      - cause: <cause>      - fix: <corrective action>      - evidence: task <id> (<level>) refine r<k>: <evidence>
    - confidence: tried once | verified repeatedly
    - status: candidate | confirmed

Field names and the section heading are English. Files in GameAnything's original Chinese names (section `## 开发`, fields
现象 / 原因 / 做法 / 证据 / 可信度, as in stores written before 2026-11-23) are still read: parse() maps them to the English names
(ALIAS, SECTION_ALIAS), and a store rewritten by add() comes out in English.

A refine round (gab/refine/refine.py):
  Retrieve   before the agent starts: <work>/lessons.md = this game's lessons + the builder role lessons under a budget of
             memory_budget_chars characters (confirmed first; within a group by relevance to the task request + the review,
             then confidence, then recency); REFINE_MSG gets one line pointing to it. The ids and a hash of the memory
             (snapshot) go into the round's record (agent/memory_r<k>.json -> agent.json refine_rounds[].memory).
  Extract    after the after-review: one LLM call through the critic's route (critic_base_url, key from the env var
             critic_key_env read at call time) on public / agent-side material only -- the task request, the review before
             (feedback.md, scores.json), the agent's own last messages, the files it changed, the review after -- never the
             evaluation, rubrics, hidden references, result.json or judge output. -> 0..memory_max_lessons lessons, validated,
             privacy / hidden-material guard (guard_hits), then
  Consolidate  add(): a lesson whose symptom + cause is like an existing one in the same file (SIMILAR) is folded into it (one
             more evidence line). Promotion rule (no human reviewer in the benchmark): a lesson folded again from ANOTHER task
             becomes `verified repeatedly` and `confirmed`; folded again from the same task it only gets the evidence line.
             A new one is appended as `tried once` / `candidate`. A failed extraction never fails the round (logged, recorded).
Writers of one memory (parallel trials) serialize on a file lock (<root>/.lock).

Refine ablation `full` (gab refine --ablation full; trial.json refine_ablation.setting == "full"): the new run's
memory (<memory_dir>/<new run>/) starts ONCE as a frozen copy of the SOURCE run's memory on this machine (copy_store: under the new
store's lock; COPIED_FROM.json = source path + sha256 of every copied file; the source is only read), later extraction writes go
into the copy only. At retrieval the current task's own experience is left out (exclude_task): an entry whose evidence is only
this task is dropped, an entry with evidence from other tasks (or the GameAnything seed) is kept without this task's evidence
lines (and shown as `candidate` / `tried once` when its promotion came from this task alone)."""
import contextlib, difflib, fcntl, hashlib, json, math, os, re, shutil, time, urllib.error, urllib.request
from datetime import date
from pathlib import Path

from .. import config
from ..util import log

ROLE = "builder"
SECTION = "Development"
FIELDS = ("date", "game", "role", "symptom", "cause", "fix", "evidence", "confidence", "status")
# compat: GameAnything's original (Chinese) field names and section heading, read and mapped to the English ones
ALIAS = {"现象": "symptom", "原因": "cause", "做法": "fix", "证据": "evidence", "可信度": "confidence", "state": "status",
         "日期": "date", "游戏": "game", "角色": "role", "状态": "status"}
SECTION_ALIAS = {"开发": SECTION, "development": SECTION}
CONF = ("tried once", "verified repeatedly")
SIMILAR = 0.72                     # GameAnything lessons.SIMILAR: symptom + cause similarity at which a lesson is folded
GENERAL = "any"
SEED_DIR = Path(__file__).resolve().parent / "memory_seed"
SEED_GAMES = ("minecraft", "cities_skylines", "stardew_valley", "terraria", "valheim")   # commercial games: seeded

HEAD_RE = re.compile(r"^###\s+(\S+)\s*$")
FIELD_RE = re.compile(r"^-\s*([^:：]+?)\s*[:：]\s?(.*)$")
TASK_RE = re.compile(r"\btask (\S+?) \(")


class MemoryError_(ValueError):
    pass


def _cfg(k, default=None):
    try: v = config.get(k)
    except KeyError: return default
    return default if v is None else v


def enabled_default():
    v = _cfg("memory", True)
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


def root(name) -> Path:
    """the memory of one configuration (= one --name run)"""
    base = _cfg("memory_dir") or (config.path("runs_dir").parent / "memory")
    if not name or "/" in str(name) or str(name) in (".", ".."): raise MemoryError_(f"bad run name {name!r}")
    return Path(base) / str(name)


def game_file(r, game): return Path(r) / "games" / f"{game}.md"
def role_file(r): return Path(r) / "roles" / f"{ROLE}.md"


@contextlib.contextmanager
def locked(r):
    r = Path(r); r.mkdir(parents=True, exist_ok=True)
    with open(r / ".lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(fh, fcntl.LOCK_UN)


# ---------------------------------------------------------------- parse / render (GameAnything lessons.py)
def _key(k):
    k = k.strip().strip("*`").strip()
    return ALIAS.get(k.lower(), ALIAS.get(k, k.lower() if k.isascii() else k))


def parse(text):
    """-> chunks: {"raw": [lines]} or {"entry": {id, fields..., "_section", "_extra"}}; unknown lines kept as they are"""
    out, cur, section, last, raw = [], None, None, None, []
    def flush():
        if raw: out.append({"raw": list(raw)}); raw.clear()
    for line in text.replace("\r\n", "\n").split("\n"):
        h = HEAD_RE.match(line)
        if h:
            flush(); cur = {"id": h.group(1), "_section": section, "_extra": []}; out.append({"entry": cur}); last = None; continue
        if line.startswith("## ") or line.startswith("# "):
            flush(); cur = None
            if line.startswith("## "):
                section = line[3:].strip(); section = SECTION_ALIAS.get(section, SECTION_ALIAS.get(section.lower(), section))
                if section == SECTION: line = f"## {SECTION}"          # the old heading (## 开发) comes out in English
            raw.append(line); continue
        if cur is None: raw.append(line); continue
        f = FIELD_RE.match(line)
        if f: last = _key(f.group(1)); cur[last] = f.group(2).strip(); continue
        if line.startswith("  ") and line.strip() and last: cur[last] = (cur[last] + "\n" + line.strip()).strip(); continue
        if not line.strip(): continue
        cur["_extra"].append(line)
    flush()
    if out and "raw" in out[-1] and out[-1]["raw"] == [""]: out.pop()
    return out


def entries(text): return [c["entry"] for c in parse(text) if "entry" in c]


def render(e, keys=None):
    lines = [f"### {e['id']}"]
    ks = keys or ([k for k in FIELDS if e.get(k) not in (None, "")] +
                  [k for k in e if not k.startswith("_") and k not in FIELDS and k != "id" and e[k] not in (None, "")])
    for k in ks:
        if e.get(k) in (None, ""): continue
        v = str(e[k]).split("\n")
        lines.append(f"- {k}: {v[0]}"); lines += ["  " + x for x in v[1:]]
    return lines + list(e.get("_extra") or []) + [""]


def unparse(chunks):
    lines = []
    for c in chunks: lines += c["raw"] if "raw" in c else render(c["entry"])
    while lines and lines[-1] == "": lines.pop()
    return "\n".join(lines) + "\n"


def read(f):
    try: return parse(Path(f).read_text(encoding="utf-8"))
    except FileNotFoundError: return []


def write(f, chunks):
    f = Path(f); f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name("." + f.name + ".tmp"); tmp.write_text(unparse(chunks), encoding="utf-8"); tmp.replace(f)


# ---------------------------------------------------------------- guards
# GameAnything lessons.PRIVATE_RES (private_hits), unchanged
PRIVATE_RES = [(re.compile(r"\b[\w.-]+\.(?:jpe?g|heic|png|webp|mp4|mov|m4a|wav)\b", re.I), "a photo / video / audio file name"),
               (re.compile(r"(?:^|[\\/\s])(?:0_inbox|1_ingest|2_perceive|3_remember|conversation)[\\/]"), "a path into a project's own materials"),
               (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "an e-mail address"),
               (re.compile(r"(?:/home/|/Users/|C:\\Users\\)([^/\\\s]+)", re.I), "a path with a user name")]
# the benchmark's hidden material: a lesson must never carry (or point to) the evaluation
HIDDEN_RES = [(re.compile(r"rubric", re.I), "mentions a rubric"),
              (re.compile(r"\bjudg(?:e|es|ed|ing)\b", re.I), "mentions a judge"),
              (re.compile(r"\bhidden\b", re.I), "mentions hidden material"),
              (re.compile(r"result\.json|judge[_ -]?output|keyframe", re.I), "names an evaluation file"),
              (re.compile(r"\bgrad(?:er|ing)\b", re.I), "mentions grading"),
              (re.compile(r"\b(?:eval|evaluation|benchmark)\b[^.\n]{0,40}\b(?:score|result|pass|fail|verdict)", re.I),
               "mentions the benchmark evaluation"),
              (re.compile(r"\bgab (?:eval|score|table)\b", re.I), "names an evaluation command")]
KEY_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}|\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b")


def private_hits(text, names=()):
    out = [what for rx, what in PRIVATE_RES if rx.search(text)]
    return out + [f"the name {n!r}" for n in names if n and n in text]


def guard_hits(text, extra_paths=()):
    """why a lesson may not go into the memory ([] = fine): GameAnything's privacy guard + hidden material + secrets"""
    out = private_hits(text) + [what for rx, what in HIDDEN_RES if rx.search(text)]
    if KEY_RE.search(text): out.append("looks like a credential")
    out += [f"a path of the benchmark's own data" for p in extra_paths if p and str(p) in text]
    return out


def hidden_paths():
    out = []
    for k in ("hidden_dir", "rubrics_dir"):
        try: out.append(str(config.path(k)))
        except Exception: pass
    return out


def _under_hidden(p):
    p = Path(p).resolve()
    for h in hidden_paths():
        try:
            if p.is_relative_to(Path(h).resolve()): return True
        except Exception: pass
    return False


# ---------------------------------------------------------------- similarity (GameAnything lessons.similarity)
def _norm(s):
    return re.sub(r"[\s`'\"“”‘’，。、；：,.;:!?！？()（）\[\]【】<>-]+", " ", str(s or "").lower()).strip()


def similarity(a, b):
    r = lambda x, y: difflib.SequenceMatcher(None, _norm(x), _norm(y)).ratio() if _norm(x) and _norm(y) else None
    s, c = r(a.get("symptom"), b.get("symptom")), r(a.get("cause"), b.get("cause"))
    if s is None: return c or 0.0
    return s if c is None else 0.6 * s + 0.4 * c


# ---------------------------------------------------------------- seeding
def game_template(game):
    return (f"# {game}: lessons (builder)\n\n"
            "Experience memory of this benchmark configuration (gab/refine/memory.py). Format: GameAnything "
            "skills/gameanything/references/lessons.md.\n\n" f"## {SECTION}\n")


def role_template():
    return ("# builder: lessons (any game)\n\nExperience memory of this benchmark configuration (gab/refine/memory.py). "
            "Format: GameAnything skills/gameanything/references/lessons.md.\n")


def ensure(r, game):
    """seed this configuration's memory on first use (call under locked(r)): the role file, and the game's file (from the
    GameAnything seed for a commercial game, empty for an open-source one). -> SEED.json content"""
    r = Path(r); sj = r / "SEED.json"
    seed = json.loads(sj.read_text()) if sj.exists() else {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": {}}
    src_meta = json.loads((SEED_DIR / "SEED.json").read_text()) if (SEED_DIR / "SEED.json").exists() else {}
    def put(rel, src, tpl):
        f = r / rel
        if f.exists(): return
        f.parent.mkdir(parents=True, exist_ok=True)
        if src is not None and src.exists():
            shutil.copy(src, f)
            seed["files"][rel] = {"seeded_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "from": f"gab/refine/memory_seed/{rel}",
                                  "seed_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                                  "source": (src_meta.get("files") or {}).get(rel)}
        else:
            f.write_text(tpl, encoding="utf-8")
            seed["files"][rel] = {"seeded_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "from": None, "note": "starts empty"}
    put(f"roles/{ROLE}.md", SEED_DIR / "roles" / f"{ROLE}.md", role_template())
    if game:
        put(f"games/{game}.md", SEED_DIR / "games" / f"{game}.md" if game in SEED_GAMES else None, game_template(game))
    sj.write_text(json.dumps(seed, ensure_ascii=False, indent=1))
    return seed


def _kept_ids(src):
    """the kept ids of one file's record in gab/refine/memory_seed/SEED.json: `kept: [ids]`; the older per-entry form
    `kept: [{id, ...}]` is read too. None when the record has no list."""
    k = (src or {}).get("kept")
    if not isinstance(k, list): return None
    return {x["id"] if isinstance(x, dict) else str(x) for x in k}


def seeded_ids(r):
    """ids of the entries of memory r that came from the GameAnything seed (by the source record copied into r/SEED.json,
    else by the seed file of this checkout)"""
    out = set(); sj = Path(r) / "SEED.json"
    for rel, m in (json.loads(sj.read_text()).get("files") or {}).items() if sj.exists() else []:
        if not m.get("from"): continue
        ids = _kept_ids(m.get("source"))
        if ids is None: ids = {e["id"] for e in entries((SEED_DIR / rel).read_text(encoding="utf-8"))} if (SEED_DIR / rel).exists() else set()
        out |= ids
    return out


# ---------------------------------------------------------------- refine ablation `full`: a frozen copy of the source's memory
COPY_MARK = "COPIED_FROM.json"
COPY_SKIP = {".lock", "log.jsonl", COPY_MARK}          # the source's lock and its retrieval / extraction log stay with the source


def _sha(b): return hashlib.sha256(b).hexdigest()


def copy_store(r, src):
    """make the memory r a frozen copy of the memory src, once (under r's lock; parallel trials and later calls get the record
    of the first copy). src is only read; every copied file's sha256 (of the bytes read from src, checked again on the copy) and
    the source path go into r/COPIED_FROM.json, written last. Refused: a missing / empty source, r == src, or an r that already
    has lesson files but no COPIED_FROM.json (not a copy). -> the COPIED_FROM.json content"""
    r, src = Path(r), Path(src)
    if r.resolve() == src.resolve(): raise MemoryError_(f"{r}: the memory copy must be another directory than its source")
    with locked(r):
        mark = r / COPY_MARK
        if mark.exists():
            rec = json.loads(mark.read_text())
            if Path(rec.get("source") or "").resolve() != src.resolve():
                raise MemoryError_(f"{r}: copied from {rec.get('source')}, not {src}: refused")
            return rec
        if any((r / d).is_dir() and any((r / d).glob("*.md")) for d in ("games", "roles")) or (r / "SEED.json").exists():
            raise MemoryError_(f"{r}: has lessons but no {COPY_MARK} (not a copy of {src}): refused")
        files = sorted(f for f in src.rglob("*") if f.is_file() and f.relative_to(src).parts[0] not in COPY_SKIP
                       and not f.name.startswith("."))
        if not any(f.suffix == ".md" for f in files): raise MemoryError_(f"{src}: no source memory (no lesson files) to copy")
        got = {}
        for f in files:
            rel = str(f.relative_to(src)); b = f.read_bytes(); h = _sha(b)
            out = r / rel; out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name("." + out.name + ".tmp"); tmp.write_bytes(b); tmp.replace(out)
            if _sha(out.read_bytes()) != h: raise MemoryError_(f"{out}: the copy differs from {f}")
            got[rel] = h
        rec = {"source": str(src.resolve()), "copied_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": got,
               "why": "refine ablation full: the source run's memory, frozen when the first trial of this run started"}
        tmp = mark.with_name("." + mark.name + ".tmp"); tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1)); tmp.replace(mark)
        return rec


EV_TASK_RE = re.compile(r"^task (\S+) \((\w+)\) refine r\d+")   # one evidence line written by after_round (ev0)


def exclude_task(es, task_id):
    """the entries without the experience of task task_id (exact id, e.g. T-TO-txt_884615e315af.e1 is not .e2; any level):
    an entry whose evidence lines all come from this task is dropped; one with other evidence (another task, a seed line) is kept
    without this task's lines, and when it was promoted (confirmed / verified repeatedly) but what is left is one source only (one
    task or the seed), it is shown as candidate / tried once. -> (entries, {excluded, stripped_lines, demoted, excluded_ids})"""
    out, st = [], {"excluded": 0, "stripped_lines": 0, "demoted": 0, "excluded_ids": [], "stripped_ids": []}
    for e in es:
        lines = [x.strip() for x in str(e.get("evidence") or "").split("\n") if x.strip()]
        own = [x for x in lines if (m := EV_TASK_RE.match(x)) and m.group(1) == task_id]
        if not own: out.append(e); continue
        rest = [x for x in lines if x not in own]
        if not rest:
            st["excluded"] += 1; st["excluded_ids"].append(e["id"]); continue
        e = {**e, "evidence": "\n".join(rest)}
        st["stripped_lines"] += len(own); st["stripped_ids"].append(e["id"])
        srcs = {(m.group(1) if (m := EV_TASK_RE.match(x)) else "seed") for x in rest}
        if len(srcs) < 2 and (e.get("status") == "confirmed" or e.get("confidence") == "verified repeatedly"):
            e["status"] = "candidate"; e["confidence"] = "tried once"; st["demoted"] += 1
        out.append(e)
    return out, st


# ---------------------------------------------------------------- retrieval
_STOP = set("the and for with that this are not but you your from into have has was were its can all any one two out use "
            "when what which then than them they will would should could game mod make made also each more most only same".split())


def _tokens(s):
    s = str(s or "").lower()
    w = {t for t in re.findall(r"[a-z_][a-z0-9_.]{2,}", s) if t not in _STOP}
    cjk = re.findall(r"[\u4e00-\u9fff]+", s)
    return w | {x[i:i + 2] for x in cjk for i in range(len(x) - 1)}


def relevance(e, query_tokens):
    t = _tokens(" ".join(str(e.get(k) or "") for k in ("symptom", "cause", "fix")))
    if not t or not query_tokens: return 0.0
    return len(t & query_tokens) / math.sqrt(len(t))


def _view(e, layer):
    return {**e, "_layer": layer}


def collect(r, game):
    out = [_view(e, "game") for e in entries(game_file(r, game).read_text(encoding="utf-8"))] if game_file(r, game).exists() else []
    out += [_view(e, "role") for e in entries(role_file(r).read_text(encoding="utf-8"))] if role_file(r).exists() else []
    return [e for e in out if (e.get("role") or ROLE) == ROLE and e.get("symptom") and e.get("fix")]


def snapshot(r, game):
    h = hashlib.sha256()
    for f in (game_file(r, game), role_file(r)):
        h.update(f.name.encode() + b"\0" + (f.read_bytes() if f.exists() else b"") + b"\0")
    return h.hexdigest()[:16]


def rank(es, query=""):
    """confirmed first; within a group: relevance to the query (request + review), confidence, recency, game before role"""
    q = _tokens(query)
    for e in es: e["_rel"] = round(relevance(e, q), 3)
    return sorted(es, key=lambda e: (e.get("status") != "confirmed", -e["_rel"], e.get("confidence") != "verified repeatedly",
                                     -int(re.sub(r"\D", "", str(e.get("date") or "")) or 0), e["_layer"] != "game", e["id"]))


SHOW_KEYS = ("symptom", "cause", "fix", "evidence", "confidence", "status")
LESSONS_HEAD = """# Lessons from earlier tasks ({game}, development)

Advice that earlier development rounds wrote down: the issue (symptom), its cause, what to do (fix), evidence.
`confirmed` = held in more than one task; `candidate` = seen once, check it yourself. It may not apply to this task: the
request and the review come first.
"""


def _brief(e):
    e = dict(e)
    if e.get("evidence"): e["evidence"] = str(e["evidence"]).split("\n")[-1][:200]      # the newest evidence line only
    return "\n".join(render(e, SHOW_KEYS))


def retrieve(r, game, query="", budget=None, exclude=None, stats=None):
    """-> (text of lessons.md or "", [ids], snapshot): the ranked lessons that fit the budget (characters, whole file).
    exclude: a task id whose own experience is left out (exclude_task; refine ablation `full`), its counts go into stats (a dict)"""
    budget = int(budget or _cfg("memory_budget_chars", 6000))
    es = collect(r, game)
    if exclude:
        es, st = exclude_task(es, exclude)
        if stats is not None: stats.update(st)
    es = rank(es, query)
    head = LESSONS_HEAD.format(game=game)
    parts, ids, n = [], [], len(head)
    groups = {True: "\n## Confirmed\n", False: "\n## Candidates\n"}
    used_groups = set()
    for e in es:
        conf = e.get("status") == "confirmed"
        b = _brief(e) + "\n"
        extra = len(groups[conf]) if conf not in used_groups else 0
        if n + extra + len(b) > budget: continue                     # skip what does not fit; a shorter one may
        if extra: parts.append(groups[conf]); used_groups.add(conf)
        parts.append(b); ids.append(e["id"]); n += extra + len(b)
    if not ids: return "", [], snapshot(r, game)
    return head + "".join(parts), ids, snapshot(r, game)


# ---------------------------------------------------------------- consolidation (GameAnything lessons.add)
def _next_id(prefix, chunks_list):
    n = 0
    for chunks in chunks_list:
        for c in chunks:
            m = re.match(rf"^{re.escape(prefix)}-(\d+)$", c["entry"]["id"]) if "entry" in c else None
            if m: n = max(n, int(m.group(1)))
    return f"{prefix}-{n + 1:03d}"


def _heading(c, t=None):
    return "raw" in c and c["raw"] and c["raw"][0].startswith("## ") and (t is None or c["raw"][0].strip() == f"## {t}")


def add(r, game, scope, symptom, cause="", fix="", evidence="", task=None, today=None):
    """append a candidate, or fold it into a similar entry of the same file (call under locked(r)).
    -> {action: added | merged, id, file, promoted, similar}"""
    if not str(symptom or "").strip() or not str(fix or "").strip(): raise MemoryError_("a lesson needs a symptom and a fix")
    general = scope == "general" or not game
    f = role_file(r) if general else game_file(r, game)
    chunks = read(f) if f.exists() else parse(role_template() if general else game_template(game))
    new = {"symptom": str(symptom).strip(), "cause": str(cause or "").strip()}
    sims = sorted(((c["entry"]["id"], similarity(new, c["entry"])) for c in chunks if "entry" in c), key=lambda x: -x[1])
    if sims and sims[0][1] >= SIMILAR:
        e = next(c["entry"] for c in chunks if "entry" in c and c["entry"]["id"] == sims[0][0])
        seen_tasks = set(TASK_RE.findall(str(e.get("evidence") or "")))
        if evidence and evidence not in str(e.get("evidence") or ""): e["evidence"] = (str(e.get("evidence") or "") + "\n" + evidence).strip()
        if not e.get("cause") and cause: e["cause"] = str(cause).strip()
        promoted = False
        if task and task not in seen_tasks:     # met again in another task (a seeded lesson: in a first task): promoted
            promoted = e.get("status") != "confirmed"
            e["confidence"] = "verified repeatedly"; e["status"] = "confirmed"
        write(f, chunks)
        return {"action": "merged", "id": e["id"], "file": str(f), "promoted": promoted, "similar": sims[:3]}
    prefix = ROLE if general else game
    e = {"id": _next_id(prefix, [chunks]), "date": (today or date.today()).isoformat(), "game": GENERAL if general else game,
         "role": ROLE, "symptom": new["symptom"], "cause": new["cause"], "fix": str(fix).strip(), "evidence": str(evidence or "").strip(),
         "confidence": "tried once", "status": "candidate", "_extra": []}
    if not general:
        idx = next((i for i, c in enumerate(chunks) if _heading(c, SECTION)), None)
        if idx is None: chunks.append({"raw": [f"## {SECTION}", ""]}); idx = len(chunks) - 1
        j = idx + 1
        while j < len(chunks) and not _heading(chunks[j]): j += 1
        chunks.insert(j, {"entry": e})
    else: chunks.append({"entry": e})
    for i, c in enumerate(chunks[:-1]):
        if "raw" in c and "entry" in chunks[i + 1] and c["raw"] and c["raw"][-1] != "": c["raw"].append("")
    write(f, chunks)
    return {"action": "added", "id": e["id"], "file": str(f), "promoted": False, "similar": sims[:3]}


def log_event(r, rec):
    with open(Path(r) / "log.jsonl", "a") as fh: fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- the refine round's two steps
def run_name(d):
    """the memory name of trial dir d (<runs_dir>/<name>/<task>/<level>): the run name. An editing trial (level `edit`) keeps
    its own memory, apart from the Construction trials of the same run: <name>-edit (a run name that already has an `edit`
    part, e.g. cfg-edit-s0, is used as it is). It starts from the Construction run's lessons (gab/edit/edit.py seed_memory)."""
    d = Path(d); name = d.parent.parent.name
    if d.name == "edit" and "edit" not in name.split("-"): return f"{name}-edit"
    return name


def round_file(d, k): return Path(d) / "agent" / f"memory_r{k}.json"


def full_ablation(d):
    """the refine ablation `full` of trial d (trial.json refine_ablation.setting == "full") -> the source run's memory root on this
    machine (<memory_dir>/<source run>/), or None for any other trial (normal gab refine: unchanged)"""
    try: ra = (json.loads((Path(d) / "trial.json").read_text()).get("refine_ablation") or {})
    except (OSError, ValueError): return None
    if ra.get("setting") != "full": return None
    if not ra.get("source_trial"): raise MemoryError_(f"{d}: refine ablation full without a source_trial")
    return root(run_name(ra["source_trial"]))


def _store(d):
    """-> (the run's memory root, the full-ablation copy record or None); a `full` trial's memory is copied first (once)"""
    r = root(run_name(d)); src = full_ablation(d)
    return r, (copy_store(r, src) if src is not None else None)


def before_round(d, task, level, k, feedback_dir, w, budget=None):
    """seed if needed, retrieve, write <w>/lessons.md; -> the retrieval record (also agent/memory_r<k>.json and log.jsonl)"""
    r, cp = _store(d); game = task.game
    fb = Path(feedback_dir) / "feedback.md"
    query = task.instruction(level) + "\n" + (fb.read_text(errors="replace")[:20000] if fb.exists() else "")
    st = {}
    with locked(r):
        ensure(r, game)
        text, ids, snap = retrieve(r, game, query, budget, exclude=task.id if cp else None, stats=st)
    lf = Path(w) / "lessons.md"
    if text: lf.write_text(text, encoding="utf-8")
    else: lf.unlink(missing_ok=True)
    rec = {"memory": str(r), "game": game, "role": ROLE, "budget": int(budget or _cfg("memory_budget_chars", 6000)),
           "chars": len(text), "ids": ids, "snapshot": snap, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if cp is not None:                                   # refine ablation full: where the memory came from, what was left out
        rec["copied_from"] = cp.get("source")
        rec["own_task_excluded"] = {"task": task.id, "entries_excluded": st.get("excluded", 0),
                                    "evidence_lines_stripped": st.get("stripped_lines", 0), "demoted": st.get("demoted", 0),
                                    "excluded_ids": st.get("excluded_ids", []), "stripped_ids": st.get("stripped_ids", [])}
    from ..util import wjson
    wjson(round_file(d, k), {"round": k, "retrieval": rec})
    with locked(r): log_event(r, {"kind": "retrieval", "task": task.id, "level": level, "round": k, **rec})
    return rec


EXTRACT_PROMPT = """You keep the experience memory of game-development agents that build mods for {title}. Below is ONE
improvement round of one task: the user's request, an independent reviewer's findings before the round, what the agent did
(its own last messages, the files it changed) and the reviewer's findings after the round.

Write 0 to {n} lessons that would help a developer on a DIFFERENT task, with this game (scope "game") or with any game (scope
"general"): a concrete problem met in this round, its cause, a corrective action another developer can follow, and the evidence
from this round (what the review before / after shows). Rules:
- only what the data below supports; prefer fixes the review after the round shows worked, or problems that persisted;
- nothing specific to this one task's content (its places, characters, story); no file names, paths, URLs or person names;
- never mention any benchmark evaluation, rubric, judge or grading; the reviewer's findings are the only feedback;
- one or two sentences per field, plain English; fewer, better lessons; an empty list is fine.

Lessons the agent was given this round (id: issue). In "applied" list the ids whose advice the agent's work evidently followed.
{given}

THE REQUEST:
{request}

REVIEW BEFORE THE ROUND (feedback.md, scores.json):
{before}

WHAT THE AGENT DID (its last messages; files it changed):
{agent}

REVIEW AFTER THE ROUND:
{after}

Answer with ONE JSON object and nothing else:
{{"lessons": [{{"scope": "game|general", "symptom": "...", "cause": "...", "fix": "...", "evidence": "..."}}], "applied": ["<id>", ...]}}"""


def _read_review(cdir, cap=7000):
    cdir = Path(cdir) if cdir else None
    if not cdir or _under_hidden(cdir): return "(none)"
    parts = []
    for n in ("feedback.md", "scores.json"):
        p = cdir / n
        if p.exists(): parts.append(p.read_text(errors="replace")[:cap])
    return "\n".join(parts) or "(none)"


def agent_messages(transcript, cap=3500):
    """the agent's own text messages (not tool output, not the user's) from a harness transcript (claude / codex / opencode
    jsonl), newest last, about cap characters"""
    if not transcript or not Path(transcript).exists() or _under_hidden(transcript): return ""
    texts = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") in ("tool_result", "tool_use", "function_call_output", "command_execution", "tool") or o.get("role") in ("user", "tool"): return
            for k, v in o.items():
                if k in ("text", "result") and isinstance(v, str) and len(v.strip()) > 20: texts.append(v.strip())
                elif isinstance(v, (dict, list)): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    for line in Path(transcript).read_text(errors="replace").splitlines():
        try: o = json.loads(line)
        except ValueError: continue
        if isinstance(o, dict) and (o.get("type") == "user" or o.get("role") == "user"): continue
        walk(o)
    out, n = [], 0
    for t in reversed(texts):
        if t in out: continue
        out.insert(0, t[:1500]); n += len(out[0])
        if n > cap: break
    return "\n---\n".join(out)[-cap:]


SKIP_DIRS = {"review", "targets", ".gab", "node_modules", "bin", "obj", ".gradle", "build", ".git", "__pycache__"}


def changed_files(w, since, cap=60):
    w = Path(w); out = []
    if not w.is_dir(): return out
    for dp, dns, fns in os.walk(w):
        dns[:] = [x for x in dns if x not in SKIP_DIRS and not x.startswith(".")]
        for fn in fns:
            p = Path(dp) / fn
            if fn in ("lessons.md", "refine_brief.md") and p.parent == w: continue
            try:
                if p.stat().st_mtime >= since: out.append(str(p.relative_to(w)))
            except OSError: pass
    return sorted(out)[:cap]


def _chat_http(prompt, model):
    """one chat completion through the critic's route -> (text, usage). The key is read from the environment here and only
    put into the request header."""
    base = str(_cfg("critic_base_url", "")).rstrip("/")
    key = os.environ.get(_cfg("critic_key_env", "") or "")
    if not key: raise RuntimeError(f"no key in the environment variable named by critic_key_env")
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2,
            "max_tokens": int(_cfg("memory_max_tokens", 8000))}   # thinking tokens count here too
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=180) as r: d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"memory model HTTP {e.code}") from None
    c = (d.get("choices") or [{}])[0]
    return c.get("message", {}).get("content") or "", {**(d.get("usage") or {}), "finish_reason": c.get("finish_reason")}


_chat = _chat_http                  # (tests replace _chat; nothing else calls a model)


def _json_obj(txt):
    txt = re.sub(r"^```(?:json)?|```$", "", str(txt or "").strip(), flags=re.M).strip()
    try: return json.loads(txt)
    except ValueError: pass
    m = re.search(r"\{.*\}", txt, re.S)
    if not m: raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


def validate(obj, n_max, given=()):
    """-> (lessons [{scope, symptom, cause, fix, evidence}], applied ids, rejected [{why}])"""
    if not isinstance(obj, dict): raise ValueError("the answer is not a JSON object")
    ls = obj.get("lessons") or []
    if not isinstance(ls, list): raise ValueError("lessons is not a list")
    out, rej = [], []
    for x in ls:
        if not isinstance(x, dict): rej.append({"why": "not an object"}); continue
        y = {k: " ".join(str(x.get(k) or "").split())[:500] for k in ("symptom", "cause", "fix", "evidence")}
        y["scope"] = "general" if str(x.get("scope") or "").strip().lower() in ("general", "any", "role") else "game"
        if not y["symptom"] or not y["fix"]: rej.append({"why": "no symptom or fix"}); continue
        hits = guard_hits(" ".join(y[k] for k in ("symptom", "cause", "fix", "evidence")), hidden_paths())
        if hits: rej.append({"why": "guard: " + "; ".join(sorted(set(hits)))}); continue
        if len(out) >= n_max: rej.append({"why": f"over the limit of {n_max}"}); continue
        out.append(y)
    ap = [i for i in (obj.get("applied") or []) if isinstance(i, str) and i in set(given)]
    return out, ap, rej


def after_round(d, task, level, k, run, before_dir, after_dir, started=None):
    """extract 0..n lessons from round k and consolidate them into the run's memory. Never raises: -> the extraction record
    (stored in agent/memory_r<k>.json; refine._after puts it into refine_rounds[].memory)"""
    from ..util import rjson, wjson
    from ..game import base as gbase
    rf = round_file(d, k); rec = rjson(rf, None)
    if not rec or "retrieval" not in rec: return None                # this round ran without memory
    if rec.get("extraction"): return rec["extraction"]                # done already (a restarted review phase)
    model = _cfg("memory_model") or _cfg("critic_model")
    ex = {"model": model, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "usd": None, "tokens_in": 0, "tokens_out": 0,
          "proposed": 0, "added": [], "merged": [], "promoted": [], "rejected": [], "applied": [], "error": None}
    try:
        r, _ = _store(d); given = rec["retrieval"].get("ids") or []   # (a `full` trial: its own copy, never the source)
        with locked(r): ges = {e["id"]: e for e in collect(r, task.game)}
        gtxt = "\n".join(f"{i}: {str(ges[i].get('symptom'))[:160]}" for i in given if i in ges) or "(none)"
        try: since = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%S")) if started else 0
        except (ValueError, TypeError): since = 0
        msgs = agent_messages(getattr(run, "transcript", None) or Path(d) / "agent" / "transcript.jsonl")
        ch = changed_files(Path(d) / "work", since) if since else []
        agent_txt = (msgs or "(no messages)") + "\n\nFiles changed in the round: " + (", ".join(ch) if ch else "(unknown)")
        prompt = EXTRACT_PROMPT.format(title=gbase.title(task.game), n=int(_cfg("memory_max_lessons", 3)), given=gtxt,
                                       request=" ".join(task.instruction(level).split())[:2000], before=_read_review(before_dir),
                                       agent=agent_txt, after=_read_review(after_dir) if after_dir else "(no review after the round)")
        if any(h in prompt for h in hidden_paths() if h): raise RuntimeError("the extraction input names a hidden path; not sent")
        from ..agent.base import price_usd
        # one call, plus up to memory_extract_retries more when the answer has no JSON object (e.g. cut at max_tokens);
        # every raw answer is kept next to the round record (agent/memory_r<k>_answers.json: host side, never shown to agents)
        obj, answers, usd = None, [], 0.0
        for attempt in range(1 + max(0, int(_cfg("memory_extract_retries", 1)))):
            txt, usage = _chat(prompt, model)
            ti = int(usage.get("prompt_tokens") or 0); to = int(usage.get("completion_tokens") or 0)
            cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
            u = usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else price_usd(model, ti - cached, to, cached)
            ex["tokens_in"] += ti; ex["tokens_out"] += to; usd += u or 0.0
            answers.append({"attempt": attempt + 1, "finish_reason": usage.get("finish_reason"), "tokens_out": to,
                            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"), "text": txt})
            try: obj = _json_obj(txt); break
            except ValueError: continue
        ex["usd"] = usd; ex["attempts"] = len(answers); ex["finish_reasons"] = [a["finish_reason"] for a in answers]
        try:
            from ..util import wjson as _wj
            _wj(Path(rf).with_name(Path(rf).stem + "_answers.json"), answers)
        except Exception: pass
        if obj is None: raise ValueError(f"no JSON object in the answer ({len(answers)} attempt(s); finish {ex['finish_reasons']})")
        lessons, applied, rej = validate(obj, int(_cfg("memory_max_lessons", 3)), given)
        ex["proposed"] = len(lessons) + len(rej); ex["rejected"] = rej; ex["applied"] = applied
        ev0 = f"task {task.id} ({level}) refine r{k}"
        with locked(r):
            ensure(r, task.game)
            for l in lessons:
                res = add(r, task.game, l["scope"], l["symptom"], l["cause"], l["fix"],
                          f"{ev0}: {l['evidence']}" if l["evidence"] else ev0, task=f"{task.id}")
                ex["added" if res["action"] == "added" else "merged"].append(res["id"])
                if res["promoted"]: ex["promoted"].append(res["id"])
            log_event(r, {"kind": "extraction", "task": task.id, "level": level, "round": k, "given": given,
                          **{x: ex[x] for x in ("applied", "added", "merged", "promoted", "usd", "tokens_in", "tokens_out")},
                          "rejected": len(rej), "snapshot_after": snapshot(r, task.game)})
    except Exception as e:
        ex["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        log(f"{task.id}/{level}: refine round {k}: lesson extraction failed ({ex['error']}); the round is not affected")
    rec["extraction"] = ex; wjson(rf, rec)
    return ex


# ---------------------------------------------------------------- inspection (gab memory)
def show(name, game=None):
    r = root(name)
    if not r.is_dir(): return f"(no memory for {name} in {r})"
    fs = [game_file(r, game), role_file(r)] if game else sorted((r / "games").glob("*.md")) + [role_file(r)]
    return "\n\n".join(f"===== {f.relative_to(r)}\n" + unparse(read(f)) for f in fs if f.exists()) or "(empty)"   # English names


def stats(name):
    r = root(name)
    if not r.is_dir(): return {"memory": str(r), "exists": False}
    seeds = seeded_ids(r); files = {}
    for f in sorted((r / "games").glob("*.md")) + [role_file(r)]:
        if not f.exists(): continue
        es = entries(f.read_text(encoding="utf-8"))
        files[str(f.relative_to(r))] = {"entries": len(es), "confirmed": sum(e.get("status") == "confirmed" for e in es),
                                        "candidate": sum(e.get("status") != "confirmed" for e in es),
                                        "seeded": sum(e["id"] in seeds for e in es), "learned": sum(e["id"] not in seeds for e in es)}
    ev = [json.loads(x) for x in (r / "log.jsonl").read_text().splitlines() if x.strip()] if (r / "log.jsonl").exists() else []
    ext = [e for e in ev if e.get("kind") == "extraction"]
    return {"memory": str(r), "files": files, "retrievals": sum(e.get("kind") == "retrieval" for e in ev), "extractions": len(ext),
            "added": sum(len(e.get("added") or []) for e in ext), "merged": sum(len(e.get("merged") or []) for e in ext),
            "promoted": sum(len(e.get("promoted") or []) for e in ext), "applied": sum(len(e.get("applied") or []) for e in ext),
            "extraction_usd": round(sum(e.get("usd") or 0 for e in ext), 4),
            "extraction_tokens": sum((e.get("tokens_in") or 0) + (e.get("tokens_out") or 0) for e in ext)}

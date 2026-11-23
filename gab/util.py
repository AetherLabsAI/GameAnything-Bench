"""Small shared helpers: time stamps, hashes, json/yaml io, and the isolated Claude Code call every LLM judge uses."""
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

import yaml

from . import config


def log(msg): print(f"[gab {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def ts(s):
    """'1:05.10' / '01:05' / '0:01:05.1' / 65.1 -> seconds"""
    if isinstance(s, (int, float)): return float(s)
    p = [float(x) for x in str(s).strip().split(":")]
    return p[0] if len(p) == 1 else p[0] * 60 + p[1] if len(p) == 2 else p[0] * 3600 + p[1] * 60 + p[2]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def tree_hash(d):
    """sha256 over (relative path, file sha) of every file in d: the deliverable freeze"""
    d = Path(d); h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        h.update(f"{f.relative_to(d)}\0{sha256(f)}\n".encode())
    return h.hexdigest()


def rjson(p, default=None):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else default


def wjson(p, d):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False)); tmp.replace(p)


def ryaml(p): return yaml.safe_load(Path(p).read_text())


def wyaml(p, d, header=""):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=140))


def loose_json(txt):
    """the first {...} object in a model answer"""
    txt = txt or ""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", txt, re.S)
    s = m.group(1) if m else txt[txt.find("{"): txt.rfind("}") + 1]
    try: return json.loads(s)
    except ValueError:
        # judges sometimes emit a stray bracket (e.g. `...}}]}`): fall back to decoding each {"id": ...} item object on its own
        dec, items, i = json.JSONDecoder(), [], 0
        for mm in re.finditer(r'\{\s*"id"\s*:', s):
            if mm.start() < i: continue
            try: obj, end = dec.raw_decode(s, mm.start())
            except ValueError: continue
            if isinstance(obj, dict) and "id" in obj: items.append(obj); i = end
        if items: return {"items": items}
        raise


def claude(prompt, out_dir, name, model=None, tools=(), allowed=(), add_dirs=(), max_turns=None, timeout=1800, cwd=None, env=None, extra=()):
    """One isolated Claude Code call (like reference_curation ga_author.claude(): no settings, no MCP, no session, only the
    given tools). Stores prompt, model, raw answer, usage and cost under out_dir/<name>.*. -> (answer text, record)"""
    model = model or config.get("judge_model"); out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.prompt.md").write_text(prompt)
    cmd = [config.get("claude"), "-p", prompt, "--model", model, "--output-format", "json", "--tools", ",".join(tools),
           "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
           "--max-turns", str(max_turns or (60 if tools else 2))]
    if allowed: cmd += ["--allowedTools", *allowed]
    for d in add_dirs: cmd += ["--add-dir", str(d)]
    cmd += list(extra)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd or out_dir, env=env)
        try: res = json.loads(r.stdout)
        except Exception: res = {"is_error": True, "result": (r.stdout or "")[-3000:], "stderr": (r.stderr or "")[-2000:]}
    except subprocess.TimeoutExpired: res = {"is_error": True, "result": "timeout", "timeout": True}
    u = res.get("usage") or {}
    rec = {"name": name, "model": model, "wall_s": round(time.time() - t0, 1), "is_error": bool(res.get("is_error")),
           "timeout": bool(res.get("timeout")), "subtype": res.get("subtype"), "stderr": res.get("stderr"),
           "permission_denials": [{"tool": d.get("tool_name"), "input": str((d.get("tool_input") or {}).get("command")
                                                                            or d.get("tool_input"))[:300]}
                                  for d in res.get("permission_denials") or [] if isinstance(d, dict)],
           "usd": res.get("total_cost_usd"), "tokens_in": int(u.get("input_tokens") or 0), "tokens_out": int(u.get("output_tokens") or 0),
           "tokens_cached": int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
           "answer": res.get("result")}
    wjson(out_dir / f"{name}.json", rec)
    return res.get("result") or "", rec


def add_cost(acc, rec):
    """accumulate a judge record into an eval cost dict"""
    for k in ("tokens_in", "tokens_out", "wall_s"): acc[k] = acc.get(k, 0) + (rec.get(k) or 0)
    acc["usd"] = round(acc.get("usd", 0) + (rec.get("usd") or 0), 4)
    return acc

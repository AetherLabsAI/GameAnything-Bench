"""What a trace replay reached, and whether a later hand-in reaches less.

A refine round can lower a score when the agent rebuilds maps / levels or story triggers and its traces no longer reach the
content (the evaluator's replay ends in an earlier map than the hand-in before reached). This module reads a replay's own files (gab/game/base.py
GameAdapter.replay: <out>/replay.json + state/*.json + screenshots) and

  summarize_dir()   one trace: total steps, the step of the last state change (clock / fps fields left out), the final state
                    (map / scene / level ..., screen, position, on-screen text), the places and texts it went through, and for
                    every shot of the agent whether the screenshot / the state differs from the previous screenshot point
  describe()        the same as one short human line (`game replay`)
  compare()         a trace now vs the same trace on an earlier hand-in -> the regressions (reaches less / stops earlier /
                    ends in an earlier place / broke)
  compare_dirs()    every trace of two replay dirs (record_replays out dirs) -> report; message() -> the agent's warning

Works on replays recorded before this module existed: everything is derived from the files a replay always wrote."""
import hashlib, json, re
from pathlib import Path

import yaml

PLACE_KEYS = ("map", "location", "location_name", "scene", "level", "level_id", "track", "world", "dimension", "area", "room",
              "zone", "stage", "submap", "mission", "chapter")
POS_KEYS = ("tile", "pos", "position")
# leaves that change on their own (clocks, counters of frames): not a "state change" of the trace
NOISE = re.compile(r"(fps|uptime|time|tick|frame|clock|elapsed|timestamp|_ms$|^ms$)", re.I)


def _rj(p, default=None):
    try: return json.loads(Path(p).read_text())
    except Exception: return default


def _leaves(x, p="", out=None):
    out = {} if out is None else out
    if isinstance(x, dict) and x:
        for k, v in x.items(): _leaves(v, f"{p}.{k}" if p else str(k), out)
    elif isinstance(x, list) and x and any(isinstance(v, (dict, list)) for v in x):
        for i, v in enumerate(x): _leaves(v, f"{p}[{i}]", out)
    else: out[p] = x
    return out


def _noise(path):
    last = re.split(r"[.\[]", str(path))[-1].rstrip("]")
    return bool(NOISE.search(last))


def real_change(diff):
    """a state_diff with the clock-like leaves left out -> True when something else changed"""
    for sec in ("changed", "added", "removed"):
        for k in (diff or {}).get(sec) or {}:
            if not _noise(k): return True
    return False


def _engine(st): return (st or {}).get("engine") if isinstance((st or {}).get("engine"), dict) else {}


def place_of(st):
    """the place a snapshot is in: {key: value} of the engine's map / scene / level ... fields (scalar values only); a game
    without any of them: its screen"""
    e = _engine(st)
    p = {k: e[k] for k in PLACE_KEYS if isinstance(e.get(k), (str, int, float)) and not isinstance(e.get(k), bool) and e.get(k) != ""}
    if not p and isinstance(e.get("screen"), (str, int)): p = {"screen": e["screen"]}
    return p


def _pos(st):
    e = _engine(st)
    for k in POS_KEYS:
        v = e.get(k)
        if isinstance(v, dict) and "x" in v and "y" in v: return f"({v['x']},{v['y']})"
        if isinstance(v, (list, tuple)) and v and all(isinstance(x, (int, float)) for x in v): return "(" + ",".join(f"{x:g}" for x in v) + ")"
    pl = e.get("player")
    if isinstance(pl, dict) and isinstance(pl.get("pos"), (list, dict)): return _pos({"engine": {"pos": pl["pos"]}})
    return None


def place_text(p):
    return " ".join(f"{k} {v}" for k, v in (p or {}).items()) or "unknown place"


def final_text(f):
    """{place, screen, pos} -> 'map aw_court.tmx tile (7,3), screen ChoiceState'"""
    if not f: return "unknown state"
    s = place_text(f.get("place"))
    if f.get("pos"): s += f" at {f['pos']}"
    if f.get("screen") is not None and "screen" not in (f.get("place") or {}): s += f", screen {f['screen']}"
    return s


def _texts(st):
    t = (st or {}).get("screen_text")
    return [str(x)[:200] for x in t] if isinstance(t, list) else ([str(t)[:200]] if t else [])


def _state_key(st):
    L = {k: v for k, v in _leaves(st or {}).items() if not _noise(k)}
    return hashlib.sha1(json.dumps(L, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _img_differs(a, b):
    """two screenshot files -> True / False (a small grey thumbnail compared), None when one is missing"""
    if not a or not b or not Path(a).exists() or not Path(b).exists(): return None
    try:
        from PIL import Image, ImageChops, ImageStat
        x = Image.open(a).convert("L").resize((96, 54)); y = Image.open(b).convert("L").resize((96, 54))
        return ImageStat.Stat(ImageChops.difference(x, y)).mean[0] > 1.5
    except Exception:
        return Path(a).read_bytes() != Path(b).read_bytes()


def summarize_dir(out, trace=None, name=None):
    """a replay's out dir (replay.json, state/*.json, screenshots) [+ the trace dict] -> summary dict (or None without
    replay.json)"""
    out = Path(out); r = _rj(out / "replay.json")
    if not isinstance(r, dict): return None
    if trace is None:
        trace = {}
    tl = r.get("timeline") or []; log = r.get("log") or []
    steps = len(trace.get("steps") or []) or (max([t.get("step") or 0 for t in tl] or [0])) or len(log)
    errors = [x for x in log if isinstance(x, str) or (isinstance(x, dict) and "error" in x)]
    snaps = [_rj(out / "state" / "start.json", {})] + [_rj(out / t["snapshot"], None) if t.get("snapshot") else None for t in tl]
    last = 0; places, texts, keys, shots = [], [], set(), []
    seen_p = set(); seen_t = set()
    def add_snap(st):
        if not isinstance(st, dict): return
        p = place_of(st); pk = json.dumps(p, sort_keys=True)
        if p and pk not in seen_p: seen_p.add(pk); places.append(p)
        for x in _texts(st):
            if x not in seen_t: seen_t.add(x); texts.append(x)
        keys.add(_state_key(st))
    add_snap(snaps[0])
    prev_shot = None
    for i, t in enumerate(tl):
        st = snaps[i + 1]; ch = real_change(t.get("diff"))
        if ch: last = t.get("step") or 0
        add_snap(st)
        shot = t.get("screenshot")
        if shot and not str(shot).startswith("zz_end") and "_auto" not in str(shot):
            shots.append({"shot": shot, "step": t.get("step"), "state_changed": ch,
                          "screen_changed": _img_differs(out / prev_shot, out / shot) if prev_shot else None})
        if shot: prev_shot = shot
    fs = r.get("state") if isinstance(r.get("state"), dict) else (snaps[-1] if isinstance(snaps[-1], dict) else {})
    e = _engine(fs)
    final = {"place": place_of(fs), "screen": e.get("screen"), "pos": _pos(fs), "screen_text": _texts(fs)[:5],
             "running": fs.get("running") if isinstance(fs, dict) else None}
    return {"trace": name or out.name, "ok": not (r.get("ok") is False) and not (not tl and errors), "steps": steps,
            "last_change": last, "final": final, "places": places, "texts": len(texts), "states": len(keys),
            "errors": len(errors), "shots": shots, "claims": [str(c)[:200] for c in (trace.get("claims") or [])][:8]}


def describe(s):
    """a summary -> human lines (the head of `game replay`'s summary_text)"""
    if not s: return "no replay record"
    L = [f"{s['trace']}: {s['steps']} steps; last state change at step {s['last_change']}"
         + (f" (nothing changes in the last {s['steps'] - s['last_change']} steps)" if s['steps'] - s['last_change'] >= 5 else "")
         + f"; ends in {final_text(s['final'])}; {len(s['places'])} place(s): " + (" -> ".join(place_text(p) for p in s["places"][:8]) or "-")
         + f"; {s['texts']} distinct on-screen text(s), {s['states']} distinct state(s)" + (f"; {s['errors']} step error(s)" if s["errors"] else "")]
    for x in s["shots"]:
        same = []
        if x["screen_changed"] is False: same.append("screenshot same as the previous one")
        if not x["state_changed"]: same.append("state unchanged")
        L.append(f"  shot {x['shot']} (step {x['step']}): " + ("; ".join(same) + " -- does it show what you claim?" if same else "changed"))
    if s.get("claims"): L.append("  claims: " + " | ".join(s["claims"]))
    return "\n".join(L)


def compare(base, cur):
    """the same trace on an earlier hand-in (base) and now (cur) -> list of regression texts ([] = none)"""
    if not base: return []
    if not cur: return ["it was not replayed now (missing, or its replay failed to start)"]
    R = []
    if base.get("ok") and not cur.get("ok"): R.append("its replay fails now")
    cur_places = [json.dumps(p, sort_keys=True) for p in cur.get("places") or []]
    bp = (base.get("final") or {}).get("place")
    if bp and json.dumps(bp, sort_keys=True) not in cur_places:
        R.append(f"it never reaches {place_text(bp)}, where it ended on your earlier hand-in")
    if len(cur.get("places") or []) < len(base.get("places") or []):
        R.append(f"it goes through {len(cur.get('places') or [])} place(s), the earlier hand-in {len(base.get('places') or [])}")
    tail = (cur.get("steps") or 0) - (cur.get("last_change") or 0)
    if (cur.get("last_change") or 0) < (base.get("last_change") or 0) and tail >= max(5, 0.15 * (cur.get("steps") or 0)):
        R.append(f"nothing changes after step {cur.get('last_change')} of {cur.get('steps')} (earlier: changes until step "
                 f"{base.get('last_change')} of {base.get('steps')})")
    bt, ct = base.get("texts") or 0, cur.get("texts") or 0
    if bt >= 3 and ct < bt * 2 / 3:
        R.append(f"it shows {ct} distinct on-screen text(s), the earlier hand-in {bt}")
    return R


def summaries(replay_dir, traces_dir=None):
    """{trace file name: summary} of a replay dir written by record_replays (record.json + <stem>/ per trace); summaries kept
    in record.json are used, others derived from the files (and added to record.json when it is writable)"""
    rd = Path(replay_dir); rec = _rj(rd / "record.json", {}) or {}; out = {}; changed = False
    names = [t.get("trace") for t in rec.get("traces") or [] if t.get("trace")]
    if not names: names = [p.name + ".yaml" for p in sorted(rd.iterdir()) if (p / "replay.json").exists()] if rd.is_dir() else []
    for n in names:
        item = next((t for t in rec.get("traces") or [] if t.get("trace") == n), None)
        s = (item or {}).get("summary")
        if not s:
            tr = None
            if traces_dir and (Path(traces_dir) / n).exists():
                try: tr = yaml.safe_load((Path(traces_dir) / n).read_text())
                except Exception: tr = None
            s = summarize_dir(rd / Path(n).stem, tr if isinstance(tr, dict) else None, name=n)
            if s and item is not None: item["summary"] = s; changed = True
        if s: out[n] = s
    if changed:
        try: (rd / "record.json").write_text(json.dumps(rec, indent=1, default=str))
        except OSError: pass
    return out


def compare_dirs(base_dir, cur_dir, base_traces=None, cur_traces=None, cur_build=None):
    """two record_replays dirs -> {traces: [{trace, regressions, base, now}], regressed: [trace names], build_failed}"""
    B = summaries(base_dir, base_traces); C = summaries(cur_dir, cur_traces)
    cur_places = {json.dumps(p, sort_keys=True) for s in C.values() for p in s.get("places") or []}
    rep = {"traces": [], "regressed": [], "base": str(base_dir), "now": str(cur_dir)}
    build = cur_build if cur_build is not None else (_rj(Path(cur_dir) / "record.json", {}) or {}).get("build")
    if build is False and B:
        rep["build_failed"] = True; rep["regressed"] = list(B)
        rep["traces"] = [{"trace": n, "regressions": ["the mod does not build now, so no trace can be replayed"],
                          "base": _brief(B[n]), "now": None} for n in B]
        return rep
    for n, b in B.items():
        c = C.get(n)
        if c is None:                        # renamed / removed: a regression only when no trace reaches its end any more
            bp = json.dumps((b.get("final") or {}).get("place") or {}, sort_keys=True)
            regs = [] if not (b.get("final") or {}).get("place") or bp in cur_places else \
                [f"this trace is gone and no trace of yours reaches {place_text(b['final']['place'])} any more"]
        else: regs = compare(b, c)
        rep["traces"].append({"trace": n, "regressions": regs, "base": _brief(b), "now": _brief(c)})
        if regs: rep["regressed"].append(n)
    return rep


def _brief(s):
    if not s: return None
    return {"steps": s.get("steps"), "last_change": s.get("last_change"), "final": final_text(s.get("final")),
            "places": [place_text(p) for p in s.get("places") or []][:10], "texts": s.get("texts"), "ok": s.get("ok")}


def regressed_lines(rep):
    """the report -> one concrete line per regressed trace"""
    L = []
    for t in rep.get("traces") or []:
        if not t["regressions"]: continue
        now, base = t.get("now"), t.get("base") or {}
        head = f"- trace {t['trace']}" + (f" now stops at step {now['last_change']} of {now['steps']} in {now['final']}" if now else "")
        head += f"; on your earlier hand-in it reached {base.get('final')} (step {base.get('last_change')} of {base.get('steps')})"
        L.append(head + ": " + "; ".join(t["regressions"]) + ".")
    return L


CHECK_MSG = """Before your round ends, the benchmark replayed your current traces (deliverable/traces/*.yaml) on a fresh game, the
way the evaluation will replay them, and compared them with the replay of your hand-in from before this round. These traces
now reach LESS of your content than before:
{lines}
The evaluation judges what the replay of each trace shows: content it does not reach counts as missing. Fix the mod (a
rebuilt map / level / trigger the old route no longer reaches) or re-record the trace so that it reaches the content again,
then verify with `game replay deliverable/traces/<trace>.yaml` (its summary shows the place it ends in and the step of its
last state change). The game was stopped for this check: `game start` again before you send input.
You have about {minutes:.0f} minutes; deliverable/DONE is still in place, leave it there when you are done."""


def message(rep, minutes):
    L = regressed_lines(rep)
    return CHECK_MSG.format(lines="\n".join(L), minutes=minutes) if L else None

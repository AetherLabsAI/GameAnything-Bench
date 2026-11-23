"""Judge health: every LLM judge call of the evaluation (replay verifier, play verifier, arbiter, visual
judge) is checked explicitly, retried once when it failed, and recorded. A judge that still fails is NOT turned into scores:
the trial becomes infra_error `judge_failed` (gab/run/infra.py), unscored, recorded in the infra ledger, re-evaluated by the next `gab run`.

Why: gpt56sol-codex-s0 T-HY-vid_3c178cfd1133.t1 fine, evaluated twice: CR 0.75 / FP 0.83, then 0.08 / 0.0. In the second run
the play verifier's `play phase2` was denied by the permission rules after 9 actions, it stopped without verdict.json, every
item became "no verdict", arbitration kept the replay verifier alone and CR6 (no verdict from either) scored 0.

  failure(rec, data, ids, ...) -> None | reason: timeout / is_error / permission denial / no verdict.json / no parseable JSON
                                  / items missing (an item is present with a numeric score or could_not_test: true)
  run(cost, stage, call, judge) -> (items by id, reason | None): the call, its check, one retry, the record in cost["judges"]
                                  (with `followup`: a verdict complete but for some items first gets one cheap follow-up
                                  call that asks only for those items -- no new play session -- before the full retry)
  summary(cost)                 -> result.json `judges`: calls, retries, failed_calls, failed (stages), per stage attempts
  incidents(cost)               -> infra incidents of the stages that failed for good (judge_failed + any model-API cause)"""
import re

from ..run import infra
from ..util import add_cost, log

DENIAL = re.compile(r"\bpermission\b[^\n]{0,120}\bden(?:y|ied|ial)|\bden(?:y|ied|ial)\b[^\n]{0,120}\bpermission|"
                    r"(?:has|have|had|was|were|got|been) (?:been )?denied|don'?t[- ]ask", re.I)


def items_of(data, ids, need_score=False):
    """parsed judge JSON -> ({id: item} of the valid items, ids missing). Valid: a numeric score, or could_not_test true (not
    with need_score: the arbiter and the visual judge always score)"""
    got, L = {}, data.get("items") if isinstance(data, dict) else None
    for i in L if isinstance(L, list) else []:
        if not isinstance(i, dict) or "id" not in i: continue
        ok = bool(i.get("could_not_test")) and not need_score
        try: float(i.get("score")); ok = True
        except (TypeError, ValueError): pass
        if ok: got[str(i["id"])] = i
    return got, [x for x in ids if x not in got]


def denied(rec, txt):
    """the permission denials of a call: the CLI's structured list, else a denial the answer reports. -> short text or ''"""
    d = rec.get("permission_denials") or []
    if d: return "; ".join(f"{x.get('tool')}: {x.get('input')}"[:160] for x in d[:3]) + (f" (+{len(d) - 3})" if len(d) > 3 else "")
    m = DENIAL.search(txt or "")
    return f"answer: ...{(txt or '')[max(0, m.start() - 60): m.end() + 60]}..." if m else ""


def failure(rec, txt, data, ids, parse_error="no parseable JSON", need_score=False):
    """-> (valid items by id, None if the call is usable else the reason). data = the parsed verdict or None. A complete verdict
    stands even after a CLI error; but a denial with any item could_not_test fails it (the verifier's "own limits" may be
    our permission rules)"""
    got, missing = items_of(data, ids, need_score)
    den = denied(rec, txt)
    if data is not None and not missing:
        if den and any(got[x].get("could_not_test") for x in ids): return got, f"permission denial with items could_not_test: {den}"
        return got, None
    if rec.get("timeout"): r = "timeout"
    elif rec.get("is_error"): r = f"is_error ({rec.get('subtype') or '-'}): {str(rec.get('answer') or rec.get('stderr') or '')[:200]}"
    elif den: r = f"permission denial: {den}"
    elif data is None: r = parse_error
    else: r = f"items missing: {', '.join(missing)}"
    return got, " ".join(r.split())[:400]


def health(cost):
    h = cost.setdefault("judges", {"calls": 0, "retries": 0, "failed_calls": 0, "failed": [], "stages": {}})
    h.setdefault("followups", 0); return h


MISSING = "items missing: "


def missing_of(got, reason):
    """the item ids a verdict lacks when that is its only fault (some valid items, the rest missing) -> [ids] or []"""
    return [x for x in reason[len(MISSING):].split(", ") if x] if got and reason and reason.startswith(MISSING) else []


def run(cost, stage, call, judge, retries=1, before_retry=None, followup=None):
    """call(n) -> (answer text, record); judge(text, record) -> (items by id, reason | None); before_retry(n) resets what the
    retry needs (the play verifier: a fresh game instance). followup(missing ids, n) -> (answer text, record, items by id, reason):
    one cheap call for only the items a verdict lacked, tried before the full retry. -> (items by id of the last attempt, None |
    reason it failed for good)"""
    h = health(cost); st = h["stages"].setdefault(stage, {"calls": 0, "ok": None, "attempts": []})
    got, reason, rec, h_fu = {}, None, {}, False                   # one follow-up per stage, before the full retry
    def note(n, rec, reason):
        h["calls"] += 1; st["calls"] += 1
        st["attempts"].append({"attempt": n, "ok": reason is None, "reason": reason, "wall_s": rec.get("wall_s"), "usd": rec.get("usd"),
                               "is_error": rec.get("is_error"), "permission_denials": len(rec.get("permission_denials") or [])})
        if reason is not None: h["failed_calls"] += 1
    for n in range(1, retries + 2):
        if n > 1:
            h["retries"] += 1
            log(f"{stage}: judge failed ({reason}); retry {n - 1} of {retries}")
            if before_retry: before_retry(n)
        txt, rec = call(n); add_cost(cost, rec)
        got, reason = judge(txt, rec)
        note(n, rec, reason)
        if reason is None: st["ok"] = True; return got, None
        miss = missing_of(got, reason) if followup and not h_fu else []
        if miss:                                             # complete but for some items: ask only for those first
            h["followups"] += 1; h_fu = True; log(f"{stage}: {reason}; follow-up call for those items only")
            try: ftxt, frec, fgot, freason = followup(miss, n)
            except Exception as x: frec, fgot, freason = {}, {}, f"follow-up failed: {type(x).__name__}: {x}"
            add_cost(cost, frec); note(f"{n}.followup", frec, freason)
            if freason is None:
                st["ok"] = True; return {**got, **{k: fgot[k] for k in miss if k in fgot}}, None
    st["ok"] = False
    cause = infra.classify(f"{rec.get('answer') or ''}\n{rec.get('stderr') or ''}", components=("model-api",)) if rec.get("is_error") else []
    h["failed"].append({"stage": stage, "reason": reason, "causes": [c["code"] for c in cause],
                        "_incidents": [{**c, "source": f"eval {stage}"} for c in cause]})
    log(f"{stage}: JUDGE FAILED after {retries} retry: {reason}")
    return got, reason


def summary(cost):
    """result.json `judges` (without the incident bodies)"""
    h = (cost or {}).get("judges") or health({})
    return {**h, "failed": [{k: v for k, v in f.items() if k != "_incidents"} for f in h["failed"]]}


def incidents(cost):
    """-> infra incidents: one judge_failed per stage that failed after its retry, plus model-API causes found in its error"""
    out = []
    for f in ((cost or {}).get("judges") or health({}))["failed"]:
        out.append(infra.make("judge_failed", f"{f['stage']}: {f['reason']}", source=f"eval {f['stage']}", stage=f["stage"]))
        out += f.get("_incidents") or []
    return out


def placeholder(stage, reason):
    """the entry of an item whose judge failed for good: no score, flagged (arbitration does not use it)"""
    return {"score": None, "could_not_test": True, "reason": f"judge failed: {stage}: {reason}", "judge_failed": f"{stage}: {reason}"}

"""The run ends when the agent marks its hand-in as done (deliverable/DONE) or the budget is used up.
Wraps every harness the same way. A harness whose process ends while the agent has not marked DONE (e.g. a provider returned
a tool call as plain text, so the harness took it for the final answer) is continued in the SAME session with a neutral
message; after `until_done_max_idle` continuations in a row without a single tool call the run stops and is recorded as an
early stop. A harness that cannot resume gets a fresh session with the continuation message appended to the brief.
A segment that ends in a crash caused by a TRANSIENT model-API failure (gab/run/infra.py: 5xx, rate limit, media timeout, ...) is
resumed in the same session after a backoff (fix and continue, never start over), at most `infra_resume_max`
times per run; the backoff counts against the budget like any other wait. Recorded per segment (infra_resume).
Every segment's transcript is kept (transcript.<tag>partN) and the parts are joined into the harness's transcript path."""
import shutil, time
from pathlib import Path

from .. import config
from ..run import infra
from .base import AgentRun, Budget

EARLY = ("You marked the hand-in as done after {used:.0f} minutes, but this round is meant to use its time: {left:.0f} minutes "
         "are left and DONE is accepted only after minute {min:.0f}. The marker was removed. Keep improving: work through the "
         "review's remaining issues (review/feedback.md), compare your screenshots with the reference / target pictures again "
         "and fix what still differs; keep the hand-in consistent, then create deliverable/DONE again.")
CONTINUE = ("You have not marked the hand-in as done (deliverable/DONE does not exist) and time is left. Continue the task; "
            "create deliverable/DONE when the hand-in is complete.")
INFRA_CONTINUE = ("The previous model request failed because of a temporary provider error (not caused by you). Nothing in your "
                  "workspace or the game was changed. Continue the task where you left off; create deliverable/DONE when the "
                  "hand-in is complete.")


def transient_api_failure(r):
    """the segment's crash came from a transient model-API failure? -> the incident or None"""
    ex = r.extra or {}
    txt = "\n".join(str(ex.get(k) or "") for k in ("error", "errors", "stderr_tail"))
    if r.transcript and Path(r.transcript).exists(): txt += "\n" + Path(r.transcript).read_text(errors="replace")[-30000:]
    if any(g["code"] in ("budget_exceeded", "request_too_large") for g in infra.classify(txt, components=("cost-guard",))):
        return None                                                     # gab's own refusal: never resumed as a provider error
    m = infra.classify(txt, components=("model-api",))
    if not m or any(i["kind"] == infra.S for i in m): return None      # systematic (credits, auth, schema): stop, fix, resume later
    return m[0]


class UntilDone:
    def __init__(self, inner, marker="deliverable/DONE", message=CONTINUE, tag="", min_minutes=None, elapsed0=0.0, max_continuations=None):
        self.max_continuations = max_continuations                     # None = no limit (the critic: its own fresh finish session after)
        self.min_minutes = min_minutes                                  # refine rounds: DONE before this is not accepted
        self.elapsed0 = float(elapsed0 or 0)                            # minutes of the round already used before this run (a
        # round continued after the operator stopped gab, gab/run/interrupt.py): min_minutes counts the round's total time
        self.inner = inner; self.model = inner.model; self.opts = getattr(inner, "opts", None)
        self.marker, self.message, self.tag, self.sid = marker, message, tag, None
        if self.opts is None: inner.opts = self.opts = {}             # wrappers without opts (in-container): fresh-session fallback

    def version(self): return self.inner.version()

    def run(self, workdir, instruction, budget, tools, resume=None, fresh=None):
        """resume: a session id to continue (first segment too); fresh: the prompt of a fresh session (default: instruction)"""
        t0 = time.time(); done = Path(workdir) / self.marker; fresh = fresh or instruction
        tot = AgentRun(model=self.inner.model, exit_reason="done"); subs, parts = [], []; idle = 0; nres = 0; early = 0
        can = getattr(self.inner, "can_resume", False); sid = resume if can else None
        if sid and not getattr(self.inner, "has_session", lambda _s, _w: True)(sid, workdir): sid = None   # e.g. a codex run from
        # before sessions were kept (--ephemeral): nothing to resume, a fresh session with the full prompt instead
        if sid: self.inner.opts["resume"] = sid; prompt = instruction
        else: self.inner.opts.pop("resume", None); prompt = fresh
        from ..run import interrupt
        while True:
            left = budget.minutes - (time.time() - t0) / 60
            if subs and interrupt.requested(): tot.exit_reason = "interrupted"; break
            r = self.inner.run(workdir, prompt, Budget(minutes=left, max_turns=budget.max_turns, stop_file=budget.stop_file), tools)
            k = len(subs)
            if r.transcript and Path(r.transcript).exists():
                p = Path(r.transcript); q = p.with_name(f"{p.stem}.{self.tag}part{k}{p.suffix}"); shutil.copy(p, q); parts.append(q)
                sid = getattr(self.inner, "session_of", lambda _p: None)(p) or sid
            subs.append({"segment": k, "resumed": bool(self.inner.opts.get("resume")), **r.to_dict()})
            for f in ("tokens_in", "tokens_out", "tokens_cached"): setattr(tot, f, getattr(tot, f) + (getattr(r, f) or 0))
            tot.usd = None if r.usd is None and tot.usd is None else (tot.usd or 0) + (r.usd or 0)
            tot.harness_version = r.harness_version; tot.rc = r.rc; tot.transcript = r.transcript
            for key, v in (r.extra or {}).items():
                if key in ("tool_calls", "turns", "steps", "num_turns", "usd_estimate", "cost_requests", "cost_priced") and isinstance(v, (int, float)):
                    tot.extra[key] = tot.extra.get(key, 0) + v
                elif key == "spend": tot.extra[key] = v                 # the key proxy's running total: the last one
                else: tot.extra.setdefault(key, v)
            idle = 0 if (r.extra or {}).get("tool_calls") else idle + 1
            left = budget.minutes - (time.time() - t0) / 60
            if (r.extra or {}).get("budget_exceeded"):                     # the key proxy's USD cap: no continuation, no resume
                tot.extra["budget_exceeded"] = r.extra["budget_exceeded"]
                if r.exit_reason != "done": tot.exit_reason = r.exit_reason
                break
            if r.exit_reason == "crash" and done.exists():                 # the hand-in was complete before the harness died
                tot.extra["crash_after_done"] = True; break
            if r.exit_reason == "crash" and left >= 3 and nres < int(config.get("infra_resume_max") or 4) and (inc := transient_api_failure(r)):
                wait = min(30 * 2 ** nres, 300, max(0, (left - 2) * 60)); nres += 1
                subs[-1]["infra_resume"] = {"code": inc["code"], "message": inc["message"][:300], "wait_s": wait}
                if interrupt.wait(wait): tot.exit_reason = "interrupted"; break
                if can and sid: self.inner.opts["resume"] = sid; prompt = INFRA_CONTINUE
                else: self.inner.opts.pop("resume", None); prompt = fresh + "\n\n" + INFRA_CONTINUE
                continue
            if r.exit_reason != "done": tot.exit_reason = r.exit_reason; break          # timeout / other crash: no continuation
            used = (time.time() - t0) / 60 + self.elapsed0                  # of the whole round
            if done.exists() and self.min_minutes and used < self.min_minutes and left >= 3 and early < 6:
                # the agent marked DONE early (e.g. an agent stopped after 4 min with the review's issues
                # listed as not done): not accepted before min_minutes -- the marker is removed and the agent continues
                done.unlink(missing_ok=True); early += 1; tot.extra["done_too_early"] = early
                msg = EARLY.format(used=used, left=left, min=self.min_minutes)
                if can and sid: self.inner.opts["resume"] = sid; prompt = msg
                else: self.inner.opts.pop("resume", None); prompt = fresh + "\n\n" + msg
                continue
            if done.exists(): break
            if left < 2: tot.exit_reason = "timeout"; break
            if idle >= int(config.get("until_done_max_idle")): tot.extra["early_stop"] = True; break
            if self.max_continuations is not None and len(subs) > self.max_continuations:
                tot.extra["continuations_exhausted"] = True; break
            if can and sid: self.inner.opts["resume"] = sid; prompt = self.message
            else: self.inner.opts.pop("resume", None); prompt = fresh + "\n\n" + self.message
        self.inner.opts.pop("resume", None); self.sid = sid
        if parts and tot.transcript:                                       # one joined transcript for the readers downstream
            with open(tot.transcript, "w") as out:
                for q in parts: out.write(q.read_text(errors="replace"))
        tot.wall_s = round(time.time() - t0, 1)
        tot.extra.update(done_marked=done.exists(), continuations=len(subs) - 1, segments=subs if len(subs) > 1 else [],
                         infra_resumes=nres, session_id=sid)
        return tot

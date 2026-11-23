"""Stopping `gab run` / `gab refine` without losing the agents' work: a stopped run continues, it is never voided.

SIGTERM / SIGINT (the operator restarts a run, e.g. with other --workers) sets STOP instead of killing the process at once:
every running harness is stopped by its watchdog (gab/agent/base.watchdog_kill -> exit reason "interrupted"; an in-container
harness is then killed by its wrapper's cleanup), each trial records where its agent stood (agent/interrupted.json: session id,
minutes used of the budget, run / refine round) and no new trial or retry starts. The same command run again continues each
interrupted agent in its SAME session with only the time that was left (gab/run/trial.py run_agent, gab/refine/refine.py refine_agent).
A second signal exits at once (exit 143); a hard kill is inferred on the next start from the trial's own markers
(trial.json `started` without an end; agent/refine_running.json)."""
import os, signal, threading, time

STOP = threading.Event()
_info = {}


class Interrupted(RuntimeError):
    """raised where a step must not start (or go on) because the operator stopped the run"""


def requested(): return STOP.is_set()


def wait(seconds):
    """time.sleep that ends early when the run is stopped -> True when stopped"""
    return STOP.wait(max(0.0, seconds))


def check(what=""):
    if STOP.is_set(): raise Interrupted(f"stopped by the operator{': ' + what if what else ''}")


def _handler(signum, frame):
    if STOP.is_set():                                   # second signal: no more waiting for the agents to stop
        from ..util import log
        log(f"signal {signum} again: exiting now (interrupted agents are inferred on the next start)")
        os._exit(143)
    _info.update(signal=signum, at=time.time()); STOP.set()
    from ..util import log
    log(f"signal {signum}: stopping -- the running agents are stopped and recorded as interrupted (the same command continues "
        "them in their sessions); no new trial starts. Send it again to exit at once.")


def install():
    """in the main thread of `gab run` / `gab refine`"""
    if threading.current_thread() is not threading.main_thread(): return False
    for s in (signal.SIGTERM, signal.SIGINT): signal.signal(s, _handler)
    return True

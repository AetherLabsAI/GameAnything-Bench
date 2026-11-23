"""AgentAdapter interface (docs/adding_an_agent.md). One subclass per harness in gab/agent/<harness>/ (module `adapter.py`, class
`Adapter`); `gab.agent.base.get(name)` loads it. An adapter starts ONE coding agent on a prepared workdir and returns an
AgentRun; it never sees hidden material (the workdir holds only the public task, the clips, the dev kit and the tools).

Contract for every adapter:
  - run(workdir, instruction, budget, tools) -> AgentRun. `instruction` is the full prompt (built by trial.py: task
    instruction of the level + deliverable spec), `budget` a Budget (wall minutes; tokens are not limited, only recorded),
    `tools` a dict name -> absolute path of an executable the agent must have on PATH (ffmpeg, ffprobe, transcribe, game).
    The tool directory is `Path(tools[...]).parent`; put it first on the agent's PATH.
  - stream the transcript to workdir/../transcript.jsonl (or any path you record in AgentRun.transcript) while running;
  - stop at budget.minutes (kill the process tree) -> exit_reason "timeout"; normal end -> "done"; anything else -> "crash";
  - credentials only through the agent process's own environment (never argv, files, logs or containers);
  - fill tokens / cost from the harness's own usage report; when it reports no USD, leave usd None and set tokens: the
    trial computes usd from gab/agent/prices.yaml (price_usd()).
"""
import importlib, json, time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml


@dataclass
class Budget:
    minutes: float = 120.0          # wall time of the whole agent run
    max_turns: int | None = None    # harness turn cap if the harness needs one; None = unlimited
    stop_file: str | None = None    # its appearance ends the run too (watchdog_kill)


@dataclass
class AgentRun:
    transcript: str | None = None   # path of the raw transcript (jsonl or text)
    exit_reason: str = "crash"      # done | timeout | crash
    wall_s: float = 0.0
    tokens_in: int = 0              # uncached input tokens
    tokens_out: int = 0
    tokens_cached: int = 0          # cache reads (+ cache writes, if the harness reports them separately: add them here)
    usd: float | None = None        # provider-reported cost; None -> price table
    model: str | None = None
    harness_version: str | None = None
    rc: int | None = None
    sub_runs: list = field(default_factory=list)   # optional: one AgentRun dict per sub-run of a wrapper
    extra: dict = field(default_factory=dict)      # harness-specific numbers (turns, tool calls, ...)

    def to_dict(self): return asdict(self)


class AgentAdapter:
    name = "base"

    def __init__(self, model=None, **opts):
        self.model, self.opts = model, opts

    can_resume = False

    def session_of(self, transcript): return None

    def run(self, workdir: Path, instruction: str, budget: Budget, tools: dict) -> AgentRun:
        raise NotImplementedError

    def version(self) -> str | None:
        return None


def get(name, model=None, **opts) -> AgentAdapter:
    """gab.agent.<name>.adapter.Adapter(model, **opts)"""
    return importlib.import_module(f"gab.agent.{name}.adapter").Adapter(model=model, **opts)


PRICES = Path(__file__).resolve().parent / "prices.yaml"


def price_usd(model, tokens_in, tokens_out, tokens_cached=0):
    """USD from the price table (per million tokens: input, output, cached_input) when a harness reports no cost; None if
    the model is not in the table."""
    if not PRICES.exists(): return None
    p = (yaml.safe_load(PRICES.read_text()) or {}).get("models", {}).get(model)
    if not p: return None
    return round((tokens_in * p["input"] + tokens_out * p["output"] + tokens_cached * p.get("cached_input", p["input"])) / 1e6, 4)


STOP_GRACE_S = 30                   # a harness still running this long after the stop file appeared is stopped


def watchdog_kill(proc, deadline, stop=None):
    """poll a Popen until it ends or the deadline (epoch s) passes; on timeout kill its process group. -> exit reason.
    stop: a file (Budget.stop_file) whose appearance also ends the run, STOP_GRACE_S later -> "done" """
    from ..run import interrupt
    seen = None
    while proc.poll() is None:
        if stop and seen is None and Path(stop).exists(): seen = time.time()
        late = seen is not None and time.time() - seen > STOP_GRACE_S
        if interrupt.requested():                        # the operator stopped gab (gab/run/interrupt.py): stop the harness now
            import os, signal
            try: os.killpg(proc.pid, signal.SIGTERM)
            except Exception: proc.terminate()
            try: proc.wait(20)
            except Exception:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except Exception: proc.kill()
            return "interrupted"
        if time.time() > deadline or late:
            import os, signal
            try: os.killpg(proc.pid, signal.SIGTERM)
            except Exception: proc.terminate()
            try: proc.wait(20)
            except Exception:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except Exception: proc.kill()
            return "done" if late else "timeout"
        time.sleep(2)
    return "done" if proc.returncode == 0 else "crash"

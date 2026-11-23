"""`gab run --agent-cmd "..."`: any program as the coding agent (docs/adding_an_agent.md). The shell template gets the placeholders
{task} (staged public workspace), {brief} (brief.md, self-contained), {refs} (space-separated paths of every reference file),
{clips} (the video clips only), {out} (deliverable dir), {model}, {game}, {level}. The dev tools are on its PATH; it is killed
at the budget. Tokens / cost: if the program writes {task}/.gab/usage.json {tokens_in, tokens_out, tokens_cached, usd}, it is used."""
import json, os, shlex, subprocess, time
from pathlib import Path

import yaml

from ..base import AgentAdapter, AgentRun, watchdog_kill


def placeholders(workdir, model=None):
    """the {task} {brief} {refs} {clips} {out} {model} {game} {level} values of a staged workspace (also used by agent/incontainer.py)"""
    w = Path(workdir); t = yaml.safe_load((w / "task" / "task.yaml").read_text())
    lv = next(p.stem.split("_")[1] for p in (w / "task").glob("instruction_*.md"))
    return {"task": str(w), "brief": str(w / "brief.md"), "clips": " ".join(shlex.quote(str(p)) for p in sorted((w / "reference").glob("*.mp4"))),
            "refs": " ".join(shlex.quote(str(p)) for p in sorted((w / "reference").iterdir()) if p.is_file()),
            "out": str(w / "deliverable"), "model": model or "", "game": t["target_game"], "level": lv}


class Adapter(AgentAdapter):
    name = "cmd"

    def run(self, workdir, instruction, budget, tools):
        w = Path(workdir)
        cmd = self.opts["agent_cmd"].format(**placeholders(w, self.model))
        tr = w.parent / "agent" / "transcript.txt"; tr.parent.mkdir(parents=True, exist_ok=True)
        tmp = w / ".gab" / "tmp"; tmp.mkdir(parents=True, exist_ok=True)                    # private TMPDIR per trial
        dirs = list(dict.fromkeys(str(Path(t).parent) for t in (tools or {}).values()))
        env = dict(os.environ, PATH=":".join(dirs + [os.environ["PATH"]]), TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp))
        t0 = time.time()
        with open(tr, "w") as f:
            p = subprocess.Popen(["bash", "-c", cmd], cwd=w, stdout=f, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            reason = watchdog_kill(p, t0 + budget.minutes * 60, budget.stop_file)
        u = json.loads((w / ".gab" / "usage.json").read_text()) if (w / ".gab" / "usage.json").exists() else {}
        return AgentRun(transcript=str(tr), exit_reason=reason, wall_s=round(time.time() - t0, 1), rc=p.returncode, model=self.model,
                        tokens_in=int(u.get("tokens_in") or 0), tokens_out=int(u.get("tokens_out") or 0),
                        tokens_cached=int(u.get("tokens_cached") or 0), usd=u.get("usd"), extra={"cmd": cmd})

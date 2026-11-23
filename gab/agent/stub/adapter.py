"""STUB agent (pipeline self-check, no model): writes a trivial but well-formed deliverable and exercises the dev tools
(`game build`, `game start`, `game shot`) the way a real agent would. No model is called."""
import json, shutil, subprocess, time
from pathlib import Path

from ..base import AgentAdapter, AgentRun

class Adapter(AgentAdapter):
    name = "stub"

    def run(self, workdir, instruction, budget, tools):
        t0 = time.time(); w = Path(workdir); dv = w / "deliverable"; log = []
        def game(*a):
            r = subprocess.run([tools["game"], *a], capture_output=True, text=True, timeout=900)
            log.append({"cmd": ["game", *a], "rc": r.returncode, "out": r.stdout[-1500:], "err": r.stderr[-800:]}); return r
        tpl = w / "devkit" / "template"
        if tpl.exists(): shutil.copytree(tpl / "mod", dv / "mod", dirs_exist_ok=True)
        for f in ("ACCESS.md",):
            if (tpl / f).exists(): shutil.copy(tpl / f, dv / f)
        for k in (1, 2):
            (dv / "traces" / f"t{k}.yaml").write_text(f"name: stub trace {k}\nclaims: [\"the game starts with the mod\"]\nsteps:\n"
                                                       f"  - {{do: wait, seconds: 2}}\n  - {{do: shot, name: after_wait}}\n")
        game("build")
        game("start")
        for k in (1, 2, 3): game("shot", str(dv / "shots" / f"{k:02d}.png")); time.sleep(1)
        game("check"); game("stop"); (dv / "DONE").write_text("")        # hand-in complete (until_done)
        tr = w.parent / "agent" / "transcript.jsonl"; tr.parent.mkdir(parents=True, exist_ok=True)
        tr.write_text("".join(json.dumps(x) + "\n" for x in log))
        return AgentRun(transcript=str(tr), exit_reason="done", wall_s=round(time.time() - t0, 1), model="stub", usd=0.0,
                        extra={"game_calls": len(log), "failed_calls": sum(1 for x in log if x["rc"])})

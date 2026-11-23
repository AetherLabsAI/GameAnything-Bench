"""Pipeline pieces with fakes (no game, no model): the --agent-cmd adapter (gate probes: test_gate.py)."""
import json
from pathlib import Path

import yaml

from gab.agent.base import AgentRun, Budget, get


def test_agent_cmd(tmp_path):
    w = tmp_path / "work"; (w / "task").mkdir(parents=True); (w / "deliverable").mkdir(); (w / "reference").mkdir()
    yaml.safe_dump({"target_game": "flare"}, open(w / "task" / "task.yaml", "w")); (w / "task" / "instruction_fine.md").write_text("x")
    (w / "reference" / "clip_01.mp4").write_bytes(b"")
    a = get("cmd", model="m", agent_cmd="echo {game} {level} {model} > {out}/u.txt; echo $TMPDIR > {out}/tmp.txt")
    r = a.run(w, "brief", Budget(minutes=1), {})
    assert r.exit_reason == "done" and (w / "deliverable" / "u.txt").read_text().split() == ["flare", "fine", "m"]
    assert (w / "deliverable" / "tmp.txt").read_text().strip() == str(w / ".gab" / "tmp")
    r = get("cmd", agent_cmd="sleep 30").run(w, "b", Budget(minutes=0.03), {})
    assert r.exit_reason == "timeout"


def test_opencode_reasoning_effort_reaches_the_request_options(tmp_path):
    """--reasoning-effort for OpenCode goes into the model's provider options (`--variant` alone never reached a custom
    openai-compatible provider: checked 2026-11-05)"""
    from gab.agent.opencode.adapter import Adapter
    for eff, want in ((None, None), ("low", "low")):
        ad = Adapter(model="kimi-k3", base_url="http://127.0.0.1:9/v1", api_key_env="K", **({"reasoning_effort": eff} if eff else {}))
        home = tmp_path / str(eff); home.mkdir()
        cmd, extra = ad.command(tmp_path, "x", home)
        m = json.loads(Path(extra["OPENCODE_CONFIG"]).read_text())["provider"]["gab"]["models"]["kimi-k3"]
        assert (m.get("options") or {}).get("reasoningEffort") == want and "--variant" not in cmd


def test_freeze_deliverable_retries_and_never_leaves_an_empty_copy(tmp_path, monkeypatch):
    """2026-11-06: a failed copy left an empty deliverable/ that the next launch took for a finished trial"""
    import shutil as sh
    from gab.run import trial
    src = tmp_path / "w"; (src / "shots").mkdir(parents=True); (src / "DONE").write_text("")
    real, n = sh.copytree, []
    def flaky(*a, **k):
        n.append(1)
        if len(n) == 1: Path(a[1]).mkdir(); raise sh.Error([("x", "y", "Permission denied")])
        return real(*a, **k)
    monkeypatch.setattr(trial.shutil, "copytree", flaky); monkeypatch.setattr(trial.time, "sleep", lambda s: None)
    trial.freeze_deliverable(src, tmp_path / "dv")
    assert (tmp_path / "dv" / "DONE").exists() and (tmp_path / "dv" / "shots").is_dir() and len(n) >= 2   # (copytree recurses)
    monkeypatch.setattr(trial.shutil, "copytree", lambda *a, **k: (Path(a[1]).mkdir(exist_ok=True), (_ for _ in ()).throw(OSError(13, "denied"))))
    import pytest
    with pytest.raises(OSError): trial.freeze_deliverable(src, tmp_path / "dv2")
    assert not (tmp_path / "dv2").exists()                                       # no empty copy left behind


def test_no_eval_relaunch_leaves_a_finished_trial_alone(tmp_path, monkeypatch):
    """2026-11-06: each --no-eval relaunch rewrote trial.json of finished trials (reasoning_effort of the relaunch)"""
    from gab import config
    from gab.run import trial
    import types
    monkeypatch.setattr(config, "path", lambda k: tmp_path / "runs")
    t = types.SimpleNamespace(id="T-x", game="flare")
    d = tmp_path / "runs" / "r" / t.id / "fine"; (d / "deliverable").mkdir(parents=True); (d / "agent").mkdir()
    (d / "agent" / "agent.json").write_text(json.dumps({"exit_reason": "done", "infra": {"status": None}}))
    (d / "trial.json").write_text(json.dumps({"reasoning_effort": None, "attempts": [{"started": "a"}]}))
    r = trial.run_trial("r", t, "fine", "opencode", "m", opts={"reasoning_effort": "low"}, skip_eval=True)
    assert r["status"] == "agent_done"
    assert json.loads((d / "trial.json").read_text()) == {"reasoning_effort": None, "attempts": [{"started": "a"}]}

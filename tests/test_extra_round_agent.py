"""refine rounds always run the trial's OWN harness and model (gab/cli.py _extra_round_agent): left out, --agent / --model
are taken from the trial; given and different, the trial is refused (exit 2); given and the same, they are accepted. Each
round records the harness it ran with."""
import argparse
from pathlib import Path

import pytest

from gab import cli
from gab.run import trial
from gab.refine import refine
from gab.agent.base import AgentRun


class Done:
    game = "flare"; id = "T-done"


def _ns(cmd, **over):
    base = dict(task=None, game=None, category=None, level="fine", modality=None, name="myrun", agent=None, agent_cmd=None,
                model=None, base_url=None, api_key_env=None, reasoning_effort=None, auth=None, agent_in_container=False,
                minutes=5.0, note=None, force=False, workers=1, plan=False, yes=True)
    if cmd == "refine": base.update(rounds=1, critic_model=None, review_only=False)
    base.update(over)
    return argparse.Namespace(**base)


def _setup(tmp_path, monkeypatch, name="myrun", meta=({"agent": "codex", "model": "gpt-5.6-sol"}), ag_model="gpt-5.6-sol"):
    from gab.host import base as hbase_mod
    from gab.game import base as gbase_mod
    monkeypatch.setenv("GAB_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_trial_jobs", lambda a: [(Done(), "fine")])
    d = trial.trial_dir(name, "T-done", "fine")
    (d / "work" / "deliverable").mkdir(parents=True); (d / "agent").mkdir()
    trial.wjson(d / "agent" / "agent.json", {"exit_reason": "done", "model": ag_model, "harness_version": "codex-cli 0.160.0"})
    if meta is not None: trial.wjson(d / "trial.json", meta)

    class FakeSeat:
        def release(self): pass
    class FakeBackend:
        def acquire(self, req, project): return FakeSeat()
    monkeypatch.setattr(hbase_mod, "get", lambda name=None: FakeBackend())
    monkeypatch.setattr(gbase_mod, "get", lambda game, seat: object())
    calls = []
    monkeypatch.setattr(refine, "make_targets", lambda t, **k: None)
    monkeypatch.setattr(refine, "refine_trial", lambda d, t, lv, agent, model, *a, **k: calls.append((agent, model)) or (AgentRun(exit_reason="done"), None, None))
    from gab.run import interrupt
    monkeypatch.setattr(interrupt, "install", lambda: None)
    return d, calls


def _cmd(name): return cli.cmd_refine


@pytest.mark.parametrize("cmd", ["refine"])
def test_default_takes_the_trials_own_harness_and_model(tmp_path, monkeypatch, cmd):
    _, calls = _setup(tmp_path, monkeypatch)
    _cmd(cmd)(_ns(cmd))
    assert calls == [("codex", "gpt-5.6-sol")]


@pytest.mark.parametrize("cmd", ["refine"])
@pytest.mark.parametrize("over", [{"agent": "opencode"}, {"agent": "claude"}, {"agent_cmd": "echo x"}, {"model": "deepseek-v4.1-flash"},
                                  {"agent": "opencode", "model": "gpt-5.6-sol"}])
def test_another_harness_or_model_is_refused(tmp_path, monkeypatch, capsys, cmd, over):
    _, calls = _setup(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as e:
        _cmd(cmd)(_ns(cmd, **over))
    assert e.value.code == 2 and calls == []                         # nothing ran: no silent override
    out = capsys.readouterr()
    assert '"status": "refused"' in out.out and "trial's own" in out.err


@pytest.mark.parametrize("cmd", ["refine"])
@pytest.mark.parametrize("over", [{"agent": "codex"}, {"model": "gpt-5.6-sol"}, {"agent": "codex", "model": "gpt-5.6-sol"}])
def test_matching_explicit_args_are_accepted(tmp_path, monkeypatch, cmd, over):
    _, calls = _setup(tmp_path, monkeypatch)
    _cmd(cmd)(_ns(cmd, **over))
    assert calls == [("codex", "gpt-5.6-sol")]


def test_short_agent_alias_matches_the_registry_name(tmp_path, monkeypatch):
    _, calls = _setup(tmp_path, monkeypatch, meta={"agent": "claude_code", "model": "claude-sonnet-5-5"}, ag_model="claude-sonnet-5-5")
    cli.cmd_refine(_ns("refine", agent="claude"))                     # `--agent claude` is claude_code (cli.AGENTS)
    assert calls == [("claude_code", "claude-sonnet-5-5")]


def test_harness_default_model_in_trial_json(tmp_path, monkeypatch):
    """round 1 ran without --model (trial.json model None): the extra round does too; --model naming the model round 1
    reported is accepted, another is refused"""
    _, calls = _setup(tmp_path, monkeypatch, meta={"agent": "codex", "model": None}, ag_model="gpt-5.6-sol")
    cli.cmd_refine(_ns("refine"))
    cli.cmd_refine(_ns("refine", model="gpt-5.6-sol", force=True))
    assert calls == [("codex", None), ("codex", "gpt-5.6-sol")]
    with pytest.raises(SystemExit): cli.cmd_refine(_ns("refine", model="gpt-6-astra", force=True))


def test_run_name_suffix_when_trial_json_has_no_agent(tmp_path, monkeypatch):
    _, calls = _setup(tmp_path, monkeypatch, name="gpt56sol-codex-s0", meta=None)
    cli.cmd_refine(_ns("refine", name="gpt56sol-codex-s0"))
    assert calls == [("codex", "gpt-5.6-sol")]
    with pytest.raises(SystemExit): cli.cmd_refine(_ns("refine", name="gpt56sol-codex-s0", agent="opencode", force=True))


def test_unknown_own_harness_is_refused(tmp_path, monkeypatch):
    _, calls = _setup(tmp_path, monkeypatch, meta=None)               # no trial.json, run name without a harness suffix
    with pytest.raises(SystemExit): cli.cmd_refine(_ns("refine", agent="codex"))
    assert calls == []


def test_cli_parser_has_no_agent_default(monkeypatch):
    """`gab refine` with no --agent / --model: None (the trial's), not "claude" as in `gab run`"""
    seen = {}
    monkeypatch.setattr(cli, "cmd_refine", lambda a: seen.__setitem__("refine", (a.agent, a.model)))
    cli.main(["refine", "--name", "x"])
    assert seen == {"refine": (None, None)}


def test_refine_round_records_its_harness(tmp_path, monkeypatch):
    d = tmp_path / "n" / "T-x" / "fine"; (d / "agent").mkdir(parents=True); (d / "deliverable").mkdir()
    trial.wjson(d / "agent" / "agent.json", {"exit_reason": "done"})
    refine._after(d, Done(), "fine", None, 1, 5, AgentRun(exit_reason="done", wall_s=1), None, None, None, after=False, harness="codex")
    assert trial.rjson(d / "agent" / "agent.json")["refine_rounds"][0]["harness"] == "codex"

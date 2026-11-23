"""fix and continue (2026-11-05): a trial stopped by a model-API infra error resumes its session with the time left;
the Gemini tool-schema cleanup of the host key proxy"""
import json

from gab.run import infra, trial
from gab.agent.base import AgentRun
from gab.host.keyproxy import clean_tool_schemas
from gab.util import wjson


def _trial(tmp_path, block, wall_s=1800, sid="S1"):
    d = tmp_path / "t"; (d / "work" / "deliverable" / "mod").mkdir(parents=True); (d / "agent").mkdir()
    (d / "work" / "deliverable" / "mod" / "a.lua").write_text("x")
    wjson(d / "agent" / "agent.json", {"wall_s": wall_s, "tokens_in": 100, "tokens_out": 10, "usd": 1.5, "infra": block,
                                       "extra": {"session_id": sid, "tool_calls": 7}, "leaks": []})
    (d / "transcript.jsonl").write_text('{"old": 1}\n'); (d / "tools").mkdir(); (d / "tools" / "calls.jsonl").write_text("{}\n")
    return d


def api_block(code="subscription_limit", msg="Claude AI usage limit reached"):
    return infra.assess([{**infra.make(code, msg), "source": "transcript"}], "crash")


def test_resumable_only_for_model_api():
    assert infra.resumable(api_block())
    game = infra.assess([{**infra.make("docker_socket_denied", "permission denied docker.sock"), "source": "x"}], "crash")
    assert not infra.resumable(game)                     # the agent saw a broken environment: start over
    mixed = infra.assess([{**infra.make("api_upstream", "503"), "source": "x"}, {**infra.make("container_gone", "No such container"), "source": "y"}], "crash")
    assert not infra.resumable(mixed) and not infra.resumable(None)


def test_resume_state_and_archive(tmp_path):
    d = _trial(tmp_path, api_block())
    rs = trial.resume_state(d, 120)
    assert rs["sid"] == "S1" and abs(rs["left"] - 90) < 1e-6
    dst = trial.archive_for_resume(d)
    assert (d / "work" / "deliverable" / "mod" / "a.lua").exists()                 # the workspace stays
    assert not (d / "transcript.jsonl").exists() and (dst / "transcript.jsonl").exists() and (dst / "tools" / "calls.jsonl").exists()
    assert (dst / "agent" / "agent.json").exists() and (d / "agent" / "agent.json").exists()


def test_no_resume_when(tmp_path):
    assert trial.resume_state(_trial(tmp_path / "a", api_block(), wall_s=118 * 60), 120) is None   # < 5 min left
    assert trial.resume_state(_trial(tmp_path / "b", None), 120) is None                            # not an infra error


def test_combine_runs():
    prev = {"wall_s": 1800, "tokens_in": 100, "tokens_out": 10, "tokens_cached": 5, "usd": 1.5, "infra": {"primary": "subscription_limit"},
            "extra": {"session_id": "S1", "tool_calls": 7}}
    r = trial.combine_runs(prev, AgentRun(wall_s=600, tokens_in=50, tokens_out=5, usd=0.5, extra={"tool_calls": 3}))
    assert (r.wall_s, r.tokens_in, r.tokens_out, r.tokens_cached, r.usd, r.extra["tool_calls"]) == (2400, 150, 15, 5, 2.0, 10)
    assert r.extra["resumed_after"] == [{"wall_s": 1800, "infra": "subscription_limit", "session_id": "S1"}]


def test_brief_renders_with_the_same_kwargs_stage_gives_it():
    """a literal JSON / code example inside BRIEF or GUIDE must escape braces as {{ }}: brief_text() `.format()`s the whole
    brief with exactly these kwargs for every trial, before any agent sees it"""
    from pathlib import Path
    class T:
        reference_ablation = None
        def instruction(self, lv): return "i"
        def references(self, kind=None): return []
    out = trial.brief_text(T(), "fine", "flare", Path("/nonexistent"), 5.0)
    assert "gen-image" in out and '{"ok": false' in out


def test_gemini_schema_cleaned():
    params = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "additionalProperties": False,
              "properties": {"if": {"type": "string"}, "n": {"type": "integer", "exclusiveMinimum": 0}, "k": {"const": "a"},
                             "l": {"type": "array", "items": {"type": "object", "propertyNames": {"pattern": "x"}}}}}
    body = json.dumps({"model": "gemini-3.8-flash", "tools": [{"type": "function", "function": {"name": "f", "parameters": params}}]}).encode()
    p = json.loads(clean_tool_schemas(body))["tools"][0]["function"]["parameters"]
    assert "$schema" not in p and set(p["properties"]) == {"if", "n", "k", "l"}                # property names untouched
    assert p["properties"]["n"] == {"type": "integer", "minimum": 0} and p["properties"]["k"] == {"enum": ["a"]}
    assert p["properties"]["l"]["items"] == {"type": "object"}
    resp = json.dumps({"model": "google/gemini-3.8-flash", "tools": [{"type": "function", "name": "f", "parameters": params}]}).encode()
    assert "$schema" not in json.loads(clean_tool_schemas(resp))["tools"][0]["parameters"]       # responses API shape
    other = json.dumps({"model": "gpt-5.6-sol", "tools": [{"type": "function", "function": {"name": "f", "parameters": params}}]}).encode()
    assert clean_tool_schemas(other) == other and clean_tool_schemas(b"not json") == b"not json"


def test_pending_resume_survives_a_failed_start(tmp_path):
    d = _trial(tmp_path, api_block()); prev = trial.rjson(d / "agent" / "agent.json")
    wjson(d / "agent" / "resume.json", {"sid": "S1", "prev": prev})                   # written before the resumed run
    harness = infra.assess([{**infra.make("harness_bug", "RuntimeError: no container build"), "source": "x"}], "crash")
    wjson(d / "agent" / "agent.json", {"wall_s": 2, "infra": harness, "extra": {"tb": "x", "resume_pending": True}})
    rs = trial.resume_state(d, 120)
    assert rs and rs["sid"] == "S1" and abs(rs["left"] - 90) < 1e-6
    assert trial.ran_nothing(AgentRun(extra={"tb": "x"})) and not trial.ran_nothing(AgentRun(tokens_in=5, extra={"tb": "x"}))

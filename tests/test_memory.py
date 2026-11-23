"""the refine rounds' experience memory (gab/refine/memory.py; 2026-11-12): seeding, budgeted retrieval, extraction (mocked
model), folding / promotion, the privacy and hidden-material guard, one memory per run, the lock, REFINE_MSG, --no-memory"""
import importlib.util, json, multiprocessing, os
from pathlib import Path

import pytest

from gab import cli, config
from gab.refine import memory as M, refine
from gab.agent.base import AgentRun
from gab.util import rjson, wjson

REPO = Path(__file__).resolve().parents[1]


class Task:
    def __init__(self, id="T-mc-1", game="minecraft", instr="Build a lit lantern festival with boats on a river."):
        self.id, self.game, self._i = id, game, instr
    mdir = Path("/nonexistent")
    def instruction(self, level): return self._i
    def references(self, kind=None): return []
    def clips(self): return []


def mem(tmp_path, name="cfg-a"):
    os.environ["GAB_MEMORY_DIR"] = str(tmp_path / "memory")
    return M.root(name)


# ---------------------------------------------------------------- seeding
def test_commercial_games_are_seeded_open_source_start_empty(tmp_path):
    r = mem(tmp_path)
    with M.locked(r):
        M.ensure(r, "minecraft"); M.ensure(r, "supertuxkart")
    mc = M.entries(M.game_file(r, "minecraft").read_text())
    assert len(mc) >= 10 and all(e["game"] == "minecraft" for e in mc)
    assert M.entries(M.game_file(r, "supertuxkart").read_text()) == []
    assert "## Development" in M.game_file(r, "supertuxkart").read_text()
    assert len(M.entries(M.role_file(r).read_text())) >= 3
    seed = rjson(r / "SEED.json")
    assert seed["files"]["games/minecraft.md"]["source"]["source_file"] == "gameanything/games/minecraft/LESSONS.md"
    assert seed["files"]["games/supertuxkart.md"]["from"] is None and seed["files"]["roles/builder.md"]["from"]
    for g in M.SEED_GAMES: assert (M.SEED_DIR / "games" / f"{g}.md").exists()


def test_seed_files_carry_no_private_or_hidden_hit():
    spec = importlib.util.spec_from_file_location("memory_seed", REPO / "scripts" / "memory_seed.py")
    ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
    for f in sorted(M.SEED_DIR.glob("games/*.md")) + sorted(M.SEED_DIR.glob("roles/*.md")):   # (not README.md)
        for e in M.entries(f.read_text()):
            txt = " ".join(str(e.get(k) or "") for k in ("symptom", "cause", "fix", "evidence"))
            assert not ms.hits(txt), (f.name, e["id"], ms.hits(txt))
            assert not M.guard_hits(txt), (f.name, e["id"], M.guard_hits(txt))
            assert "seed: GameAnything" in e["evidence"]                   # provenance on every seeded entry
        assert not any("\u4e00" <= ch <= "\u9fff" for ch in f.read_text()), f.name      # English, field names included
    meta = json.loads((M.SEED_DIR / "SEED.json").read_text())
    assert all(len(v["source_sha256"]) == 64 for v in meta["files"].values())
    for rel, v in meta["files"].items():                                   # compact SEED.json: ids grouped, counts consistent
        ids = [e["id"] for e in M.entries((M.SEED_DIR / rel).read_text())]
        assert v["kept"] == ids and v["counts"]["kept"] == len(ids)
        assert v["counts"]["entries_in_source"] == len(ids) + len(v["dropped"])
        assert v["translated"] == "all" or set(v["translated"]) <= set(ids)
        assert set(v["evidence_replaced"]) <= set(ids) and set(v["adapted"]) <= set(ids)
    sj = (M.SEED_DIR / "SEED.json").read_text()                            # categories only, never the matched text
    assert "她" not in sj and "matched" not in sj.lower()


OLD_FORMAT = """# minecraft: lessons (builder)

## 开发

### minecraft-001
- date: 2026-11-10
- game: minecraft
- role: builder
- 现象: Boats built from block displays drift apart.
- 原因: The parts share one tag.
- 做法: Give every part a unique tag.
- 证据: task T-old (fine) refine r1: both boats sail
- 可信度: verified repeatedly
- status: confirmed
"""


def test_old_chinese_format_still_parses_and_renders_english(tmp_path):
    """stores and logs written before 2026-11-23 use GameAnything's Chinese field names and section: still read, shown in English"""
    e = M.entries(OLD_FORMAT)[0]
    assert e["symptom"].startswith("Boats") and e["cause"] and e["fix"] and e["evidence"].startswith("task T-old")
    assert e["confidence"] == "verified repeatedly" and e["_section"] == M.SECTION == "Development"
    out = M.unparse(M.parse(OLD_FORMAT))
    assert "## Development" in out and "- symptom: Boats" in out and "- evidence: task T-old" in out
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in out)
    assert M.entries(out) == [e]                                           # the English rendering reads back the same
    r = mem(tmp_path)
    M.game_file(r, "minecraft").parent.mkdir(parents=True); M.game_file(r, "minecraft").write_text(OLD_FORMAT)
    M.role_file(r).parent.mkdir(parents=True); M.role_file(r).write_text(M.role_template())
    text, ids, _ = M.retrieve(r, "minecraft", "boats", 6000)
    assert ids == ["minecraft-001"] and "- symptom: Boats" in text and "- fix: Give" in text
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in text)
    assert "- symptom: Boats" in M.show("cfg-a", "minecraft") and "现象" not in M.show("cfg-a", "minecraft")
    with M.locked(r):                                                      # folding into an old store: comes out in English
        res = M.add(r, "minecraft", "game", "Boats built from block displays drift apart", "The parts share one tag.", "f",
                    "task T-new (fine) refine r1: x", task="T-new")
        new = M.add(r, "minecraft", "game", "Lanterns flicker at night", "light updates", "re-place lights", "e", task="T-new")
    assert res["action"] == "merged" and new["action"] == "added"
    txt = M.game_file(r, "minecraft").read_text()
    assert txt.count("## Development") == 1 and "开发" not in txt and "现象" not in txt
    assert [x["id"] for x in M.entries(txt)] == ["minecraft-001", "minecraft-002"]
    assert M._kept_ids({"kept": [{"id": "a-1", "translated": True}]}) == {"a-1"} == M._kept_ids({"kept": ["a-1"]})


# ---------------------------------------------------------------- retrieval
def test_retrieval_respects_the_budget_and_puts_confirmed_first(tmp_path):
    r = mem(tmp_path)
    with M.locked(r):
        M.ensure(r, "minecraft")
        M.add(r, "minecraft", "game", "Boats built from block displays drift apart", "shared tags", "use unique tags", "e1", task="T1")
    for budget in (1500, 4000, 6000):
        text, ids, snap = M.retrieve(r, "minecraft", "boats lantern", budget)
        assert 0 < len(text) <= budget and ids and len(snap) == 16
    text, ids, _ = M.retrieve(r, "minecraft", "", 100000)
    st = {e["id"]: e.get("status") for e in M.collect(r, "minecraft")}
    seen_cand = False
    for i in ids:                                                        # confirmed first, then candidates
        if st[i] != "confirmed": seen_cand = True
        else: assert not seen_cand
    assert text.index("## Confirmed") < text.index("## Candidates")
    assert len(M.retrieve(r, "minecraft", "", 1500)[1]) < len(ids)


def test_retrieval_ranks_by_relevance_within_a_group(tmp_path):
    r = mem(tmp_path)
    with M.locked(r):
        M.ensure(r, "supertuxkart")
        M.add(r, "supertuxkart", "game", "Kart textures look washed out", "wrong gamma", "convert textures to sRGB", "e", task="T1")
        M.add(r, "supertuxkart", "game", "Track checkpoints are skipped by the AI karts", "missing drivelines", "add driveline quads", "e", task="T2")
    _, ids, _ = M.retrieve(r, "supertuxkart", "the ai karts skip checkpoints on the track", 100000)
    assert ids.index("supertuxkart-002") < ids.index("supertuxkart-001")


def test_empty_open_source_memory_still_gets_role_lessons(tmp_path):
    r = mem(tmp_path)
    with M.locked(r): M.ensure(r, "xonotic")
    text, ids, _ = M.retrieve(r, "xonotic", "", 6000)
    assert ids and all(i.startswith("builder-") for i in ids)


# ---------------------------------------------------------------- consolidation
def test_folding_and_promotion_across_tasks(tmp_path):
    r = mem(tmp_path)
    with M.locked(r):
        M.ensure(r, "terraria")
        a = M.add(r, "terraria", "game", "NPC sprites render as white boxes", "texture path wrong", "put sprites under Assets", "task T1 (fine) refine r1: x", task="T1")
        b = M.add(r, "terraria", "game", "NPC sprites render as white boxes.", "texture path is wrong", "fix", "task T1 (fine) refine r2: y", task="T1")
        e = next(x for x in M.collect(r, "terraria") if x["id"] == a["id"])
        assert a["action"] == "added" and b["action"] == "merged" and b["id"] == a["id"] and not b["promoted"]
        assert e["status"] == "candidate" and e["confidence"] == "tried once" and e["evidence"].count("\n") == 1
        c = M.add(r, "terraria", "game", "NPC sprites render as white boxes", "texture path wrong", "f", "task T2 (fine) refine r1: z", task="T2")
        e = next(x for x in M.collect(r, "terraria") if x["id"] == a["id"])
        assert c["promoted"] and e["status"] == "confirmed" and e["confidence"] == "verified repeatedly"
        d = M.add(r, "any", "general", "Hand-in replay starts mid-level", "trace skipped the menu", "start traces from the title screen", "e", task="T3")
    assert d["file"].endswith("roles/builder.md") and d["id"].startswith("builder-")
    assert M.entries(M.role_file(r).read_text())[-1]["game"] == "any"


def test_runs_do_not_share_memory(tmp_path):
    ra, rb = mem(tmp_path, "sonnet55-claudecode-s0"), mem(tmp_path, "gpt-codex-s0")
    assert ra != rb
    with M.locked(ra):
        M.ensure(ra, "valheim"); M.add(ra, "valheim", "game", "Portal prefab missing", "not registered", "register in ZNetScene", "e", task="T1")
    with M.locked(rb): M.ensure(rb, "valheim")
    ida = {e["id"] for e in M.collect(ra, "valheim")}; idb = {e["id"] for e in M.collect(rb, "valheim")}
    assert "valheim-005" in ida and "valheim-005" not in idb
    with pytest.raises(M.MemoryError_): M.root("../x")


def _worker(args):
    root, i = args
    r = Path(root)
    with M.locked(r):
        M.ensure(r, "celeste64")
        import hashlib
        h = lambda x: hashlib.sha256(f"{x}{i}".encode()).hexdigest()                 # unrelated texts: never folded
        M.add(r, "celeste64", "game", h("s"), h("c"), f"fix {i}", "e", task=f"T{i}")


def test_parallel_writers_are_serialized(tmp_path):
    r = mem(tmp_path)
    with multiprocessing.get_context("fork").Pool(6) as p: p.map(_worker, [(str(r), i) for i in range(12)])
    es = M.entries(M.game_file(r, "celeste64").read_text())
    assert len(es) == 12 and len({e["id"] for e in es}) == 12


# ---------------------------------------------------------------- guard + extraction
def test_guard_rejects_hidden_material_and_private_content():
    ok = {"scope": "game", "symptom": "Lanterns are dark at night", "cause": "no light level", "fix": "give the block a light level", "evidence": "review r1 said dark"}
    bad = [{**ok, "fix": "match the rubric item about lanterns"}, {**ok, "evidence": "the judge gave 2/5"},
           {**ok, "evidence": "see shot_03.png"}, {**ok, "cause": "path /home/alice/x"}, {**ok, "evidence": "mail a@b.com"},
           {**ok, "fix": "read the hidden reference"}, {**ok, "evidence": "result.json shows pass"},
           {**ok, "evidence": "the eval score went up"}, {**ok, "fix": ""},
           {**ok, "evidence": "token sk-abcdefghijklmnop1234"}]
    ls, ap, rej = M.validate({"lessons": [ok] + bad, "applied": ["x-1", "nope"]}, 3, given=["x-1"])
    assert ls == [{**ok}] and len(rej) == len(bad) and ap == ["x-1"]
    ls, _, rej = M.validate({"lessons": [ok, {**ok, "symptom": "b"}, {**ok, "symptom": "c"}, {**ok, "symptom": "d"}]}, 3)
    assert len(ls) == 3 and "limit" in rej[0]["why"]


def _trial(tmp_path, name="cfg-a", task=None):
    task = task or Task()
    d = tmp_path / "runs" / name / task.id / "fine"
    (d / "agent").mkdir(parents=True); (d / "work" / "deliverable").mkdir(parents=True)
    for k in (0, 1):
        c = d / "refine" / f"r{k}" / "critic"; c.mkdir(parents=True)
        (c / "feedback.md").write_text(f"## Coverage\nboats | not shown\nREVIEW{k}"); wjson(c / "scores.json", {"reference_likeness": k + 1})
    tr = d / "agent" / "transcript.jsonl"
    tr.write_text("\n".join(json.dumps(x) for x in [
        {"type": "user", "message": {"content": [{"type": "text", "text": "USER BRIEF SHOULD NOT APPEAR here at all"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "I made the boats use unique tags so both move."},
                                                       {"type": "tool_use", "input": {"cmd": "ls"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": "TOOL OUTPUT secret-ish"}]}]}},
        {"type": "result", "result": "Done: both boats sail; traces and shots updated."}]) + "\n")
    return d, task, tr


def test_round_retrieves_extracts_and_consolidates(tmp_path, monkeypatch):
    mem(tmp_path)
    d, task, tr = _trial(tmp_path)
    w = d / "work"
    rec = M.before_round(d, task, "fine", 1, d / "refine" / "r0" / "critic", w)
    assert (w / "lessons.md").exists() and rec["ids"] and len((w / "lessons.md").read_text()) <= rec["budget"]
    seen = {}
    def fake_chat(prompt, model):
        seen["prompt"] = prompt; seen["model"] = model
        return json.dumps({"lessons": [
            {"scope": "game", "symptom": "Second boat never moved", "cause": "two boats shared one tag", "fix": "tag each part with an instance id", "evidence": "review r0 not shown, r1 shown"},
            {"scope": "general", "symptom": "x", "cause": "y", "fix": "check the rubric", "evidence": "e"}],
            "applied": [rec["ids"][0], "bogus-1"]}), {"prompt_tokens": 1000, "completion_tokens": 200}
    monkeypatch.setattr(M, "_chat", fake_chat)
    ex = M.after_round(d, task, "fine", 1, AgentRun(transcript=str(tr)), d / "refine" / "r0" / "critic", d / "refine" / "r1" / "critic", "2026-11-12T00:00:00")
    p = seen["prompt"]
    assert "REVIEW0" in p and "REVIEW1" in p and "unique tags" in p and "both boats sail" in p
    assert "USER BRIEF SHOULD NOT" not in p and "TOOL OUTPUT" not in p and task.instruction("fine") in p
    assert ex["added"] == ["minecraft-028"] or ex["added"][0].startswith("minecraft-")
    assert len(ex["rejected"]) == 1 and ex["applied"] == [rec["ids"][0]] and ex["tokens_in"] == 1000 and ex["usd"] and ex["usd"] > 0
    assert ex["model"] == config.get("critic_model") and ex["error"] is None
    e = next(x for x in M.collect(M.root("cfg-a"), "minecraft") if x["id"] == ex["added"][0])
    assert e["status"] == "candidate" and e["evidence"].startswith("task T-mc-1 (fine) refine r1:")
    # a restarted review phase does not extract twice
    monkeypatch.setattr(M, "_chat", lambda *a: (_ for _ in ()).throw(AssertionError("called twice")))
    assert M.after_round(d, task, "fine", 1, AgentRun(), None, None)["added"] == ex["added"]
    st = M.stats("cfg-a")
    assert st["retrievals"] == 1 and st["extractions"] == 1 and st["added"] == 1 and st["applied"] == 1
    assert "minecraft" in M.show("cfg-a", "minecraft")


def test_extraction_failure_never_fails_the_round(tmp_path, monkeypatch):
    mem(tmp_path)
    d, task, tr = _trial(tmp_path)
    M.before_round(d, task, "fine", 1, d / "refine" / "r0" / "critic", d / "work")
    monkeypatch.setattr(M, "_chat", lambda *a: ("not json at all", {}))
    ex = M.after_round(d, task, "fine", 1, AgentRun(transcript=str(tr)), d / "refine" / "r0" / "critic", None, None)
    assert ex["error"] and not ex["added"]
    monkeypatch.delenv(config.get("critic_key_env"), raising=False)
    monkeypatch.setattr(M, "_chat", M._chat_http)                          # no key in the environment: refused before any call
    d2, task2, _ = _trial(tmp_path, task=Task(id="T-mc-2"))
    M.before_round(d2, task2, "fine", 1, d2 / "refine" / "r0" / "critic", d2 / "work")
    assert M.after_round(d2, task2, "fine", 1, AgentRun(), None, None)["error"]


def test_chat_reads_the_key_at_call_time_and_never_logs_it(tmp_path, monkeypatch, capsys):
    import urllib.request
    chat = M._chat_http
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-a-real-key-0000")
    sent = {}
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 5}}).encode()
    def fake_open(req, timeout=None):
        sent["url"] = req.full_url; sent["auth"] = req.headers.get("Authorization"); sent["body"] = req.data.decode(); return Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    txt, usage = chat("hello", "gemini-3.8-flash")
    assert sent["url"].endswith("/chat/completions") and sent["auth"] == "Bearer sk-test-not-a-real-key-0000"
    assert "sk-test" not in sent["body"] and "sk-test" not in capsys.readouterr().err and usage["prompt_tokens"] == 5


# ---------------------------------------------------------------- refine integration
def test_refine_msg_line_only_with_memory():
    kw = dict(minutes=40, min_done=30, work="/w", out="/w/deliverable", brief="B", tg="")
    on = refine.REFINE_MSG.format(lessons=refine.LESSONS_LINE.format(work="/w"), **kw)
    off = refine.REFINE_MSG.format(lessons="", **kw)
    assert "/w/lessons.md" in on and "may not apply" in on and "lessons" not in off
    assert on.replace(refine.LESSONS_LINE.format(work="/w"), "") == off


class FakeAd:
    def provision(self, inst): pass
    def stop(self, inst): pass


class FakeAgent:
    model, opts, can_resume = "m", {}, False
    def __init__(self): self.prompts = []
    def version(self): return "v"
    def session_of(self, p): return None
    def run(self, w, msg, budget, tools, **k):
        self.prompts.append(msg); (Path(w) / "deliverable" / "DONE").write_text("")
        return AgentRun(exit_reason="done", wall_s=1.0, usd=0.5)


def _refine_env(tmp_path, monkeypatch):
    from gab.run import trial
    from gab.agent import base as abase
    d, task, tr = _trial(tmp_path)
    import shutil; shutil.rmtree(d / "refine" / "r1")
    wjson(d / "refine" / "r0" / "replay" / "record.json", {"traces": []}); (d / "deliverable").mkdir()
    wjson(d / "agent" / "agent.json", {"exit_reason": "done"})
    ag = FakeAgent()
    monkeypatch.setattr(trial, "write_ctx", lambda *a, **k: None); monkeypatch.setattr(trial, "tools", lambda *a, **k: {})
    monkeypatch.setattr(trial, "brief_text", lambda *a, **k: "BRIEF"); monkeypatch.setattr(refine, "copy_targets", lambda t, w: 0)
    monkeypatch.setattr(refine.gbase, "get", lambda *a, **k: FakeAd()); monkeypatch.setattr(abase, "get", lambda *a, **k: ag)
    monkeypatch.setattr(refine, "record_replays", lambda d, task, level, seat, out, **k: wjson(Path(out) / "record.json", {}))
    def crit(d, task, level, seat, rd, rp, **k):
        (Path(rd) / "critic").mkdir(parents=True, exist_ok=True); (Path(rd) / "critic" / "feedback.md").write_text("AFTER")
        wjson(Path(rd) / "critic" / "scores.json", {"reference_likeness": 3}); return {"reference_likeness": 3}
    monkeypatch.setattr(refine, "critic", crit)
    return d, task, ag


def test_refine_round_with_memory_by_default(tmp_path, monkeypatch):
    mem(tmp_path)
    d, task, ag = _refine_env(tmp_path, monkeypatch)
    monkeypatch.setattr(M, "_chat", lambda p, m: (json.dumps({"lessons": [{"scope": "game", "symptom": "s", "cause": "c", "fix": "f", "evidence": "e"}]}),
                                                 {"prompt_tokens": 10, "completion_tokens": 5}))
    refine.refine_trial(d, task, "fine", "opencode", "m", {"until_done": False}, 1, seat=None)
    assert "lessons.md" in ag.prompts[0] and (d / "work" / "lessons.md").exists()
    rr = rjson(d / "agent" / "agent.json")["refine_rounds"][0]
    assert rr["memory"]["retrieval"]["ids"] and rr["memory"]["retrieval"]["snapshot"]
    assert rr["memory"]["extraction"]["added"] and rr["usd"] == 0.5               # the agent's cost, extraction apart
    assert rr["memory"]["extraction"]["tokens_in"] == 10


def test_no_memory_round(tmp_path, monkeypatch):
    mem(tmp_path)
    d, task, ag = _refine_env(tmp_path, monkeypatch)
    (d / "work" / "lessons.md").write_text("STALE")
    monkeypatch.setattr(M, "_chat", lambda *a: (_ for _ in ()).throw(AssertionError("no extraction without memory")))
    refine.refine_trial(d, task, "fine", "opencode", "m", {"until_done": False}, 1, seat=None, memory=False)
    assert "lessons.md" not in ag.prompts[0] and not (d / "work" / "lessons.md").exists()
    assert "memory" not in rjson(d / "agent" / "agent.json")["refine_rounds"][0]
    assert not M.root("cfg-a").exists()


def test_cli_no_memory_flag(monkeypatch):
    import argparse
    real = argparse.ArgumentParser.parse_args
    # parse only: the refine parser accepts --no-memory, default off (= memory on)
    ap_args = []
    def spy(self, argv=None, ns=None):
        a = real(self, argv, ns); ap_args.append(a); raise SystemExit(0)
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", spy)
    for argv, want in ((["refine", "--name", "x", "--no-memory"], True), (["refine", "--name", "x"], False)):
        with pytest.raises(SystemExit): cli.main(argv)
        assert ap_args[-1].no_memory is want
    with pytest.raises(SystemExit): cli.main(["memory", "stats", "--name", "x"])
    assert ap_args[-1].action == "stats"


def test_extraction_retries_a_truncated_answer_and_keeps_the_raw_answers(tmp_path, monkeypatch):
    # 2026-11-20: gemini counts thinking tokens in max_tokens; an answer cut at the limit has no JSON object -> one retry
    mem(tmp_path)
    d, task, tr = _trial(tmp_path)
    M.before_round(d, task, "fine", 1, d / "refine" / "r0" / "critic", d / "work")
    good = json.dumps({"lessons": [{"scope": "game", "symptom": "Boat stuck", "cause": "shared tag", "fix": "unique tags", "evidence": "r1"}], "applied": []})
    calls = []
    def fake_chat(prompt, model):
        calls.append(1)
        if len(calls) == 1: return '{"lessons": [{"scope": "game", "symptom": "Bo', {"prompt_tokens": 900, "completion_tokens": 1996, "finish_reason": "length"}
        return good, {"prompt_tokens": 900, "completion_tokens": 300, "finish_reason": "stop"}
    monkeypatch.setattr(M, "_chat", fake_chat)
    ex = M.after_round(d, task, "fine", 1, AgentRun(transcript=str(tr)), d / "refine" / "r0" / "critic", d / "refine" / "r1" / "critic", "2026-11-12T00:00:00")
    assert ex["error"] is None and len(ex["added"]) == 1 and ex["attempts"] == 2 and ex["finish_reasons"] == ["length", "stop"]
    assert ex["tokens_out"] == 2296 and ex["tokens_in"] == 1800
    ans = json.loads((d / "agent" / "memory_r1_answers.json").read_text())
    assert [a["finish_reason"] for a in ans] == ["length", "stop"] and ans[1]["text"] == good


def test_extraction_gives_up_after_the_retries_and_records_the_error(tmp_path, monkeypatch):
    mem(tmp_path)
    d, task, tr = _trial(tmp_path)
    M.before_round(d, task, "fine", 1, d / "refine" / "r0" / "critic", d / "work")
    monkeypatch.setattr(M, "_chat", lambda p, m: ("no json here", {"prompt_tokens": 10, "completion_tokens": 2000, "finish_reason": "length"}))
    ex = M.after_round(d, task, "fine", 1, AgentRun(transcript=str(tr)), d / "refine" / "r0" / "critic", None, "2026-11-12T00:00:00")
    assert ex["attempts"] == 2 and ex["added"] == [] and "no JSON object" in ex["error"] and "length" in ex["error"]

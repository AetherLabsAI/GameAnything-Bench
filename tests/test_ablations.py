"""ablation settings (2026-11-19): A) refine-round ablations from the source's R0 in a new run (gab refine --ablation
nomem | nocritic / --no-critic: the matched-budget control); B) reference ablation of construction runs (gab run --no-reference
/ --drop-modality). Fakes only: no game, no agent, no model call."""
import json, shutil
from pathlib import Path

import pytest
import yaml

from gab import cli
from gab.refine import refine
from gab.run import trial
from gab.agent.base import AgentRun
from gab.util import rjson, wjson


class HybridTask:
    id, game = "T-HY-test", "stardew_valley"
    def __init__(self, root):
        self.dir = root / "task"; self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "instruction_fine.md").write_text("Make the festival from the clip.")
        self.mdir = root / "media"; self.mdir.mkdir(exist_ok=True)
        self.refs = {"refs": [{"id": "c01", "kind": "video", "file": "clip_01.mp4"}, {"id": "t01", "kind": "text", "file": "text_01.md"},
                              {"id": "i01", "kind": "image", "file": "img_01.jpg"}]}
        for r in self.refs["refs"]: (self.mdir / r["file"]).write_bytes(b"x")
        self.meta = {"task_id": self.id, "target_game": self.game, "modality": "video+text+image", "references": ["c01", "t01", "i01"]}
    modality = property(lambda s: s.meta["modality"])
    def instruction(self, level): return (self.dir / "instruction_fine.md").read_text()
    def references(self, kind=None): return [r for r in self.refs["refs"] if kind is None or r["kind"] == kind]
    def clips(self): return [self.mdir / c["file"] for c in self.references("video")]


def _targets(tmp_path, monkeypatch, task):
    monkeypatch.setattr(refine, "targets_dir", lambda: tmp_path / "targets")
    t = tmp_path / "targets" / task.id; t.mkdir(parents=True); (t / "target_01.png").write_bytes(b"p")
    monkeypatch.setattr(trial.gbase, "gdir", lambda g: tmp_path / "nogame")


# ---------------------------------------------------------------- B. reference ablation
def test_reference_setting_flags():
    assert trial.reference_setting() is None and trial.reference_setting(True) == "none"
    assert trial.reference_setting(drop="video") == "drop-video"
    with pytest.raises(ValueError): trial.reference_setting(True, "video")
    with pytest.raises(ValueError): trial.reference_setting(drop="audio")
    assert trial.dropped_kinds("none") == {"video", "text", "image"} and trial.dropped_kinds("drop-text") == {"text"}


def test_full_task_is_staged_as_before(tmp_path, monkeypatch):
    task = HybridTask(tmp_path); _targets(tmp_path, monkeypatch, task)
    assert trial.agent_task(task, None) is task                       # default: the task itself, nothing wrapped
    w, b = trial.stage(tmp_path / "d", task, "fine", task.game, 60)
    assert sorted(p.name for p in (w / "reference").iterdir()) == ["clip_01.mp4", "img_01.jpg", "text_01.md"]
    assert (w / "targets" / "target_01.png").exists() and "targets/ has target pictures" in b
    assert "clip_01.mp4" in b and "text_01.md" in b and "img_01.jpg" in b and trial.NO_REFS not in b


def test_no_reference_stages_instruction_only(tmp_path, monkeypatch):
    task = HybridTask(tmp_path); _targets(tmp_path, monkeypatch, task)
    w, b = trial.stage(tmp_path / "d", trial.agent_task(task, "none"), "fine", task.game, 60)
    assert list((w / "reference").iterdir()) == [] and not (w / "targets").exists()
    assert trial.NO_REFS in b and "Make the festival from the clip." in b
    assert "clip_01" not in b and "text_01" not in b and "img_01" not in b and "targets/ has target pictures" not in b
    ty = yaml.safe_load((w / "task" / "task.yaml").read_text())
    assert ty["references"] == [] and ty["modality"] == "none"
    assert task.references() and len(task.references()) == 3          # the task itself (the evaluation's) is untouched


@pytest.mark.parametrize("kind,gone,targets", [("video", "clip_01.mp4", False), ("image", "img_01.jpg", False), ("text", "text_01.md", True)])
def test_drop_modality(tmp_path, monkeypatch, kind, gone, targets):
    task = HybridTask(tmp_path); _targets(tmp_path, monkeypatch, task)
    at = trial.agent_task(task, f"drop-{kind}")
    w, b = trial.stage(tmp_path / "d", at, "fine", task.game, 60)
    files = sorted(p.name for p in (w / "reference").iterdir())
    assert gone not in files and len(files) == 2 and gone not in b and all(f in b for f in files)
    assert (w / "targets").exists() == targets and ("targets/ has target pictures" in b) == targets
    assert trial.NO_REFS not in b and trial.KIND_TEXT[kind] not in b
    ty = yaml.safe_load((w / "task" / "task.yaml").read_text())
    assert kind not in ty["modality"].split("+") and len(ty["references"]) == 2


def test_run_trial_records_the_setting_and_refuses_mixing(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, "trial_dir", lambda name, tid, lv: tmp_path / name / tid / lv)
    monkeypatch.setattr(trial, "_run_trial", lambda *a, **k: {"status": "agent_done"})
    monkeypatch.setattr(trial.config, "get", lambda k, *x: None if k == "infra_resume" else trial.config.DEFAULTS.get(k))
    task = HybridTask(tmp_path)
    trial.run_trial("dsv-opencode-noref-s0", task, "fine", "opencode", opts={"reference_ablation": "none", "brief": "v2"}, skip_eval=True)
    m = rjson(tmp_path / "dsv-opencode-noref-s0" / task.id / "fine" / "trial.json")
    assert m["reference_ablation"] == "none" and m["name"] == "dsv-opencode-noref-s0"
    with pytest.raises(RuntimeError, match="reference setting none"):  # the same dir under another setting
        trial.run_trial("dsv-opencode-noref-s0", task, "fine", "opencode", opts={"brief": "v2"}, skip_eval=True)
    trial.run_trial("dsv-opencode-s0", task, "fine", "opencode", opts={"brief": "v2"}, skip_eval=True)
    assert "reference_ablation" not in rjson(tmp_path / "dsv-opencode-s0" / task.id / "fine" / "trial.json")   # default: as before


def test_cli_run_reference_flags(monkeypatch):
    got = {}
    monkeypatch.setattr(cli, "cmd_run", lambda a: got.update(vars(a)))
    cli.main(["run", "--name", "x-noref-s0", "--no-reference"]); assert got["no_reference"] and got["drop_modality"] is None
    cli.main(["run", "--name", "x-novideo-s0", "--drop-modality", "video"]); assert got["drop_modality"] == "video"
    cli.main(["run", "--name", "x-s0"]); assert not got["no_reference"] and got["drop_modality"] is None
    with pytest.raises(SystemExit): cli.main(["run", "--name", "x", "--drop-modality", "audio"])


# ---------------------------------------------------------------- A. refine-round ablations
def test_nocritic_prompt_is_refine_msg_minus_the_review():
    kw = dict(minutes=60, min_done=45, work="/w", out="/w/deliverable", brief="BRIEF", tg="")
    on = refine.REFINE_MSG.format(lessons="", **kw); off = refine.REFINE_MSG_NOCRITIC.format(**kw)
    assert "review" not in off.lower() and "lessons" not in off and "Keep improving your submission" in off
    for same in ("This is an IMPROVEMENT round of about 60 minutes", "a message alone does not count", "A missing part costs much more",
                 "replay your\nexisting traces", "accepted only after minute\n45", "BRIEF"):
        assert same in on and same in off
    assert on.split("Keep `game check` passing")[1] == off.split("Keep `game check` passing")[1]   # the same tail


def test_ablation_run_name():
    assert refine.ablation_run_name("dsv41flash-opencode-s0", "nocritic") == "dsv41flash-opencode-nocritic-s0"
    assert refine.ablation_run_name("dsv41flash-opencode-s1", "nomem") == "dsv41flash-opencode-nomem-s1"
    assert refine.ablation_run_name("x", "nomem") == "x-nomem"
    with pytest.raises(ValueError): refine.ablation_run_name("x-s0", "other")


def _source(tmp_path):
    """a source trial with its base attempt (attempts/2, 0 rounds) and R1 (current, 1 round), refine/r0 reviewed"""
    src = tmp_path / "runs" / "dsv-opencode-s0" / "T-HY-test" / "fine"
    def dv(p, tag):
        (p / "mod").mkdir(parents=True); (p / "mod" / "m.txt").write_text(tag); (p / "traces").mkdir(); (p / "DONE").write_text("")
    a = src / "attempts" / "2"; (a / "agent").mkdir(parents=True)
    wjson(a / "agent" / "agent.json", {"exit_reason": "done", "usd": 1.0}); dv(a / "deliverable", "base")
    (src / "agent").mkdir(); wjson(src / "agent" / "agent.json", {"exit_reason": "done", "refine_rounds": [{"round": 1}]})
    dv(src / "deliverable", "r1"); dv(src / "work" / "deliverable", "r1")
    wjson(src / "trial.json", {"task": "T-HY-test", "level": "fine", "agent": "opencode", "model": "m", "name": "dsv-opencode-s0"})
    c = src / "refine" / "r0" / "critic"; c.mkdir(parents=True)
    (c / "feedback.md").write_text("R0 REVIEW: the lanterns are missing"); wjson(c / "scores.json", {"reference_likeness": 1})
    wjson(src / "refine" / "r0" / "replay" / "record.json", {"traces": []}); wjson(src / "refine" / "r0" / "gate.json", {"pass": True})
    (src / "refine" / "r0" / "critic_old_1").mkdir()
    return src


def tree(p): return {str(f.relative_to(p)): f.read_bytes() for f in sorted(Path(p).rglob("*")) if f.is_file()}


class Ad:
    def provision(self, i): pass
    def stop(self, i): pass


class Agent:
    model, opts, can_resume = "m", {}, False
    def __init__(self): self.prompts = []; self.ws = []
    def version(self): return "v"
    def session_of(self, p): return None
    def run(self, w, msg, budget, tools, **k):
        self.prompts.append(msg); self.ws.append(sorted(p.name for p in Path(w).iterdir()))
        assert (Path(w) / "deliverable" / "mod" / "m.txt").read_text() == "base"          # starts from R0
        (Path(w) / "deliverable" / "DONE").write_text(""); return AgentRun(exit_reason="done", wall_s=60.0, usd=0.2)


def _env(tmp_path, monkeypatch):
    from gab.agent import base as abase
    from gab.refine import memory as M
    monkeypatch.setenv("GAB_MEMORY_DIR", str(tmp_path / "memory"))
    task = HybridTask(tmp_path); _targets(tmp_path, monkeypatch, task)
    ag = Agent()
    monkeypatch.setattr(trial, "write_ctx", lambda *a, **k: None); monkeypatch.setattr(trial, "tools", lambda *a, **k: {})
    monkeypatch.setattr(refine.gbase, "get", lambda *a, **k: Ad()); monkeypatch.setattr(abase, "get", lambda *a, **k: ag)
    monkeypatch.setattr(refine, "record_replays", lambda d, task, level, seat, out, **k: wjson(Path(out) / "record.json", {}))
    crit = []
    def fake_critic(d, task, level, seat, rd, rp, **k):
        crit.append(Path(rd).name); (Path(rd) / "critic").mkdir(parents=True, exist_ok=True)
        (Path(rd) / "critic" / "feedback.md").write_text("AFTER"); wjson(Path(rd) / "critic" / "scores.json", {"reference_likeness": 3})
        return {"reference_likeness": 3}
    monkeypatch.setattr(refine, "critic", fake_critic)
    monkeypatch.setattr(refine, "eval_gate_warning", lambda *a, **k: crit.append("gate"))
    monkeypatch.setattr(M, "_chat", lambda *a: (_ for _ in ()).throw(AssertionError("no memory call in an ablation")))
    return task, ag, crit


@pytest.mark.parametrize("abl", ["nocritic", "nomem"])
def test_ablation_round_from_r0_in_a_new_run(tmp_path, monkeypatch, abl):
    task, ag, crit = _env(tmp_path, monkeypatch)
    src = _source(tmp_path); before = tree(src)
    name = refine.ablation_run_name("dsv-opencode-s0", abl); new = tmp_path / "runs" / name / task.id / "fine"
    run, sb, sa = refine.ablation_round(src, new, task, "fine", "opencode", "m", {"until_done": False}, 60, None, name, abl)
    assert tree(src) == before                                          # the source trial is only read
    msg = ag.prompts[0]
    assert "lessons.md" not in msg and not (new / "work" / "lessons.md").exists()
    if abl == "nocritic":
        assert msg.startswith(refine.REFINE_MSG_NOCRITIC.split("{")[0]) and "Keep improving your submission" in msg
        assert "review" not in ag.ws[0] and "R0 REVIEW" not in msg and "review/" not in msg
        assert crit == ["r1"]                                           # only the after-review (scores); no gate warning
    else:
        assert "review/feedback.md" in msg and "review" in ag.ws[0]
        assert (new / "work" / "review" / "feedback.md").read_text() == "R0 REVIEW: the lanterns are missing"   # the source's R0 review
        assert crit == ["gate", "r1"]
    assert "targets/" in msg and "Make the festival from the clip." in msg
    tm = rjson(new / "trial.json")
    assert tm["name"] == name and tm["refine_ablation"]["setting"] == abl and tm["refine_ablation"]["base_snapshot"].endswith("attempts/2/deliverable")
    rr = rjson(new / "agent" / "agent.json")["refine_rounds"]
    assert len(rr) == 1 and rr[0]["setting"] == refine.ABLATIONS[abl]["setting"] and rr[0]["scores_before"] == {"reference_likeness": 1}
    assert "memory" not in rr[0] and sa == {"reference_likeness": 3}
    assert not (new / "refine" / "r0" / "critic_old_1").exists()
    assert refine.snapshot(new, 0) is not None                         # the seeded base (archived by the round)
    assert refine.snapshot(new, 1) == new / "deliverable"
    with pytest.raises(RuntimeError, match="seeded for refine ablation"):
        refine.ablation_round(src, new, task, "fine", "opencode", "m", {}, 60, None, name, "nomem" if abl == "nocritic" else "nocritic")


def test_default_refine_round_records_its_setting(tmp_path, monkeypatch):
    task, ag, crit = _env(tmp_path, monkeypatch)
    src = _source(tmp_path)
    shutil.rmtree(src / "attempts"); shutil.rmtree(src / "deliverable"); shutil.copytree(src / "work" / "deliverable", src / "deliverable")
    wjson(src / "agent" / "agent.json", {"exit_reason": "done"})
    (src / "work" / "deliverable" / "mod" / "m.txt").write_text("base")
    refine.refine_trial(src, task, "fine", "opencode", "m", {"until_done": False}, 1, seat=None, memory=False)
    assert rjson(src / "agent" / "agent.json")["refine_rounds"][0]["setting"] == "critic"
    assert "review/feedback.md" in ag.prompts[0]                        # the normal round is unchanged


def test_cli_refine_ablation_flags(monkeypatch):
    got = {}
    monkeypatch.setattr(cli, "cmd_refine", lambda a: got.update(vars(a)))
    cli.main(["refine", "--name", "x-s0", "--no-critic"]); assert got["no_critic"] and got["ablation"] is None
    cli.main(["refine", "--name", "x-s0", "--ablation", "nomem", "--into", "y-s0"]); assert got["ablation"] == "nomem" and got["into"] == "y-s0"
    cli.main(["refine", "--name", "x-s0"]); assert not got["no_critic"] and got["ablation"] is None


def test_cli_ablation_seeds_a_source_with_three_rounds(tmp_path, monkeypatch):
    """2026-11-19: `gab refine --ablation nomem` on a source with R1-R3 selected 0 trials (the --rounds skip ran on the
    source): the source's own rounds do not matter, the new run starts from its R0"""
    import contextlib
    from gab.run import sched, interrupt
    from gab.host import base as hbase
    from gab.agent import incontainer
    from gab.run.task import Task
    src = _source(tmp_path)
    wjson(src / "agent" / "agent.json", {"exit_reason": "done", "refine_rounds": [{"round": 1}, {"round": 2},
                                         {"round": 3, "infra": {"status": "budget_exceeded"}}]})   # even its last round over budget
    runs = tmp_path / "runs"
    monkeypatch.setattr(trial, "trial_dir", lambda name, tid, lv: runs / name / tid / lv)
    monkeypatch.setattr(cli, "_trial_jobs", lambda a: [(HybridTask(tmp_path), "fine")])
    monkeypatch.setattr(refine, "make_targets", lambda t: None)
    monkeypatch.setattr(sched, "plan", lambda *a, **k: {"games": []})
    class S:
        def __init__(self, p): pass
        def slot(self, g): return contextlib.nullcontext()
    monkeypatch.setattr(sched, "Scheduler", S)
    class Seat:
        def release(self): pass
    monkeypatch.setattr(hbase, "get", lambda h: type("H", (), {"acquire": lambda self, req, name: Seat()})())
    monkeypatch.setattr(incontainer, "mounts", lambda d: [])
    monkeypatch.setattr(interrupt, "install", lambda: None)
    calls = []
    def fake_round(s, new, t, lv, agent, model, opts, minutes, seat, into, abl, critic_model=None):
        calls.append((s, new, agent, model, into, abl, minutes)); return AgentRun(exit_reason="done"), None, None
    monkeypatch.setattr(refine, "ablation_round", fake_round)
    cli.main(["refine", "--name", "dsv-opencode-s0", "--ablation", "nomem", "--minutes", "60", "--yes"])
    new = runs / "dsv-opencode-nomem-s0" / "T-HY-test" / "fine"
    assert calls == [(src, new, "opencode", "m", "dsv-opencode-nomem-s0", "nomem", 60.0)]
    # once the new run has its round, it is not selected again
    wjson(new / "agent" / "agent.json", {"exit_reason": "done", "refine_rounds": [{"round": 1}]}); calls.clear()
    cli.main(["refine", "--name", "dsv-opencode-s0", "--ablation", "nomem", "--yes"])
    assert calls == []


def test_restaging_an_earlier_full_workspace_removes_withheld_files(tmp_path, monkeypatch):
    """2026-11-19: a trial dir first staged with the full references (a launch that lost its flags) and re-run with
    --no-reference --force: work/ is not wiped between attempts, so stage removes what the setting withholds"""
    task = HybridTask(tmp_path); _targets(tmp_path, monkeypatch, task)
    d = tmp_path / "d"
    trial.stage(d, task, "fine", task.game, 60)
    assert len(list((d / "work" / "reference").iterdir())) == 3 and (d / "work" / "targets").exists()
    w, b = trial.stage(d, trial.agent_task(task, "drop-video"), "fine", task.game, 60)
    assert sorted(p.name for p in (w / "reference").iterdir()) == ["img_01.jpg", "text_01.md"] and not (w / "targets").exists()
    w, b = trial.stage(d, trial.agent_task(task, "none"), "fine", task.game, 60)
    assert list((w / "reference").iterdir()) == []


# ---------------------------------------------------------------- A2. refine ablation `full` (critic + memory; 2026-11-20)
import hashlib, multiprocessing, os
from gab.refine import memory as M

OWN = "T-HY-test"


def _entry(eid, game, status, conf, *ev, sym=None):
    """one entry in the old (Chinese field names) format of stores written before 2026-11-23: still read (gab/refine/memory.py ALIAS)"""
    return (f"### {eid}\n- date: 2026-11-13\n- game: {game}\n- role: builder\n- 现象: {sym or 'issue ' + eid}\n- 原因: cause {eid}\n"
            f"- 做法: fix {eid}\n- 证据: " + "\n  ".join(ev) + f"\n- 可信度: {conf}\n- status: {status}\n")


def _src_store(tmp_path, name="dsv-opencode-s0"):
    """a source memory like data/memory/<run>: stardew lessons whose evidence is the current task only, mixed, the seed + the
    current task, another task whose id only shares a prefix (.t1 vs .t2), and a role lesson; plus SEED.json and log.jsonl"""
    r = tmp_path / "memory" / name
    (r / "games").mkdir(parents=True); (r / "roles").mkdir()
    g = ("# stardew_valley: lessons (builder)\n\n## 开发\n"
         + _entry("stardew_valley-001", "stardew_valley", "candidate", "tried once", f"task {OWN} (fine) refine r1: own only")
         + _entry("stardew_valley-002", "stardew_valley", "confirmed", "verified repeatedly", f"task {OWN} (fine) refine r1: own A",
                  "task T-VO-other.t1 (fine) refine r2: other B", f"task {OWN} (coarse) refine r3: own C newest")
         + _entry("stardew_valley-003", "stardew_valley", "confirmed", "verified repeatedly", "seed: GameAnything games/x LESSONS.md",
                  f"task {OWN} (fine) refine r1: own folded into the seed")
         + _entry("stardew_valley-004", "stardew_valley", "candidate", "tried once", f"task {OWN}.t2 (fine) refine r1: another task, same prefix")
         + _entry("stardew_valley-005", "stardew_valley", "confirmed", "verified repeatedly", f"task {OWN} (fine) refine r1: own",
                  "task T-A.t1 (fine) refine r1: a", "task T-B.t1 (edit) refine r2: b"))
    (r / "games" / "stardew_valley.md").write_text(g)
    (r / "roles" / "builder.md").write_text("# builder: lessons (any game)\n\n"
                                            + _entry("builder-001", "any", "candidate", "tried once", f"task {OWN} (fine) refine r2: own role"))
    (r / "games" / "minecraft.md").write_text("# minecraft: lessons (builder)\n\n## 开发\n")
    wjson(r / "SEED.json", {"created": "2026-11-12T00:00:00", "files": {}}); (r / "log.jsonl").write_text('{"kind": "retrieval"}\n')
    (r / ".lock").write_text("")
    return r


def _hashes(r): return {str(f.relative_to(r)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path(r).rglob("*")) if f.is_file()}


def test_ablation_run_name_full():
    assert refine.ablation_run_name("dsv41flash-opencode-s0", "full") == "dsv41flash-opencode-full-s0"
    assert refine.ablation_run_name("sonnet55-claudecode-s0", "full") == "sonnet55-claudecode-full-s0"
    assert refine.ABLATIONS["full"] == {"feedback": True, "memory": True, "setting": "critic+memory"}


def test_cli_refine_ablation_full_flag(monkeypatch):
    got = {}
    monkeypatch.setattr(cli, "cmd_refine", lambda a: got.update(vars(a)))
    cli.main(["refine", "--name", "x-s0", "--ablation", "full", "--minutes", "60"]); assert got["ablation"] == "full"


def test_copy_store_once_idempotent_and_source_unchanged(tmp_path):
    src = _src_store(tmp_path); before = _hashes(src)
    new = tmp_path / "memory" / "dsv-opencode-full-s0"
    rec = M.copy_store(new, src)
    assert rec["source"] == str(src.resolve()) and set(rec["files"]) == {"games/stardew_valley.md", "games/minecraft.md", "roles/builder.md", "SEED.json"}
    assert all(rec["files"][k] == before[k] for k in rec["files"])          # the sha256 of the source's bytes
    assert not (new / "log.jsonl").exists()                                 # the source's log stays with it
    assert rjson(new / M.COPY_MARK) == rec
    (new / "games" / "stardew_valley.md").write_text("changed by a later extraction\n")
    assert M.copy_store(new, src) == rec                                    # once: a second call does not copy again
    assert (new / "games" / "stardew_valley.md").read_text() == "changed by a later extraction\n"
    assert _hashes(src) == before
    with pytest.raises(M.MemoryError_, match="copied from"): M.copy_store(new, tmp_path / "memory" / "other")
    with pytest.raises(M.MemoryError_, match="another directory"): M.copy_store(src, src)
    with pytest.raises(M.MemoryError_, match="no source memory"): M.copy_store(tmp_path / "memory" / "n2", tmp_path / "memory" / "missing")
    plain = tmp_path / "memory" / "plain"; (plain / "games").mkdir(parents=True); (plain / "games" / "x.md").write_text("x")
    with pytest.raises(M.MemoryError_, match="not a copy"): M.copy_store(plain, src)


def _copy_worker(args):
    new, src = args
    return json.dumps(M.copy_store(Path(new), Path(src)), sort_keys=True)


def test_copy_store_parallel_trials_copy_once(tmp_path):
    src = _src_store(tmp_path); before = _hashes(src)
    new = tmp_path / "memory" / "dsv-opencode-full-s0"
    with multiprocessing.get_context("fork").Pool(6) as p: recs = p.map(_copy_worker, [(str(new), str(src))] * 12)
    assert len(set(recs)) == 1                                              # one copy, every trial got its record
    assert _hashes(src) == before
    assert {k: v for k, v in _hashes(new).items() if k not in (".lock", M.COPY_MARK)} == {k: before[k] for k in json.loads(recs[0])["files"]}


def test_exclude_task_drops_and_strips_the_current_task_exactly(tmp_path):
    src = _src_store(tmp_path)
    es, st = M.exclude_task(M.collect(src, "stardew_valley"), OWN)
    by = {e["id"]: e for e in es}
    assert st["excluded"] == 2 and sorted(st["excluded_ids"]) == ["builder-001", "stardew_valley-001"]
    assert st["stripped_lines"] == 4 and sorted(st["stripped_ids"]) == ["stardew_valley-002", "stardew_valley-003", "stardew_valley-005"]
    assert by["stardew_valley-002"]["evidence"] == "task T-VO-other.t1 (fine) refine r2: other B"      # both levels of the task stripped
    assert by["stardew_valley-002"]["status"] == "candidate" and by["stardew_valley-002"]["confidence"] == "tried once"  # one source left
    assert by["stardew_valley-003"]["evidence"].startswith("seed:") and by["stardew_valley-003"]["status"] == "candidate"
    assert by["stardew_valley-005"]["status"] == "confirmed" and OWN + " " not in by["stardew_valley-005"]["evidence"]  # two others left
    assert by["stardew_valley-004"]["evidence"].startswith(f"task {OWN}.t2 ")  # another task (same prefix) is not the current one
    assert st["demoted"] == 2
    raw = {e["id"]: e for e in M.collect(src, "stardew_valley")}            # the stored entries are not changed
    assert OWN + " (coarse)" in raw["stardew_valley-002"]["evidence"] and raw["stardew_valley-002"]["status"] == "confirmed"


def test_retrieve_with_exclusion_shows_no_line_of_the_current_task(tmp_path):
    src = _src_store(tmp_path); st = {}
    text, ids, _ = M.retrieve(src, "stardew_valley", "festival", exclude=OWN, stats=st)
    assert f"task {OWN} " not in text and f"task {OWN}.t2" in text
    assert not any(x in text for x in ("own only", "own A", "own C", "own role", "own folded"))
    assert "other B" in text                                               # the newest line left is what the agent sees
    assert set(ids) == {"stardew_valley-002", "stardew_valley-003", "stardew_valley-004", "stardew_valley-005"} and st["excluded"] == 2
    text0, ids0, _ = M.retrieve(src, "stardew_valley", "festival")         # without exclude: as before
    assert "own C newest" in text0 and "stardew_valley-001" in ids0 and "builder-001" in ids0


def test_full_ablation_round_uses_a_frozen_copy_without_the_own_task(tmp_path, monkeypatch):
    task, ag, crit = _env(tmp_path, monkeypatch)
    msrc = _src_store(tmp_path); mbefore = _hashes(msrc)
    calls = []
    def chat(prompt, model):
        calls.append(prompt)
        return json.dumps({"lessons": [{"scope": "game", "symptom": "lanterns not lit at night in the replay", "cause": "no light source",
                                        "fix": "add a light block under each lantern", "evidence": "after-review shows lit lanterns"}],
                           "applied": []}), {"prompt_tokens": 10, "completion_tokens": 5}
    monkeypatch.setattr(M, "_chat", chat)
    src = _source(tmp_path); before = tree(src)
    name = refine.ablation_run_name("dsv-opencode-s0", "full"); new = tmp_path / "runs" / name / task.id / "fine"
    run, sb, sa = refine.ablation_round(src, new, task, "fine", "opencode", "m", {"until_done": False}, 60, None, name, "full")
    assert tree(src) == before and _hashes(msrc) == mbefore                 # the source trial and the source memory are only read
    msg = ag.prompts[0]
    assert "lessons.md" in msg and "review/feedback.md" in msg and crit == ["gate", "r1"]
    lessons = (new / "work" / "lessons.md").read_text()
    assert f"task {OWN} " not in lessons and "other B" in lessons and "stardew_valley-001" not in lessons and "builder-001" not in lessons
    mr = rjson(new / "agent" / "memory_r1.json")
    rr = mr["retrieval"]
    assert rr["memory"] == str(tmp_path / "memory" / name) and rr["copied_from"] == str(msrc.resolve())
    assert rr["own_task_excluded"]["entries_excluded"] == 2 and rr["own_task_excluded"]["evidence_lines_stripped"] == 4
    assert rr["chars"] == len(lessons)
    assert len(calls) == 1 and mr["extraction"]["added"] == ["stardew_valley-006"]
    mnew = tmp_path / "memory" / name
    assert "lanterns not lit" in M.game_file(mnew, "stardew_valley").read_text()    # written into the copy ...
    assert "lanterns not lit" not in M.game_file(msrc, "stardew_valley").read_text()  # ... never into the source
    assert rjson(mnew / M.COPY_MARK)["files"]["games/stardew_valley.md"] == mbefore["games/stardew_valley.md"]
    agj = rjson(new / "agent" / "agent.json")["refine_rounds"]
    assert agj[0]["setting"] == "critic+memory" and agj[0]["memory"]["retrieval"]["own_task_excluded"]["entries_excluded"] == 2
    assert rjson(new / "trial.json")["refine_ablation"]["setting"] == "full"


def test_full_ablation_without_a_source_memory_runs_without_lessons(tmp_path, monkeypatch):
    task, ag, crit = _env(tmp_path, monkeypatch)
    src = _source(tmp_path)
    name = refine.ablation_run_name("dsv-opencode-s0", "full"); new = tmp_path / "runs" / name / task.id / "fine"
    refine.ablation_round(src, new, task, "fine", "opencode", "m", {"until_done": False}, 60, None, name, "full")
    mr = rjson(new / "agent" / "memory_r1.json")
    assert "no source memory" in mr["retrieval"]["error"] and "no source memory" in mr["extraction"]["error"]
    assert not (tmp_path / "memory" / name / "games").exists()              # no seeded store that is not a copy


def test_normal_refine_with_memory_is_unchanged(tmp_path, monkeypatch):
    """a normal gab refine round (no refine_ablation in trial.json): its own run's memory, nothing excluded, no copy"""
    task, ag, crit = _env(tmp_path, monkeypatch)
    monkeypatch.setattr(M, "_chat", lambda p, m: ('{"lessons": [], "applied": []}', {}))
    src = _source(tmp_path); msrc = _src_store(tmp_path)
    shutil.rmtree(src / "attempts"); shutil.rmtree(src / "deliverable"); shutil.copytree(src / "work" / "deliverable", src / "deliverable")
    wjson(src / "agent" / "agent.json", {"exit_reason": "done"}); (src / "work" / "deliverable" / "mod" / "m.txt").write_text("base")
    assert M.full_ablation(src) is None
    refine.refine_trial(src, task, "fine", "opencode", "m", {"until_done": False}, 1, seat=None, memory=True)
    rr = rjson(src / "agent" / "memory_r1.json")["retrieval"]
    assert rr["memory"] == str(msrc) and "copied_from" not in rr and "own_task_excluded" not in rr
    assert "stardew_valley-001" in rr["ids"] and "own C newest" in (src / "work" / "lessons.md").read_text()
    assert not (msrc / M.COPY_MARK).exists()
    assert rjson(src / "agent" / "agent.json")["refine_rounds"][0]["setting"] == "critic+memory"

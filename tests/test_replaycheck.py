"""replay summaries and the refine regression check (gab/refine/replaycheck.py, `game replay`, UntilDone on_done, gab/refine/refine.py
regression_hook / fix_round): fakes only, no game, no agent"""
import hashlib, io, json
from pathlib import Path

import yaml

from gab.run import control
from gab.refine import refine, replaycheck
from gab.agent.base import AgentRun, Budget
from gab.agent.until_done import UntilDone
from gab.game import base as gbase


def st(map_, tile=(1, 1), screen="WorldState", text=(), t=0.0, extra=None):
    return {"running": True, "engine": {"map": map_, "tile": {"x": tile[0], "y": tile[1]}, "screen": screen, "uptime_s": t, **(extra or {})},
            "mod": {}, "screen_text": list(text)}


def png(p, colour):
    from PIL import Image
    Image.new("RGB", (64, 36), colour).save(p)


def make_replay(out, points, n_steps=None):
    """points: [(step, state, shot name, colour)] -> a replay dir like GameAdapter.replay writes"""
    out = Path(out); (out / "state").mkdir(parents=True, exist_ok=True)
    start = st("start.tmx", text=["title"]); (out / "state" / "start.json").write_text(json.dumps(start))
    prev = start; tl = []
    for step, s, name, colour in points:
        png(out / name, colour); sf = out / "state" / (Path(name).stem + ".json"); sf.write_text(json.dumps(s))
        tl.append({"step": step, "actions": [], "screenshot": name, "diff": gbase.state_diff(prev, s), "snapshot": f"state/{sf.name}"})
        prev = s
    (out / "replay.json").write_text(json.dumps({"log": [{"i": i} for i in range(n_steps or points[-1][0])], "state": prev, "timeline": tl}))
    return out


def base_points():
    return [(10, st("hall.tmx", (2, 2), text=["Welcome"], t=1), "010_hall.png", "red"),
            (40, st("pool.tmx", (3, 3), text=["Pool"], t=2), "040_pool.png", "blue"),
            (80, st("court.tmx", (7, 3), "ChoiceState", text=["Queen", "Off with"], t=3), "080_court.png", "green"),
            (100, st("court.tmx", (7, 3), "ChoiceState", text=["Queen", "Off with"], t=4), "zz_end.png", "green")]


def r1_points():                      # rebuilt map: the trace walks into a wall in pool.tmx and never reaches the court
    return [(10, st("hall.tmx", (2, 2), text=["Welcome"], t=1), "010_hall.png", "red"),
            (40, st("pool.tmx", (3, 3), text=["Pool"], t=2), "040_pool.png", "blue"),
            (41, st("pool.tmx", (4, 3), text=["Pool"], t=3), "041_auto.png", "blue"),
            (80, st("pool.tmx", (4, 3), text=["Pool"], t=9), "080_court.png", "blue"),
            (100, st("pool.tmx", (4, 3), text=["Pool"], t=12), "zz_end.png", "blue")]


def record(rd, traces):
    (Path(rd)).mkdir(parents=True, exist_ok=True)
    (Path(rd) / "record.json").write_text(json.dumps({"build": True, "traces": [{"trace": t, "ok": True} for t in traces]}))


# ---- 1. summaries and the comparison
def test_summary_final_state_last_change_and_shots(tmp_path):
    s = replaycheck.summarize_dir(make_replay(tmp_path / "b", base_points()), {"steps": [{}] * 100, "claims": ["court"]}, "03.yaml")
    assert s["steps"] == 100 and s["last_change"] == 80 and s["final"]["place"] == {"map": "court.tmx"}
    assert s["final"]["pos"] == "(7,3)" and s["final"]["screen"] == "ChoiceState"
    assert [p["map"] for p in s["places"]] == ["start.tmx", "hall.tmx", "pool.tmx", "court.tmx"]
    assert [x["shot"] for x in s["shots"]] == ["010_hall.png", "040_pool.png", "080_court.png"]   # auto / end shots left out
    assert all(x["state_changed"] for x in s["shots"]) and s["shots"][1]["screen_changed"] is True
    r = replaycheck.summarize_dir(make_replay(tmp_path / "c", r1_points()), None, "03.yaml")
    assert r["last_change"] == 41                             # the uptime clock alone is no state change
    sh = {x["shot"]: x for x in r["shots"]}
    assert sh["080_court.png"]["screen_changed"] is False and sh["080_court.png"]["state_changed"] is False
    txt = replaycheck.describe(r)
    assert "last state change at step 41" in txt and "ends in map pool.tmx at (4,3)" in txt and "080_court.png" in txt
    assert "screenshot same as the previous one" in txt


def test_compare_flags_the_regression_and_not_an_identical_trace(tmp_path):
    b = replaycheck.summarize_dir(make_replay(tmp_path / "b", base_points()), name="03.yaml")
    c = replaycheck.summarize_dir(make_replay(tmp_path / "c", r1_points()), name="03.yaml")
    regs = replaycheck.compare(b, c)
    assert any("never reaches map court.tmx" in x for x in regs) and any("nothing changes after step 41" in x for x in regs)
    assert replaycheck.compare(b, b) == [] and replaycheck.compare(c, b) == []      # reaching more is never a regression


def test_compare_dirs_and_message_from_old_records(tmp_path):
    """record.json written before summaries existed: derived from the replay files and added to the record"""
    B, C = tmp_path / "r0", tmp_path / "r1"
    record(B, ["01.yaml", "03.yaml"]); record(C, ["01.yaml", "03.yaml"])
    for rd, pts in ((B, base_points()), (C, r1_points())):
        make_replay(rd / "01", base_points()[:2]); make_replay(rd / "03", pts)
    rep = replaycheck.compare_dirs(B, C)
    assert rep["regressed"] == ["03.yaml"]
    assert "summary" in json.loads((B / "record.json").read_text())["traces"][1]
    lines = replaycheck.regressed_lines(rep)
    assert len(lines) == 1 and lines[0].startswith("- trace 03.yaml now stops at step 41 of 100 in map pool.tmx at (4,3)")
    assert "on your earlier hand-in it reached map court.tmx" in lines[0]
    m = replaycheck.message(rep, 5)
    assert "about 5 minutes" in m and "game replay" in m and "03.yaml" in m
    assert replaycheck.message(replaycheck.compare_dirs(B, B), 5) is None
    assert replaycheck.compare_dirs(B, C, cur_build=False)["build_failed"]


def test_renamed_trace_is_fine_when_another_reaches_its_end(tmp_path):
    B, C = tmp_path / "r0", tmp_path / "r1"
    record(B, ["03.yaml"]); make_replay(B / "03", base_points())
    record(C, ["03_new.yaml"]); make_replay(C / "03_new", base_points())
    assert replaycheck.compare_dirs(B, C)["regressed"] == []
    make_replay(C / "03_new", r1_points())
    (C / "record.json").write_text(json.dumps({"build": True, "traces": [{"trace": "03_new.yaml", "ok": True}]}))
    assert replaycheck.compare_dirs(B, C)["regressed"] == ["03.yaml"]


# ---- 2. `game replay` answers its summary after the old fields
class ReplayAd:
    screen = (800, 600); extra_help = ""
    class seat:
        @staticmethod
        def copy_out(a, b): return True
    def attach(self, inst, mod): pass
    def replay(self, inst, trace, out):
        make_replay(out, r1_points())
        return {"ok": True, "log": [{"i": 0}, "start note"], "shots": [str(Path(out) / "010_hall.png")], "state": r1_points()[-1][1]}


# ---- 3. UntilDone: one check when DONE is accepted
class Agent:
    can_resume = True
    def __init__(self, n_done=2):
        self.model, self.opts, self.calls = "m", {}, []
    def session_of(self, p): return "S1"
    def version(self): return "v"
    def run(self, workdir, instruction, budget, tools):
        self.calls.append((instruction, budget.minutes, dict(self.opts)))
        (workdir / "deliverable" / "DONE").write_text("")
        tr = workdir.parent / "transcript.jsonl"; tr.write_text("{}\n")
        return AgentRun(transcript=str(tr), exit_reason="timeout" if len(self.calls) == 2 else "done", extra={"tool_calls": 3})


# ---- 4. the refine round's hook
class FakeTask:
    id, game = "T-IO-test", "tuxemon"
    def instruction(self, level): return "Make Alice."
    def references(self, kind=None): return []
    def clips(self): return []


class StopAd:
    def __init__(self): self.stopped = []
    def stop(self, inst): self.stopped.append(inst)


# ---- 5.
def tree_hash(p):
    h = hashlib.sha1()
    for f in sorted(Path(p).rglob("*")):
        if f.is_file(): h.update(str(f.relative_to(p)).encode()); h.update(f.read_bytes())
    return h.hexdigest()


def make_source(tmp_path):
    src = tmp_path / "runs" / "dsv-opencode-s0" / "T-IO-test" / "fine"
    def dv(p, tag):
        (p / "mod").mkdir(parents=True); (p / "mod" / "m.txt").write_text(tag)
        (p / "traces").mkdir(); (p / "traces" / "03.yaml").write_text(yaml.safe_dump({"steps": [{"do": "wait", "seconds": 1}] * 100}))
        (p / "DONE").write_text("")
    for n, k in (("1", None), ("2", 0), ("3", 1), ("4", 2)):        # 1: an interrupted attempt; 2 base; 3 R1; 4 R2
        a = src / "attempts" / n
        (a / "agent").mkdir(parents=True)
        (a / "agent" / "agent.json").write_text(json.dumps({"exit_reason": "interrupted" if k is None else "done",
                                                             "refine_rounds": [{"round": i + 1} for i in range(k or 0)]}))
        dv(a / "deliverable", f"attempt{n}")
    (src / "agent").mkdir(); (src / "agent" / "agent.json").write_text(json.dumps({"exit_reason": "done", "refine_rounds": [{}, {}, {}]}))
    dv(src / "deliverable", "r3"); dv(src / "work" / "deliverable", "r3")
    (src / "trial.json").write_text(json.dumps({"task": "T-IO-test", "level": "fine", "agent": "opencode", "model": "deepseek-v4.1-flash",
                                                "name": "dsv-opencode-s0"}))
    for r, pts in (("r0", base_points()), ("r1", r1_points())):
        rd = src / "refine" / r / "replay"; record(rd, ["03.yaml"]); make_replay(rd / "03", pts); (rd / "03.mp4").write_bytes(b"v")
    return src


def test_snapshot_and_run_name(tmp_path):
    src = make_source(tmp_path)
    assert refine.snapshot(src, 0).parent.name == "2" and refine.snapshot(src, 1).parent.name == "3"
    assert refine.snapshot(src, 3) == src / "deliverable" and refine.snapshot(src, 4) is None


class StageTask(FakeTask):
    def __init__(self, root):
        self.dir = root / "task"; self.dir.mkdir(parents=True, exist_ok=True); (self.dir / "instruction_fine.md").write_text("Make Alice.")
        self.mdir = root; self.meta = {"task_id": self.id, "target_game": self.game, "modality": "text", "references": []}



"""Host fidelity (gab.eval.host_fidelity): evidence copy, rules after the judge, gate-failed skip -- no LLM"""
import json
from pathlib import Path

from gab.eval import host_fidelity as hf

IDS = [f"TR-ID{i}" for i in range(1, 6)]


def material(tmp):
    m = tmp / "_host_fidelity"; (m / "vanilla").mkdir(parents=True)
    feats = [{"id": i, "trait": f"trait {i}", "evidence": f"evidence {i}"} for i in IDS]
    (m / "checklist.json").write_text(json.dumps({"version": "t", "games": {"terraria": {"title": "Terraria", "gate": "-", "features": feats, "notes": ["n"]}}}))
    (m / "anchor.md").write_text("ANCHOR TEXT"); (m / "vanilla" / "terraria.png").write_bytes(b"v")
    return m


def trial(tmp, gate=True):
    d = tmp / "runs" / "runA" / "T-X.t1" / "fine"; fn = d / "eval" / "functions"
    rp = fn / "replay" / "01_a"; (rp / "state").mkdir(parents=True)
    shots = []
    for k in (1, 2):
        s = rp / f"00{k}_s.png"; s.write_bytes(b"x"); (rp / "state" / f"00{k}_s.json").write_text("{}"); shots.append(str(s))
    (rp / "replay.json").write_text("{}")
    (fn / "functions.json").write_text(json.dumps({"replays": [{"trace": "01_a.yaml", "ok": True, "claims": ["c"], "shots": shots,
                                                                 "timeline": [{"step": 1, "screenshot": "001_s.png", "diff": {}}]}]}))
    pl = fn / "play"; (pl / "shots").mkdir(parents=True); acts = []
    for n in (1, 2, 3):
        s = pl / "shots" / f"a00{n}.png"; s.write_bytes(b"x"); s.with_suffix(".state.json").write_text("{}")
        acts.append({"n": n, "cmd": "key", "args": ["a"], "shot": str(s), "state_diff": {}})
    (pl / "actions.jsonl").write_text("\n".join(json.dumps(a) for a in acts) + "\n")
    (pl / "verdict.json").write_text("{}"); (pl / "ref").mkdir(); (pl / "ref" / "frame.png").write_bytes(b"r")
    (fn / "arbiter.json").write_text("{}")
    d.joinpath("result.json").write_text(json.dumps({"status": "completed", "gate": {"pass": gate, "fail": None if gate else "TR-MOVE"},
                                                     "scores": {"overall": 0.4}, "items": []}))
    d.joinpath("trial.json").write_text(json.dumps({"model": "m1", "agent": "a1"}))
    return d


def verdict(used, score, reason="r"):
    return json.dumps({"features": [{"id": i, "used": u, "evidence": ["e"], "reason": "x"} for i, u in zip(IDS, used)], "score": score, "reason": reason})


def fake(answer, seen=None):
    def call(p, out, name):
        if seen is not None: seen.append(p)
        return answer, {"wall_s": 1, "usd": 0.01, "tokens_in": 10, "tokens_out": 5, "tokens_cached": 100}
    return call


def test_copy_evidence(tmp_path):
    d = trial(tmp_path); out = tmp_path / "out"
    E = hf.copy_evidence(d / "eval", out)
    assert E["counts"] == {"replay_traces": 1, "replay_shots": 2, "replay_states": 2, "play_actions": 3, "play_shots": 3}
    assert sorted(p.name for p in (out / "replay" / "01_a").iterdir()) == ["001_s.png", "002_s.png", "state"]
    assert all(a["shot"].startswith(str(out)) for a in E["play"]) and all(s.startswith(str(out)) for s in E["replays"][0]["shots"])
    names = {p.name for p in out.rglob("*")}
    assert not names & {"verdict.json", "frame.png", "arbiter.json", "ref", "replay.json"}


def test_scored_and_files(tmp_path):
    m = material(tmp_path); d = trial(tmp_path); seen = []
    r = hf.run_trial(d, "T-X.t1", "terraria", mdir=m, call=fake(verdict(["yes"] * 5, 1), seen))
    assert r["status"] == "scored" and r["score"] == 1.0 and r["overall"] == 0.4 and r["model"] == "m1"
    assert r["cost"]["tokens_cached"] == 100 and r["evidence"]["vanilla"] and r["trial"] == "runA/T-X.t1"
    assert "ANCHOR TEXT" in seen[0] and "TR-ID5: trait TR-ID5" in seen[0] and "vanilla_terraria.png" in seen[0] and "a002" in seen[0]
    assert (d / "eval@host" / "vanilla_terraria.png").exists()
    assert json.loads((d / "host_fidelity.json").read_text())["score"] == 1.0
    assert json.loads((d / "result.json").read_text()) == {"status": "completed", "gate": {"pass": True, "fail": None}, "scores": {"overall": 0.4}, "items": []}
    assert hf.run_trial(d, "T-X.t1", "terraria", mdir=m, call=fake("boom"))["score"] == 1.0     # cached without --force


def test_insufficient(tmp_path):
    m = material(tmp_path); d = trial(tmp_path)
    r = hf.run_trial(d, "T-X.t1", "terraria", mdir=m, call=fake(verdict(["yes", "unknown", "unknown", "unknown", "unknown"], 1)))
    assert r["status"] == "insufficient" and r["score"] is None and r["judge_score"] == 1.0 and r["flags"]
    r = hf.run_trial(d, "T-X.t1", "terraria", mdir=m, force=True,
                     call=fake(verdict(["unknown"] * 5, "insufficient evidence")))
    assert r["status"] == "insufficient" and r["score"] is None and not r["flags"]


def test_rules():
    f = lambda used, s: hf.finalize(json.loads(verdict(used, s)), IDS)[1:]
    assert f(["yes", "yes", "yes", "yes", "yes"], 1) == (1.0, [])
    assert f(["yes", "yes", "not_applicable", "partial", "unknown"], 1) == (1.0, [])          # 1 partial of 3: not a majority
    assert f(["partial", "partial", "yes", "not_applicable", "yes"], 1)[0] == 1.0            # 2 partial of 4: not more than half
    assert f(["partial", "partial", "partial", "yes", "not_applicable"], 0.5) == (0.5, [])    # 3 partial of 4
    assert f(["yes", "yes", "no", "yes", "yes"], 0.5) == (0.5, [])                           # any no
    assert f(["no", "yes", "yes", "yes", "yes"], 0) == (0.0, [])                             # ID1 no
    assert f(["yes", "no", "yes", "yes", "yes"], 0) == (0.0, [])                             # ID2 no
    assert f(["partial", "partial", "no", "not_applicable", "no"], 0) == (0.0, [])           # no yes at all (tuxemon fa2bd630)
    s, fl = f(["yes", "yes", "yes", "yes", "partial"], 0.5)
    assert s == 1.0 and fl == ["rule_vs_judge_disagree: rule 1.0, judge 0.5"]


def test_apply_rule_rescores_saved_record():
    r = {"score": 0.5, "judge_score": 1.0, "status": "scored", "features": [{"id": i, "used": u} for i, u in zip(IDS, ["yes"] * 4 + ["partial"])]}
    n = hf.apply_rule(r)
    assert n["score"] == 1.0 and n["rule_vs_judge_disagree"] is False and n["judge_score"] == 1.0 and "rule" in n
    assert hf.apply_rule({"status": "gate_failed", "score": None}) == {"status": "gate_failed", "score": None}


def test_not_applicable_is_not_judgeable():
    f = lambda used, s: hf.finalize(json.loads(verdict(used, s)), IDS)[1]
    assert f(["yes", "not_applicable", "not_applicable", "unknown", "unknown"], 1) == "insufficient"
    assert f(["unknown", "yes", "not_applicable", "not_applicable", "unknown"], "insufficient") == "insufficient"
    assert f(["yes", "yes", "not_applicable", "not_applicable", "unknown"], 1) == 1.0


def test_instruction_and_modality_in_prompt(tmp_path):
    m = material(tmp_path); d = trial(tmp_path); seen = []
    (d / "work" / "task").mkdir(parents=True); (d / "work" / "task" / "instruction_fine.md").write_text("BUILD A CHASE")
    r = hf.run_trial(d, "T-X.t1", "terraria", mdir=m, call=fake(verdict(["yes"] * 5, 1), seen), modality="video")
    assert "BUILD A CHASE" in seen[0] and "reference modality: video" in seen[0]
    assert (d / "eval@host" / "instruction_fine.md").read_text() == "BUILD A CHASE"
    assert r["evidence"]["instruction"] and r["evidence"]["modality"] == "video"


def test_gate_failed_skips_judge(tmp_path):
    m = material(tmp_path); d = trial(tmp_path, gate=False)
    def boom(*a): raise AssertionError("judge called")
    r = hf.run_trial(d, "T-X.t1", "terraria", mdir=m, call=boom)
    assert r["status"] == "gate_failed" and r["score"] is None and "TR-MOVE" in r["reason"]
    assert not (d / "eval@host").exists()


def test_bad_answer_retried_then_failed(tmp_path):
    m = material(tmp_path); d = trial(tmp_path); n = []
    def call(p, out, name): n.append(name); return verdict(["yes"] * 4 + ["maybe"], 1), {"wall_s": 1}
    r = hf.run_trial(d, "T-X.t1", "terraria", mdir=m, call=call)
    assert r["status"] == "judge_failed" and r["score"] is None and n == ["host_judge", "host_judge.try2"]


def test_real_checklist_shape():
    p = hf.material_dir() / "checklist.json"
    cl = json.loads(p.read_text())["games"]
    assert len(cl) == 14 and all(len(g["features"]) == 5 for g in cl.values())


def test_tagged_evaluation(tmp_path):
    """--tag T: the evidence of result@T.json + eval@T/ -> host_fidelity@T.json; the main record and other tags untouched"""
    import shutil
    m = material(tmp_path); d = trial(tmp_path)
    shutil.move(str(d / "eval"), str(d / "eval@lite-base")); shutil.move(str(d / "result.json"), str(d / "result@lite-base.json"))
    (d / "host_fidelity.json").write_text(json.dumps({"keep": 1}))
    seen = []
    r = hf.run_trial(d, "T-X.t1", "terraria", mdir=m, call=fake(verdict(["yes"] * 5, 1), seen), tag="lite-base")
    assert r["status"] == "scored" and r["score"] == 1.0 and r["eval_tag"] == "lite-base" and r["evidence"]["replay_shots"] == 2
    assert json.loads((d / "host_fidelity@lite-base.json").read_text())["score"] == 1.0
    assert json.loads((d / "host_fidelity.json").read_text()) == {"keep": 1}
    assert (d / "eval@host-lite-base" / "replay" / "01_a" / "001_s.png").exists() and not (d / "eval@host").exists()
    r2 = hf.run_trial(d, "T-X.t1", "terraria", mdir=m, call=fake(verdict(["yes"] * 5, 1)), tag="lite-iter1")   # no such evaluation
    assert r2["status"] == "no_result" and "result@lite-iter1.json" in r2["reason"]

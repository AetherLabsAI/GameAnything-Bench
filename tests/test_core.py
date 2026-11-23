"""Unit tests without games (rubric v4): task loading / validation of video, text, image and hybrid packages, evidence
resolution, scoring, aggregation, trace + hand-in checks, JPEG metadata stripping."""
import json
from pathlib import Path

import pytest
import yaml

from gab.eval import scoring
from gab.run import task as T
from gab.game.base import check_trace
from gab.report import aggregate
from gab.run.trial import check_deliverable
from gab.util import sha256, ts


def anchors(): return {1: "full", 0.5: "half", 0: "none"}


def _meta(tid, game, modality, refs):
    return {"task_id": tid, "version": "0.2", "category": "scene_world", "modality": modality, "domain": "travel", "track": "core",
            "target_game": game, "references": refs, "provenance": {"pipeline": "x", "rules_version": "v4", "review_status": "pending"}}


def make_task(root, tid="T-VO-vid_000000000000.t1", game="flare", kinds=("video",)):
    """a v4 package with the given reference kinds; CR / FP / VP items cite every kind the task has"""
    pub, hid, med = root / "tasks" / tid, root / "hidden" / tid, root / "media" / tid
    for d in (pub, hid, med / "keyframes", med / "judge"): d.mkdir(parents=True, exist_ok=True)
    refs, ev = [], []
    if "video" in kinds:
        (med / "clip_01.mp4").write_bytes(b"fake clip"); (med / "keyframes" / "t00m02.0s.jpg").write_bytes(b"jpg")   # already cut
        refs.append({"id": "c01", "kind": "video", "file": "clip_01.mp4", "video_id": "vid_0", "source": {"title": "t", "url": None},
                     "range": {"start": 0.0, "end": 9.0}, "hf_path": "hf://x/clip_01.mp4", "sha256": sha256(med / "clip_01.mp4")})
        ev.append(["00:01", "00:03"])
    if "text" in kinds:
        (med / "text_01.md").write_text("The river bends.\n\nA bridge crosses it.\n")
        (med / "judge" / "text_01.anchored.md").write_text("[p001] The river bends.\n\n[p002] A bridge crosses it.\n")
        refs.append({"id": "t01", "kind": "text", "file": "text_01.md", "hf_path": "hf://x/text_01.md", "sha256": sha256(med / "text_01.md"),
                     "judge_file": "judge/text_01.anchored.md", "judge_sha256": sha256(med / "judge" / "text_01.anchored.md")})
        ev.append(["p001", "p002"])
    if "image" in kinds:
        (med / "img01.jpg").write_bytes(b"\xff\xd8jpg")
        refs.append({"id": "img01", "kind": "image", "file": "img01.jpg", "hf_path": "hf://x/img01.jpg", "sha256": sha256(med / "img01.jpg")})
        ev.append("img01: the bridge")
    mod = "+".join(k for k in ("video", "text", "image") if k in kinds)
    yaml.safe_dump(_meta(tid, game, mod, [r["id"] for r in refs]), open(pub / "task.yaml", "w"))
    yaml.safe_dump({"task_id": tid, "refs": refs}, open(pub / "references.yaml", "w"))
    (pub / "instruction_fine.md").write_text("Build it. Specifically, I want: the river should bend.\n")
    cross = {"cross": True} if len(kinds) > 1 else {}
    CR = [{"id": f"CR{i}", "item": "x", "anchors": anchors(), "evidence": ev, **(cross if i == 1 else {})} for i in range(1, 5)]
    F = [{"id": "FP1", "item": "x", "anchors": anchors(), "evidence": ev}, {"id": "FP2", "item": "x", "anchors": anchors()}]
    V = [{"id": f"VP{i}", "item": "x", "anchors": anchors(), "evidence": ev, **({"keyframes": ["00:02.0"]} if "video" in kinds else {})}
         for i in range(1, 4)]
    yaml.safe_dump({"task_id": tid, "instructions_meta": {"requirement_groups": {"river": ["CR1", "FP1"]}, "explicit_requirements": [
        {"type": "specify", "text": "The river should bend.", "covers": ["CR2"]}, {"type": "modify", "text": "make it mine", "covers": ["FP2"]}]},
        "rubric": {"content_realization": CR, "functions_playability": F, "visual_presentation": V}}, open(hid / "rubric.yaml", "w"))
    return tid, dict(tasks_dir=root / "tasks", hidden_dir=root / "hidden", media_dir=root / "media")


def test_load_and_validate(tmp_path):
    tid, kw = make_task(tmp_path)
    t = T.Task(tid, **kw)
    assert t.game == "flare" and t.category == "scene_world" and t.modality == "video"
    assert t.items("content_realization")[0]["anchors"] == {"1": "full", "0.5": "half", "0": "none"}
    assert [p.name for p in t.keyframe_files(t.items("visual_presentation")[0])] == ["t00m02.0s.jpg"]
    E, W = T.validate(tid, **kw)
    assert E == [], E
    assert any("verbatim" in w for w in W)                               # "make it mine" is not in the fine instruction


@pytest.mark.parametrize("tid,kinds", [("T-TO-txt_000000000000.e1", ("text",)), ("T-IO-0000000000", ("image",)),
                                       ("T-HY-vid_000000000000.t1", ("video", "text", "image"))])
def test_other_kinds_validate(tmp_path, tid, kinds):
    tid, kw = make_task(tmp_path, tid, kinds=kinds)
    E, W = T.validate(tid, **kw)
    assert E == [], E
    t = T.Task(tid, **kw)
    assert [r["kind"] for r in t.references()] == list(k for k in ("video", "text", "image") if k in kinds)


def test_evidence_resolves_to_reference(tmp_path):
    """text anchors -> the quoted paragraphs of the judge copy; image numbers -> the image file; times -> a clip frame"""
    from gab.eval.evidence import resolve
    tid, kw = make_task(tmp_path, "T-HY-vid_000000000000.t1", kinds=("video", "text", "image"))
    t = T.Task(tid, **kw)
    assert t.anchored_text() == {"p001": "The river bends.", "p002": "A bridge crosses it."}
    txt, files = resolve(t, t.items("content_realization")[0], tmp_path / "out")
    assert "[p001] The river bends." in txt and "[p002] A bridge crosses it." in txt
    assert "ref/img01.jpg" in files and (tmp_path / "out" / "ref" / "img01.jpg").exists() and "(the bridge)" in txt


def test_rubric_falls_back_to_published(tmp_path, monkeypatch):
    """no hidden copy -> rubrics/<id> (tasks on open-source games are published)"""
    tid, kw = make_task(tmp_path)
    (kw["hidden_dir"]).rename(tmp_path / "rubrics")
    monkeypatch.setenv("GAB_HIDDEN_DIR", str(kw["hidden_dir"])); monkeypatch.setenv("GAB_RUBRICS_DIR", str(tmp_path / "rubrics"))
    t = T.Task(tid, tasks_dir=kw["tasks_dir"], media_dir=kw["media_dir"])
    assert t.hdir == tmp_path / "rubrics" / tid and len(t.items("content_realization")) == 4


def test_validate_catches_problems(tmp_path):
    tid, kw = make_task(tmp_path)
    (kw["media_dir"] / tid / "clip_01.mp4").write_bytes(b"changed")
    (kw["tasks_dir"] / tid / "rubric.yaml").write_text("leak")
    (kw["tasks_dir"] / tid / "instruction_coarse.md").write_text("old level")
    r = yaml.safe_load(open(kw["hidden_dir"] / tid / "rubric.yaml"))
    r["instructions_meta"]["explicit_requirements"][0]["covers"] = ["CR9"]
    r["rubric"]["visual_presentation"][0]["keyframes"] = ["00:30.0"]
    r["rubric"]["content_realization"][1]["evidence"] = ["p004", "img02"]       # a video-only task has no text or images
    r["rubric"]["content_realization"][2]["evidence"] = ["01:30"]                # outside the clip
    yaml.safe_dump(r, open(kw["hidden_dir"] / tid / "rubric.yaml", "w"))
    E, _ = T.validate(tid, **kw)
    msg = "\n".join(E)
    for x in ("sha256 mismatch", "public package", "instruction_coarse.md", "CR9", "keyframes outside the clips",
              "no text reference", "no image reference", "outside the clips"):
        assert x in msg, (x, msg)


def test_validate_text_anchor_and_cross(tmp_path):
    tid, kw = make_task(tmp_path, "T-HY-vid_000000000000.t1", kinds=("video", "text"))
    r = yaml.safe_load(open(kw["hidden_dir"] / tid / "rubric.yaml"))
    r["rubric"]["content_realization"][0]["evidence"] = ["p009"]                 # cross item: one kind only, unknown anchor
    yaml.safe_dump(r, open(kw["hidden_dir"] / tid / "rubric.yaml", "w"))
    E, _ = T.validate(tid, **kw); msg = "\n".join(E)
    assert "p009 is not a paragraph anchor" in msg and "at least two kinds" in msg


def test_schema_rejects_bad_task(tmp_path):
    tid, kw = make_task(tmp_path)
    d = yaml.safe_load(open(kw["tasks_dir"] / tid / "task.yaml")); d["category"] = "other"; d["extra"] = 1
    yaml.safe_dump(d, open(kw["tasks_dir"] / tid / "task.yaml", "w"))
    E, _ = T.validate(tid, **kw)
    assert any("task.yaml" in e for e in E)


def test_schema_rejects_v3_rubric(tmp_path):
    """an Understanding section or level masks are not v4"""
    tid, kw = make_task(tmp_path)
    r = yaml.safe_load(open(kw["hidden_dir"] / tid / "rubric.yaml"))
    r["rubric"]["understanding"] = [{"id": "U1", "cat": "mechanisms", "item": "x", "anchors": anchors()}]
    r["rubric"]["functions_playability"][0]["fine_only"] = True
    yaml.safe_dump(r, open(kw["hidden_dir"] / tid / "rubric.yaml", "w"))
    E, _ = T.validate(tid, **kw)
    assert any("rubric.yaml" in e for e in E)


def items_for(tmp_path):
    tid, kw = make_task(tmp_path)
    t = T.Task(tid, **kw)
    it = [{"id": c["id"], "column": "content_realization", "score": 1.0, "could_not_test": False} for c in t.items("content_realization")]
    it[1]["score"] = 0.0                                                  # CR2 missing
    it += [{"id": "FP1", "column": "functions_playability", "score": 0.5, "could_not_test": False},
           {"id": "FP2", "column": "functions_playability", "score": 0.0, "could_not_test": False}]
    it += [{"id": v["id"], "column": "visual_presentation", "score": 1.0, "could_not_test": False} for v in t.items("visual_presentation")]
    return it, t


def test_column_scores(tmp_path):
    """three scored columns; without P, overall = their equal-weight mean (= overall_cfv); a gate failure (any reason) zeroes
    everything (2026-11-06)"""
    it, t = items_for(tmp_path)
    s = scoring.column_scores(it, True)
    assert set(s) == {"C", "overall", "overall_cfv", "P", "P_status"} and set(s["C"]) == set(T.COLS_C) == {"content_realization", "functions_playability", "visual_presentation"}
    assert s["C"]["content_realization"] == 0.75 and s["C"]["functions_playability"] == 0.25 and s["C"]["visual_presentation"] == 1.0
    assert s["overall"] == s["overall_cfv"] == round((0.75 + 0.25 + 1.0) / 3, 4) and s["P"] is None and s["P_status"] == "missing"
    ex = it + [{"id": "FP3", "column": "functions_playability", "score": 0.0, "could_not_test": True, "excluded": True}]
    assert scoring.column_scores(ex, True)["C"]["functions_playability"] == 0.25          # excluded item out of the denominator
    g = scoring.column_scores(it, False)
    assert g["C"] == {c: 0.0 for c in T.COLS_C} and g["overall"] == 0.0 and g["overall_cfv"] == 0.0
    noV = scoring.column_scores([i for i in it if i["column"] != "visual_presentation"], True)
    assert noV["overall_cfv"] is None and noV["overall"] == round((0.75 + 0.25) / 2, 4)    # a missing column: weights renormalized


HF = lambda s, st="scored": {"status": st, "score": s}


def test_overall_with_preservation(tmp_path):
    """Overall = 0.3 CR + 0.3 FP + 0.3 VP + 0.1 P (2026-11-10); missing dimensions renormalized"""
    it, t = items_for(tmp_path)
    s = scoring.column_scores(it, True, HF(1.0))
    assert s["P"] == 1.0 and s["P_status"] == "scored"
    assert s["overall"] == round(0.3 * 0.75 + 0.3 * 0.25 + 0.3 * 1.0 + 0.1 * 1.0, 4) == 0.7
    assert s["overall_cfv"] == round(2 / 3, 4)                                              # the 3-column rule is kept
    assert scoring.column_scores(it, True, HF(0.0))["overall"] == round(0.3 * 2.0 / 1.0, 4)
    assert scoring.column_scores(it, True, HF(0.5))["overall"] == round(0.6 + 0.05, 4)
    # renormalization: VP missing + P -> weights 0.3 / 0.3 / 0.1 over 0.7
    noV = [i for i in it if i["column"] != "visual_presentation"]
    assert scoring.column_scores(noV, True, HF(1.0))["overall"] == round((0.3 * 0.75 + 0.3 * 0.25 + 0.1) / 0.7, 4)
    C = {"content_realization": None, "functions_playability": None, "visual_presentation": None}
    assert scoring.overall(C, 0.5) == 0.5 and scoring.overall(C) is None


def test_overall_insufficient_or_missing_P_is_old_mean(tmp_path):
    """P insufficient / gate_failed / judge_failed / no record / malformed -> P None and overall == the old 3-column mean"""
    it, t = items_for(tmp_path)
    old = round((0.75 + 0.25 + 1.0) / 3, 4)
    for hf in (None, {}, HF(None, "insufficient"), HF(None, "gate_failed"), HF(None, "judge_failed"), HF(None, "no_result"),
               HF(0.7), HF("1"), HF(True), HF(1.0, "insufficient")):
        s = scoring.column_scores(it, True, hf)
        assert s["P"] is None and s["overall"] == s["overall_cfv"] == old, hf
    assert scoring.column_scores(it, True, HF(None, "insufficient"))["P_status"] == "insufficient"
    # no P equals the old mean exactly, over many column values (no float drift from the weights)
    import itertools
    for a, b, c in itertools.product([0, 0.1667, 0.25, 0.3333, 0.5, 0.6667, 0.75, 0.8333, 1.0], repeat=3):
        C = {"content_realization": a, "functions_playability": b, "visual_presentation": c}
        assert scoring.overall(C) == scoring.overall_cfv(C) == round((a + b + c) / 3, 4)


def test_overall_gate_failure_ignores_P(tmp_path):
    it, t = items_for(tmp_path)
    for hf in (HF(1.0), HF(None, "gate_failed"), None):
        s = scoring.column_scores(it, False, hf)
        assert s["C"] == {c: 0.0 for c in T.COLS_C} and s["overall"] == 0.0 and s["overall_cfv"] == 0.0
    assert scoring.with_preservation({"C": {c: 0.0 for c in T.COLS_C}}, HF(1.0), False)["overall"] == 0.0


def test_eval_adds_preservation(tmp_path, monkeypatch):
    """gab eval: after CR / FP / VP the host-fidelity judge runs (force) and P enters overall; a judge crash keeps the 3-column mean"""
    from gab.eval import run as R
    class Tk: id = "T-VO-x"; game = "g"; modality = "video"
    rf = tmp_path / "result.json"
    res = {"status": "completed", "gate": {"pass": True}, "scores": scoring.column_scores(
        [{"id": "CR1", "column": "content_realization", "score": 1.0}, {"id": "FP1", "column": "functions_playability", "score": 0.5},
         {"id": "VP1", "column": "visual_presentation", "score": 0.0}], True)}
    seen = {}
    def judge_fn(d, tid, game, force=False, modality=None, tag=None):
        seen.update(force=force, tag=tag, modality=modality); return {"status": "scored", "score": 0.5}
    out = R.add_preservation(tmp_path, Tk, "fine", "t1", res, rf, judge_fn=judge_fn)
    assert seen == {"force": True, "tag": "t1", "modality": "video"}
    assert out["scores"]["P"] == 0.5 and out["scores"]["overall"] == round(0.3 * 1.5 + 0.05, 4) and out["scores"]["overall_cfv"] == 0.5
    assert out["host_fidelity"]["file"] == "host_fidelity@t1.json" and json.loads(rf.read_text()) == out
    def boom(*a, **k): raise KeyError("no checklist")
    out = R.add_preservation(tmp_path, Tk, "fine", None, res, rf, judge_fn=boom)
    assert out["scores"]["P"] is None and out["scores"]["P_status"] == "error" and out["scores"]["overall"] == 0.5
    monkeypatch.setenv("GAB_HOST_FIDELITY_IN_EVAL", "0"); assert R.host_fidelity_on() is False
    monkeypatch.setenv("GAB_HOST_FIDELITY_IN_EVAL", "1"); assert R.host_fidelity_on() is True


def test_explicit_requirements(tmp_path):
    it, t = items_for(tmp_path)
    ex = scoring.explicit(t.hidden["instructions_meta"]["explicit_requirements"], it)
    assert ex == [{"type": "specify", "items": ["CR2"], "satisfied": 0.0}, {"type": "modify", "items": ["FP2"], "satisfied": 0.0}]


def test_median_and_snap():
    assert scoring.median3([1, 0.5, 0]) == 0.5 and scoring.median3([1, None, 1]) == 1.0 and scoring.median3([None]) is None
    assert scoring.snap(0.75) in (0.5, 1.0) and scoring.snap(0.2) == 0.0


def test_aggregate_reads_results(tmp_path, monkeypatch):
    monkeypatch.setenv("GAB_RUNS_DIR", str(tmp_path))
    rows = [("T-VO-vid_000000000000.t1", 1.0, 0.5, 0.5, True, None), ("T-TO-txt_000000000001.e1", 0.5, 0.0, 1.0, True, None),
            ("T-IO-0000000002", 0.0, 0.0, 0.0, False, "host_removed"), ("T-HY-vid_000000000003.t1", 0.0, 0.0, 0.0, False, "load")]
    for tid, cr, f, v, gp, gf in rows:
        d = tmp_path / "x" / tid / "fine"; d.mkdir(parents=True)
        C = {"content_realization": cr, "functions_playability": f, "visual_presentation": v}
        (d / "result.json").write_text(json.dumps({"trial": {"task": tid, "level": "fine"}, "status": gf if gf == "host_removed" else ("load_failed" if gf else "completed"),
            "gate": {"pass": gp, **({"fail": gf} if gf == "host_removed" else {})}, "items": [], "explicit_requirements": [{"satisfied": 1.0}],
            "scores": {"C": C, "overall": scoring.overall(C)},
            "cost": {"agent": {"usd": 2.0, "wall_s": 60}, "eval": {"usd": 1.0, "game_minutes": 3}}}))
    d = tmp_path / "x" / "T-VO-vid_000000000009.t1" / "fine"; d.mkdir(parents=True)     # an older result: host_preservation, no overall
    (d / "result.json").write_text(json.dumps({"trial": {"task": "T-VO-vid_000000000009.t1", "level": "fine"}, "status": "completed",
        "gate": {"pass": True}, "items": [], "scores": {"C": {"content_realization": 1.0, "functions_playability": 1.0, "visual_presentation": 0.5,
        "host_preservation": 0.0}}, "cost": {"agent": {"usd": 2.0, "wall_s": 60}, "eval": {"usd": 1.0, "game_minutes": 3}}}))
    s = aggregate.score("x")
    c = s["cells"]["all"]
    assert c["n"] == 5 and c["content_realization"]["mean"] == 0.5 and "host_preservation" not in c
    assert c["overall"]["mean"] == round((2 / 3 + 0.5 + 0 + 0 + 2.5 / 3) / 5, 3)
    assert c["content_realization"]["ci95"][0] <= 0.5 <= c["content_realization"]["ci95"][1]
    assert c["gate_pass"] == 0.6 and c["gate_fail"] == {"load": 1, "host_removed": 1}
    assert c["agent_usd"] == 2.0 and c["explicit_satisfied"] == 1.0
    assert s["cells"]["text"]["n"] == 1 and s["cells"]["text"]["content_realization"]["mean"] == 0.5
    assert aggregate.score("x") == s                                      # idempotent
    main = aggregate.table(["x"], "main")
    assert "| run | n | CR | F&P | V&P | Overall | gate | explicit |" in main and "HP" not in main and "| x |" in main
    assert "| x |" in aggregate.table(["x"], "modality") and "HP" not in aggregate.table(["x"], "modality")
    t = (tmp_path / "x" / "report" / "tasks.csv").read_text()
    assert "gate_fail" in t and "host_removed" in t and "host_preservation" not in t


def test_check_trace():
    assert check_trace({"steps": [{"do": "key", "keys": "w"}, {"do": "shot"}]}) == []
    assert check_trace({"steps": []}) and check_trace({"steps": [{"do": "teleport"}]}) and check_trace({"steps": [{"do": "click", "x": 1}]})


def test_check_deliverable(tmp_path):
    dv = tmp_path / "deliverable"
    assert not check_deliverable(dv)["ok"]
    (dv / "mod" / "m").mkdir(parents=True); (dv / "mod" / "m" / "init.lua").write_text("--")
    (dv / "ACCESS.md").write_text("start")
    (dv / "traces").mkdir(); (dv / "shots").mkdir()
    for k in (1, 2): (dv / "traces" / f"t{k}.yaml").write_text("claims: [x]\nsteps:\n  - {do: wait, seconds: 1}\n")
    for k in (1, 2, 3): (dv / "shots" / f"{k}.png").write_bytes(b"png")
    r = check_deliverable(dv)
    assert r["ok"], r


def test_ts():
    assert ts("1:05.10") == pytest.approx(65.1) and ts("00:02.0") == 2.0 and ts("0:01:05") == 65.0 and ts(3) == 3.0


def test_exported_tasks_validate():
    """the real packages (if exported on this machine)"""
    ids = T.all_task_ids()
    if not ids: pytest.skip("no exported tasks")
    bad = {i: T.validate(i, check_media=False)[0] for i in ids}
    assert not {k: v for k, v in bad.items() if v}


def test_auto_shot_points():
    """the agent's shot steps are not counted; the rest is sampled evenly up to the cap; long waits get inner points"""
    from gab.game.base import auto_shot_points
    steps = [{"do": "shot"}] + [{"do": "key", "keys": ["w"]}] * 20 + [{"do": "wait", "seconds": 10}, {"do": "shot"}]
    pts = auto_shot_points(steps, 5, 3.0)
    assert sum(len(v) for v in pts.values()) == 5 and 0 not in pts and 22 not in pts
    assert pts.get(1) == [None] and pts.get(21) == [9.0]               # first and last candidate are kept
    assert auto_shot_points(steps, 0, 3.0) == {}
    few = auto_shot_points([{"do": "click", "x": 1, "y": 1}, {"do": "wait", "seconds": 7}], 10, 3.0)
    assert few == {0: [None], 1: [3.0, 6.0]}


def test_baseline_kept_per_site(tmp_path, monkeypatch):
    """vanilla baselines are cached per game version AND per site (pool sandbox / this machine)"""
    from gab.eval import regression as R
    monkeypatch.setenv("GAB_GAMES_DIR", str(tmp_path)); monkeypatch.setenv("GAB_HOST", "pool")
    class Seat: sandbox = "sandbox-a"
    class Ad:
        name = "g"; seat = Seat(); runs = 0
        def version(self): return {"build": "1.0"}
        def run_probes(self, names, mod, label): Ad.runs += 1; return {"p": {"v": Ad.runs}}
    ad = Ad(); b1, f1 = R.baseline(ad, ["p"]); b2, f2 = R.baseline(ad, ["p"])
    assert Ad.runs == 1 and "sandbox-a" in f1 and f1 == f2
    Seat.sandbox = "sandbox-b"; b3, f3 = R.baseline(ad, ["p"])
    assert Ad.runs == 2 and "sandbox-b" in f3 and b3["_meta"]["site"] == "sandbox-b"

"""editing mode (gab/edit/edit.py, gab/eval/edit.py, gab/edit/cli.py): store fetch, staging + brief, checkpoint comparison, snapshot
resolution, preservation baseline and scores. No game, no model."""
import json
from pathlib import Path

import pytest
import yaml

from gab.edit import edit as E
from gab.eval import edit as EV

PARENT, EID = "T-IO-edittest", "E-T-IO-edittest.AP"


def _pack(tmp_path, key):
    """a checkpoint archive as scripts/pack_checkpoints.py writes it (<key>/work, deliverable, eval, result.json, ...)"""
    import tarfile
    src = tmp_path / "pack" / key
    for p in ("work/deliverable/mod/railway", "work/deliverable/traces", "work/deliverable/shots", "work/gen", "deliverable/mod/railway",
              "deliverable/traces", "deliverable/shots", "eval/functions/replay/01_signals"):
        (src / p).mkdir(parents=True, exist_ok=True)
    for root in (src / "work" / "deliverable", src / "deliverable"):
        (root / "mod" / "railway" / "init.lua").write_text("-- rails")
        (root / "traces" / "01_signals.yaml").write_text("start: fresh\nclaims: [stops at red]\nsteps: []\n")
        (root / "shots" / "01.png").write_bytes(b"png"); (root / "ACCESS.md").write_text("walk to the box")
    (src / "work" / "gen" / "train.png").write_bytes(b"asset"); (src / "work" / "notes.txt").write_text("agent notes")
    (src / "eval" / "functions" / "replay" / "01_signals" / "000_a.png").write_bytes(b"png")
    (src / "eval" / "functions" / "functions.json").write_text(json.dumps({"replays": [
        {"trace": "01_signals.yaml", "ok": True, "claims": [], "shots": ["/other/machine/eval@lite-base/functions/replay/01_signals/000_a.png"],
         "timeline": []}]}))
    (src / "result.json").write_text(json.dumps({"status": "completed", "gate": {"pass": True}, "scores": {"overall": 0.9}}))
    (src / "checkpoint.json").write_text(json.dumps({"key": key, "parent": PARENT, "deliverable_sha256": "abc", "overall": 0.9,
                                                     "source": {"cfg": "fable52-claudecode-s0", "round": 0}}))
    arc = tmp_path / "dataset" / "checkpoints" / f"{key}.tar.gz"; arc.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(arc, "w:gz") as t: t.add(src, arcname=key)
    return arc


@pytest.fixture
def env(tmp_path, monkeypatch):
    t = tmp_path / "tasks" / PARENT; t.mkdir(parents=True)
    (t / "task.yaml").write_text(yaml.safe_dump({"task_id": PARENT, "target_game": "voxelibre", "modality": "image",
                                                 "category": "mechanics_behavior", "references": ["img01"]}))
    (t / "references.yaml").write_text(yaml.safe_dump({"refs": [{"id": "img01", "kind": "image", "file": "img01.jpg", "sha256": "x"}]}))
    (t / "instruction_fine.md").write_text("Build a railway with red and green signals.")
    m = tmp_path / "media" / PARENT; m.mkdir(parents=True); (m / "img01.jpg").write_bytes(b"jpg")
    h = tmp_path / "hidden" / PARENT; h.mkdir(parents=True)
    (h / "rubric.yaml").write_text(yaml.safe_dump({"rubric": {"content_realization": [], "visual_presentation": [],
        "functions_playability": [{"id": "FP3", "item": "A train stops at red and moves on at green", "anchors": {1: "a", 0.5: "b", 0: "c"}}]}}))
    ed = tmp_path / "editing"; ed.mkdir()
    for k, v in (("TASKS_DIR", tmp_path / "tasks"), ("MEDIA_DIR", tmp_path / "media"), ("HIDDEN_DIR", tmp_path / "hidden"),
                 ("RUNS_DIR", tmp_path / "runs"), ("TARGETS_DIR", tmp_path / "targets"), ("EDIT_STORE", tmp_path / "store"),
                 ("EDIT_DIR", ed), ("HF_DATASET", "test/dataset")):
        monkeypatch.setenv("GAB_" + k, str(v))
    (ed / "tasks.json").write_text(json.dumps([
        {"edit_task_id": EID, "parent_task": PARENT, "game": "VoxeLibre", "game_id": "voxelibre", "track": "open", "edit_class": "appearance",
         "edit_class_code": "AP", "scope": "group", "reference_dependence": "R1", "checkpoint": {"key": PARENT, "refine_rounds": 0},
         "instruction": "Make every rail vehicle look like a small passenger train. Keep everything else as it is."}]))
    an = {"1": "yes", "0.5": "part", "0": "no"}
    (ed / "rubrics.json").write_text(json.dumps([{"edit_task_id": EID, "rubric": {
        "edit_success": [{"id": "ES1", "item": "cab front", "anchors": an, "judge": "visual", "evidence": ["edit-request"]},
                         {"id": "ES2", "item": "new carts too", "anchors": an, "judge": "play", "evidence": ["edit-request"]}],
        "preservation": [{"id": "P1", "item": "still stops at red", "anchors": an, "judge": "play", "evidence": ["parent:FP3", "card:FP3"]},
                         {"id": "P2", "item": "layout unchanged", "anchors": an, "judge": "visual", "evidence": ["parent:CR1"]},
                         {"id": "P3", "item": "signals readable", "anchors": an, "judge": "visual", "evidence": []}]}}]))
    (ed / "p_to_es.json").write_text(json.dumps({"edits": {EID: {"items": {"P1": {"edit_dependent": False}, "P2": {"edit_dependent": False},
                                                                           "P3": {"edit_dependent": False}}}}}))
    from gab.setup import media
    arc = _pack(tmp_path, PARENT)
    (ed / "checkpoints.json").write_text(json.dumps({"checkpoints": {PARENT: {"file": f"checkpoints/{PARENT}.tar.gz",
                                                                             "sha256": media.sha256(arc), "size_bytes": arc.stat().st_size}}}))
    def fake_get(path, dst):
        import shutil
        dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy(tmp_path / "dataset" / path, dst)
    monkeypatch.setattr(media, "_get", fake_get)
    return tmp_path


def test_tasks_drop_parents_and_rubric(env):
    assert list(E.edit_tasks()) == [EID]
    et = E.EditTask(EID)
    assert et.game == "voxelibre" and et.parent.id == PARENT and et.code == "AP"
    r = et.rubric(); assert [i["id"] for i in r["edit_success"]] == ["ES1", "ES2"] and r["preservation"][0]["anchors"]["0.5"] == "part"
    assert "Change request:" in et.instruction() and "railway" in et.instruction()
    assert E.select(classes=["CS"]) == [] and [x.id for x in E.select(games=["voxelibre"])] == [EID]


def test_checkpoint_download_and_store(env):
    st = E.EditTask(EID).store()                                                     # downloaded on first use, sha256 checked
    w = st / "work"
    assert (w / "deliverable" / "mod" / "railway" / "init.lua").exists() and (w / "gen" / "train.png").exists() and (w / "notes.txt").exists()
    assert (st / "eval" / "functions" / "functions.json").exists() and (st / "p_baseline").is_dir()
    assert json.loads((st / "checkpoint.json").read_text())["overall"] == 0.9
    assert not (st / "deliverable" / "ACCESS.md").stat().st_mode & 0o222          # read-only store
    assert E.checkpoint(PARENT) == st                                                # cached
    R = E.ckpt_replays(st)
    assert R[0]["shots"] == [str(st / "eval" / "functions" / "replay" / "01_signals" / "000_a.png")]   # mapped into the store


def test_checkpoint_sha_mismatch_is_refused(env):
    from gab.setup import media
    m = json.loads((env / "editing" / "checkpoints.json").read_text()); m["checkpoints"][PARENT]["sha256"] = "0" * 64
    (env / "editing" / "checkpoints.json").write_text(json.dumps(m))
    assert media.fetch_checkpoint(PARENT) is False and not E.store_dir(PARENT).exists()
    with pytest.raises(KeyError): E.EditTask(EID).store()


def test_stage_edit_workspace_and_brief(env):
    et = E.EditTask(EID); d = env / "runs" / "x-edit-s0" / EID / "edit"
    w, b = E.stage_edit(d, et, 60)
    assert (w / "deliverable" / "mod" / "railway" / "init.lua").exists() and not (w / "deliverable" / "DONE").exists()
    assert (w / "reference" / "img01.jpg").exists() and (w / "task" / "instruction_fine.md").exists()
    assert (w / "task" / "edit_request.md").read_text().startswith("Make every rail vehicle")
    assert not (w / "targets").exists() and not (w / "lessons.md").exists()
    assert (w / "deliverable" / "ACCESS.md").stat().st_mode & 0o200                  # writable again
    for s in ("Build a railway with red and green signals.", "This project already implements that request",
              "Make every rail vehicle look like a small passenger train", "nothing else is broken", "looks like part of the game",
              'gen-image "PROMPT" OUT.png [--ref IMG ...]', "gen-3d", "gen-sfx", "gen-music", "transcribe", "ffmpeg",
              "You may make your own reference or concept pictures of the change with gen-image", "ACCESS.md         updated",
              "the rest of the earlier work (sources, generated assets, scripts) is in", "at least one shows the change working", f"{w}/deliverable/DONE"):
        assert s in b, s
    assert b.count("Keep everything else as it is.") == 1 and "lessons" not in b and "{" not in b.replace('{"ok": false, ...}', "")
    (w / "deliverable" / "mod" / "railway" / "init.lua").write_text("-- changed")       # a second staging keeps the agent's work
    E.stage_edit(d, et, 60)
    assert (w / "deliverable" / "mod" / "railway" / "init.lua").read_text() == "-- changed"
    assert "edit target pictures" not in b


def test_items_text_names_the_parent_requirement(env):
    et = E.EditTask(EID)
    txt = EV.items_text(et, et.rubric()["preservation"])
    assert "P1 (preservation): still stops at red" in txt and "FP3: A train stops at red and moves on at green" in txt
    assert "trap" not in txt and "card:" not in txt


def test_relative_and_scores():
    assert EV.relative(1.0, 1.0) == 1.0 and EV.relative(0.5, 1.0) == 0.5 and EV.relative(1.0, 0.0) is None
    assert EV.relative(0.5, 0.5) == 1.0 and EV.relative(1.0, 0.5) == 1.0 and EV.relative(0.0, 0.5) == 0.0 and EV.relative(1, None) is None
    items = [{"column": "edit_success", "score": 1.0}, {"column": "edit_success", "score": 0.5},
             {"column": "preservation", "score": 1.0, "relative": 1.0, "na": False},
             {"column": "preservation", "score": 0.0, "relative": None, "na": True},
             {"column": "preservation", "score": 0.5, "relative": 0.5, "na": False}]
    s = EV.edit_scores(items, True, 1.0)
    assert s["ES"] == 0.75 and s["P"] == 0.75 and s["n_P_na"] == 1 and s["overall"] == round(0.75 * (0.5 + 0.4 * 0.75 + 0.1), 4)
    assert EV.edit_scores(items, True, None)["overall"] == round(0.75 * round((0.5 + 0.4 * 0.75) / 0.9, 4), 4)
    assert EV.edit_scores(items, False, 1.0)["overall"] == 0.0
    noop = [{"column": "edit_success", "score": 0.0}, {"column": "preservation", "score": 1, "relative": 1.0, "na": False}]
    assert EV.edit_scores(noop, True, 1.0)["overall"] == 0.0                          # a no-op scores 0
    perfect = [{"column": "edit_success", "score": 1.0}, {"column": "preservation", "score": 1, "relative": 1.0, "na": False}]
    assert EV.edit_scores(perfect, True, 1.0)["overall"] == 1.0
    broke = [{"column": "edit_success", "score": 1.0}, {"column": "preservation", "score": 0, "relative": 0.0, "na": False}]
    assert EV.edit_scores(broke, True, 0.0)["overall"] == 0.5                         # correct edit, everything else broken
    assert EV.round_of_tag("edit") == 0 and EV.round_of_tag("edit-r1") == 1 and EV.tag_of_round(0) == "edit" and EV.tag_of_round(2) == "edit-r2"


def test_snapshot_by_round(tmp_path):
    d = tmp_path / "t"; (d / "deliverable").mkdir(parents=True); (d / "agent").mkdir()
    (d / "agent" / "agent.json").write_text(json.dumps({"refine_rounds": [{"round": 1}]}))
    (d / "attempts" / "1" / "deliverable").mkdir(parents=True); (d / "attempts" / "1" / "agent").mkdir()
    (d / "attempts" / "1" / "agent" / "agent.json").write_text(json.dumps({"exit_reason": "done"}))
    assert EV.snapshot(d, 1) == (d / "deliverable", "deliverable")
    assert EV.snapshot(d, 0) == (d / "attempts" / "1" / "deliverable", "attempts/1/deliverable")
    assert EV.snapshot(d, 2) == (None, None)


def test_ckpt_compare_flags_what_broke(env):
    et = E.EditTask(EID); E.checkpoint(PARENT)
    d = env / "runs" / "x" / EID / "edit"; (d / "deliverable" / "traces").mkdir(parents=True)
    (d / "deliverable" / "traces" / "02_train.yaml").write_text("claims: [train look]")          # 01_signals was removed
    assert [f.name for f in E.changed_traces(d, et)] == ["01_signals.yaml"]
    rd = d / "refine" / "r0"; (rd / "critic").mkdir(parents=True); (rd / "critic" / "feedback.md").write_text("## Change coverage\n")
    (rd / "replay_ckpt").mkdir(); (rd / "replay_ckpt" / "record.json").write_text(json.dumps({"traces": [{"trace": "01_signals.yaml", "ok": False, "error": "start failed"}]}))
    res = E.ckpt_compare(d, et, rd, gate={"pass": False, "fail": "unstable"})
    assert len(res["regressions"]) == 2 and "01_signals.yaml" in res["regressions"][1]
    fb = (rd / "critic" / "feedback.md").read_text()
    assert fb.startswith("## Regressions against the project you started from") and fb.count("Regressions against") == 1
    E.ckpt_compare(d, et, rd, gate=None); assert (rd / "critic" / "feedback.md").read_text().count("Regressions against") == 1


def test_p_baseline_cached_and_na(env, monkeypatch):
    et = E.EditTask(EID); E.checkpoint(PARENT); calls = []
    def fake(et_, F, dv, R, out, cost, before=None):
        calls.append([f["id"] for f in F]); assert before is None and R and R[0]["shots"]
        return {"P1": {"score": 1}, "P2": {"score": 0}, "P3": {"score": 0.5}}
    monkeypatch.setattr(EV, "judge_all", fake)
    b = EV.p_baseline(et); assert b["status"] == "scored" and b["items"] == {"P1": 1.0, "P2": 0.0, "P3": 0.5}
    assert EV.p_baseline(et)["items"] == b["items"] and calls == [["P1", "P2", "P3"]]                     # cached
    caps = list((E.store_dir(PARENT) / "p_baseline" / EID / "replay" / "captures").rglob("*.png"))
    assert caps and all(c.stat().st_mode & 0o200 for c in caps)                       # writable copies inside the judge's folder
    assert EV.p_baseline(et, force=True)["status"] == "scored"                        # a recompute can clear its folder


def test_cli_registers_edit_commands():
    import argparse
    from gab.edit import cli as editcli
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd")
    editcli.add_parser(sp)
    assert set(sp.choices) == {"edit", "edit-refine", "eval-edit"}
    from gab.cli import _opts
    a = ap.parse_args(["edit", "--name", "x-edit-s0", "--edit", EID, "--agent", "opencode", "--agent-in-container", "--auth", "api", "--minutes", "15"])
    assert a.minutes == 15 and _opts(a) == {"auth": "api", "in_container": True}
    e = ap.parse_args(["eval-edit", "--name", "x-edit-s0", "--round", "2", "--track", "open"])
    assert e.round == 2 and e.tag is None and e.track == "open"


def test_start_key_from_the_task_row(env):
    """an edit whose start is another snapshot (e.g. the R1 hand-in of another configuration) names its own store key"""
    rows = json.loads((env / "editing" / "tasks.json").read_text())
    rows[0]["checkpoint"]["key"] = f"{PARENT}@sonnet55-claudecode-s0-r1"; (env / "editing" / "tasks.json").write_text(json.dumps(rows))
    et = E.EditTask(EID)
    assert et.start_key() == f"{PARENT}@sonnet55-claudecode-s0-r1" and et.store_path() == E.store_dir(f"{PARENT}@sonnet55-claudecode-s0-r1")
    assert E.select(track="commercial") == [] and [x.id for x in E.select(track="open")] == [EID]


def test_p_to_es_split(env, monkeypatch):
    et = E.EditTask(EID)
    d = json.loads(EV.p_to_es_file().read_text()); d["edits"][EID]["items"]["P3"]["edit_dependent"] = True
    EV.p_to_es_file().write_text(json.dumps(d))
    ES, P = EV.split_rubric(et)
    assert [i["id"] for i in ES] == ["ES1", "ES2", "P3"] and ES[-1]["moved_from"] == "preservation" and [i["id"] for i in P] == ["P1", "P2"]
    d["edits"][EID]["items"]["P1"]["manual"] = True; EV.p_to_es_file().write_text(json.dumps(d))
    assert EV.moved_ids(EID) == {"P1", "P3"}                                         # a manual verdict overrides the model's
    E.checkpoint(PARENT)
    monkeypatch.setattr(EV, "judge_all", lambda et_, F, *a, **k: {f["id"]: {"score": 1} for f in F})
    assert EV.p_baseline(et)["p_ids"] == ["P2"]                                       # only what stays P


def test_real_p_to_es_covers_every_edit():
    from gab import config
    d = json.loads((config.REPO / "benchmark" / "editing" / "p_to_es.json").read_text())
    assert set(d["edits"]) == set(E.edit_tasks()) and len(d["edits"]) == 80


def test_seed_memory_from_the_construction_run(env, monkeypatch):
    from gab.refine import memory as mem
    et = E.EditTask(EID); d = env / "runs" / "sonnet55-claudecode-edit-s0" / EID / "edit"; d.mkdir(parents=True)
    assert E.construction_run("sonnet55-claudecode-edit-s0") == "sonnet55-claudecode-s0" and E.construction_run("x-s0") is None
    assert E.seed_memory(d, et) is None                                               # nothing to seed from
    src = mem.game_file(mem.root("sonnet55-claudecode-s0"), "voxelibre"); src.parent.mkdir(parents=True); src.write_text("### voxelibre-001\n")
    assert E.seed_memory(d, et) == src
    dst = mem.game_file(mem.root("sonnet55-claudecode-edit-s0"), "voxelibre")
    assert dst.read_text() == "### voxelibre-001\n" and "memory:sonnet55-claudecode-s0" in (dst.parent.parent / "SEED.json").read_text()
    src.write_text("changed"); assert E.seed_memory(d, et) is None and dst.read_text() == "### voxelibre-001\n"   # once


def test_brief_names_the_mod_as_source_for_a_non_final_round(env, monkeypatch):
    et = E.EditTask(EID); E.checkpoint(PARENT)
    monkeypatch.setattr(E, "full_work", lambda et_: False)
    b = E.brief_text(et, env / "w", 60)
    assert "the source is the installed mod itself" in b and "the rest of the earlier work" not in b


def test_visual_candidates_skip_stale_developer_shots(tmp_path):
    """a developer screenshot left unchanged from the start shows the BEFORE state: not an AFTER candidate"""
    import subprocess
    old, new = tmp_path / "old" / "shots", tmp_path / "new" / "shots"; old.mkdir(parents=True); new.mkdir(parents=True)
    for f, c in ((old / "01.png", "red"), (new / "01.png", "red"), (new / "02.png", "blue")):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={c}:s=64x36", "-frames:v", "1", str(f)], check=True)
    C = EV._cands(tmp_path / "new", [], tmp_path / "out", stale=old)
    assert len(C) == 1 and "02.png" in C[0]
    assert len(EV._cands(tmp_path / "new", [], tmp_path / "out2")) == 2


def test_v2_levels_anchors_and_tags(env):
    from gab.eval import edit_v2 as V2
    assert V2.LEVELS_ES == V2.LEVELS_P == (0.0, 0.5, 1.0)
    assert V2.snap(0.7, V2.LEVELS_ES) == 0.5 and V2.snap(0.8, V2.LEVELS_ES) == 1.0 and V2.snap(0.2, V2.LEVELS_ES) == 0.0
    q = V2.quality_item(E.EditTask(EID))
    assert q["id"] == "ESQ" and list(q["anchors"]) == ["1", "0.5", "0"]
    assert EV.round_of_tag("edit") == 0 and EV.round_of_tag("edit-r1") == 1


def _video(p, secs=12):
    import subprocess
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=duration={secs}:size=320x180:rate=8",
                    "-pix_fmt", "yuv420p", str(p)], check=True)


def test_v2_judging_locates_segments_and_scores(env, monkeypatch, tmp_path):
    """locator -> dense frames of its segments -> both verifiers see them; verdicts carry evidence segments; ESQ is added"""
    from gab.eval import edit_v2 as V2
    et = E.EditTask(EID); E.checkpoint(PARENT); st = E.store_dir(PARENT)
    fn = tmp_path / "functions"; (fn / "replay" / "01_signals").mkdir(parents=True)
    _video(fn / "replay" / "01_signals.mp4"); (fn / "replay" / "01_signals" / "a.png").write_bytes(b"png")
    (fn / "replay" / "01_signals" / "replay.json").write_text(json.dumps({"log": [{"step": {"do": "wait", "seconds": 4}, "s": 4.0},
                                                                                  {"step": {"do": "key", "keys": "c"}, "s": 1.0}]}))
    R = [{"trace": "01_signals.yaml", "ok": True, "claims": ["falls"], "shots": [str(fn / "replay" / "01_signals" / "a.png")], "timeline": []}]
    prompts = {}
    def fake(prompt, out, name, **k):
        prompts[name] = prompt
        if name.startswith("locator"):
            return json.dumps({"items": [{"id": i, "segments": [{"video": "01_signals", "t0": 4, "t1": 9}]} for i in ("ES1", "ES2", "P1", "P2", "P3")]}), {}
        ids = [l.split(" (")[0] for l in prompt.split("ITEMS:")[1].split("LOCATED FRAMES:")[0].strip().splitlines() if l and not l.startswith(" ")]
        return json.dumps({"items": [{"id": i, "score": 0.7, "segments": [{"video": "01_signals", "t0": 5, "t1": 7}]} for i in ids]}), {}
    monkeypatch.setattr(V2, "claude", fake)
    F = E.EditTask(EID).rubric(); items = F["edit_success"] + F["preservation"] + [V2.quality_item(et)]
    v = V2.judge_all_v2(et, items, st / "deliverable", R, fn, {}, before=(st / "deliverable", [], []))
    assert set(v) == {"ES1", "ES2", "P1", "P2", "P3", "ESQ"}
    assert v["ES2"]["segments"] == [{"video": "01_signals", "t0": 5, "t1": 7}] and v["ES2"]["located"][0]["t0"] == 4
    assert "t~4s do=key keys=c" in prompts["locator"] and "ESQ" not in prompts["locator"]
    rp = prompts["edit_replay_verifier_v2"]; vp = prompts["edit_visual_judge_v2"]
    assert "0.75" not in rp and "five levels" not in rp and "three levels" in rp and "(edit, 3 levels)" in rp
    assert "v2/frames/01_signals_0000.0-0011.0/t0000.0s.jpg" in rp and "ESQ (edit, 3 levels)" in vp
    assert len(list((fn / "replay" / "v2" / "frames").rglob("*.jpg"))) >= 8 and (fn / "v2" / "located.json").exists()


def test_eval_edit_of_an_infra_error_is_not_scored(env):
    et = E.EditTask(EID)
    d = env / "runs" / "x" / EID / "edit"; (d / "deliverable").mkdir(parents=True); (d / "agent").mkdir()
    (d / "agent" / "agent.json").write_text(json.dumps({"exit_reason": "crash", "infra": {"status": "infra_error", "primary": "api_auth"}}))
    (d / "trial.json").write_text(json.dumps({"name": "x"}))
    r = EV.eval_edit(d, et)
    assert r["status"] == "infra_error" and (d / "result@edit.json").exists() and r["scores"]["overall"] is None


def test_edit_memory_is_separate_from_construction(env):
    """one run name for both modes (gab bench): editing trials use their own memory <run>-edit, seeded once from the run's
    Construction lessons of the game, as the paper's separate <cfg>-edit-s0 runs were; the Construction memory is never written"""
    from gab.refine import memory as mem
    et = E.EditTask(EID)
    d = env / "runs" / "my-agent" / EID / "edit"; d.mkdir(parents=True)
    assert mem.run_name(d) == "my-agent-edit" and mem.run_name(env / "runs" / "my-agent" / PARENT / "fine") == "my-agent"
    assert mem.run_name(env / "runs" / "sonnet55-claudecode-edit-s0" / EID / "edit") == "sonnet55-claudecode-edit-s0"   # paper names
    src = mem.game_file(mem.root("my-agent"), "voxelibre"); src.parent.mkdir(parents=True); src.write_text("### voxelibre-001\n")
    assert E.seed_memory(d, et) == src
    dst = mem.game_file(mem.root("my-agent-edit"), "voxelibre")
    assert dst.read_text() == "### voxelibre-001\n" and dst != src
    dst.write_text("### voxelibre-001\n### voxelibre-002 (edit lesson)\n")
    assert src.read_text() == "### voxelibre-001\n"                                  # the Construction memory is untouched


def test_media_fetch_counts_files_and_size(env):
    from gab.setup import media
    media.STATS.clear()
    assert media.fetch_checkpoint(PARENT)
    c = media.STATS["checkpoints"]
    assert c["checked"] == 1 and c["downloaded"] == 1 and c["bytes"] > 0
    assert "checkpoints 1 files (1 downloaded" in media.summary() and "total" in media.summary()
    media.STATS.clear(); assert media.fetch_checkpoint(PARENT)
    assert media.STATS["checkpoints"]["downloaded"] == 0 and media.STATS["checkpoints"]["checked"] == 1     # already in the store

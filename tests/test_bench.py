"""`gab bench` argument handling and step order, Full as the default evaluation, the benchmark's task sets (no game, no model)."""
import argparse, os

import pytest

from gab.run import bench
from gab import cli, config
from gab.edit import cli as editcli


def parse(argv):
    seen = {}
    def grab(a): seen["a"] = a
    real = cli.cmd_bench
    cli.cmd_bench = grab
    try: cli.main(argv)
    finally: cli.cmd_bench = real
    return seen["a"]


def test_bench_arguments():
    a = parse(["bench", "--name", "x", "--agent", "opencode", "--model", "m", "--base-url", "http://h/v1", "--api-key-env", "K",
               "--track", "open", "--workers", "3", "--yes"])
    assert (a.name, a.agent, a.model, a.base_url, a.api_key_env, a.track, a.workers, a.yes) == ("x", "opencode", "m", "http://h/v1", "K", "open", 3, True)
    assert not a.lite and not a.construction_only and not a.editing_only and a.minutes == 120 and a.edit_minutes == 60
    a = parse(["bench", "--name", "x", "--lite", "--editing-only", "--agent-cmd", "my_agent {brief}"])
    assert a.lite and a.editing_only and a.agent_cmd == "my_agent {brief}" and a.track == "all" and a.agent == "claude"
    with pytest.raises(SystemExit): parse(["bench", "--name", "x", "--construction-only", "--editing-only"])
    with pytest.raises(SystemExit): parse(["bench", "--name", "x", "--track", "steam"])
    with pytest.raises(SystemExit): parse(["bench", "--agent", "claude"])                     # --name is required
    with pytest.raises(SystemExit): parse(["bench", "--name", "x", "--agent", "gemini"])      # removed harnesses are refused


@pytest.fixture
def steps(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cmd_run", lambda a: calls.append(("run", a)))
    monkeypatch.setattr(editcli, "cmd_edit", lambda a: calls.append(("edit", a)))
    monkeypatch.setattr(cli, "cmd_eval", lambda a: calls.append(("eval", a)))
    monkeypatch.setattr(editcli, "cmd_eval", lambda a: calls.append(("eval-edit", a)))
    import gab.report.aggregate as agg
    monkeypatch.setattr(agg, "score", lambda name, **kw: calls.append(("score", name, kw)) or {"main_table": "| t |", "report": "r"})
    return calls


def test_bench_runs_the_steps_in_order_with_full_evaluation(steps):
    bench.bench(parse(["bench", "--name", "x", "--agent", "codex", "--model", "m", "--track", "open", "--yes"]))
    assert [c[0] for c in steps] == ["run", "edit", "eval", "eval-edit", "score"]
    run, edit, ev = steps[0][1], steps[1][1], steps[2][1]
    assert run.no_eval and run.minutes == 120 and run.track == "open" and run.agent == "codex" and run.model == "m" and run.yes
    assert edit.minutes == 60 and edit.track == "open"
    assert ev.lite is False and cli.eval_mode(ev) == "full" and ev.round == 0 and ev.tag is None
    name, kw = steps[4][1], steps[4][2]
    assert name == "x" and kw["n_tasks"]["all"] == 77 and kw["n_edits"]["all"] == 64     # the open-source track


def test_bench_lite_and_partial(steps):
    code = bench.bench(parse(["bench", "--name", "x", "--lite", "--construction-only", "--yes"]))
    assert code == 0 and [c[0] for c in steps] == ["run", "eval", "score"]
    assert cli.eval_mode(steps[1][1]) == "lite" and steps[2][1] == "x@lite"
    steps.clear()
    bench.bench(parse(["bench", "--name", "x", "--editing-only", "--yes"]))
    assert [c[0] for c in steps] == ["edit", "eval-edit", "score"]


def test_bench_continues_after_a_failed_step(steps, monkeypatch):
    def bad(a): steps.append(("run", a)); raise SystemExit(3)
    monkeypatch.setattr(cli, "cmd_run", bad)
    assert bench.bench(parse(["bench", "--name", "x", "--yes"])) == 3
    assert [c[0] for c in steps] == ["run", "edit", "eval", "eval-edit", "score"]
    def stop(a): raise SystemExit(143)
    monkeypatch.setattr(cli, "cmd_run", stop)
    with pytest.raises(SystemExit): bench.bench(parse(["bench", "--name", "x", "--yes"]))      # an interrupt stops the bench


def test_full_is_the_default_evaluation(monkeypatch):
    from gab.eval import functions
    monkeypatch.delenv("GAB_EVIDENCE", raising=False)
    assert config.DEFAULTS["evidence"] == "full" and functions.evidence_mode() == "full"
    assert functions.evidence_mode("lite") == "shots+state+replay"
    ns = lambda **k: argparse.Namespace(**{"lite": False, "evidence": None, **k})
    assert cli.eval_mode(ns()) == "full" and cli.eval_mode(ns(lite=True)) == "lite" and cli.eval_mode(ns(evidence="lite")) == "lite"
    with pytest.raises(SystemExit): cli.eval_mode(ns(lite=True, evidence="full"))
    assert [cli.eval_tag_for(l, k) for l, k in ((False, 0), (True, 0), (False, 2), (True, 1))] == [None, "lite", "r2", "lite-r1"]


def test_eval_command_sets_the_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("GAB_RUNS_DIR", str(tmp_path))
    for k in ("GAB_EVIDENCE", "GAB_EVAL_TAG"): monkeypatch.delenv(k, raising=False)
    cli.main(["eval", "--name", "nothing", "--task", "T-IO-43b34cf1fa"])
    assert os.environ["GAB_EVIDENCE"] == "full" and "GAB_EVAL_TAG" not in os.environ
    cli.main(["eval", "--name", "nothing", "--task", "T-IO-43b34cf1fa", "--lite"])
    assert os.environ["GAB_EVIDENCE"] == "lite" and os.environ["GAB_EVAL_TAG"] == "lite"
    for k in ("GAB_EVIDENCE", "GAB_EVAL_TAG"): os.environ.pop(k, None)


def test_benchmark_task_sets():
    a = argparse.Namespace(task=None, game=None, category=None, modality=None, track="all", edit=None)
    ts, es = cli._tasks(a), cli._edits(a)
    assert len(ts) == 125 and len(es) == 80 and len({t.game for t in ts}) == 14
    a.track = "commercial"
    assert len(cli._tasks(a)) == 48 and len(cli._edits(a)) == 16
    from gab.edit.edit import manifest
    keys = {e.start_key() for e in es}
    assert keys <= set(manifest()) and all(e.parent.id in {t.id for t in ts} for e in es)
    for t in ts: assert t.hdir.exists() and (t.hdir / "rubric.yaml").exists()           # every rubric is in the release


def test_removed_commands_are_gone():
    for c in ("polish", "refine-fix", "oracle", "ps", "infra", "edit-classify-p", "edit-targets", "targets"):
        with pytest.raises(SystemExit) as e: cli.main([c, "--help"])
        assert e.value.code == 2                                                           # argparse: invalid choice


def test_score_writes_the_main_table(tmp_path, monkeypatch):
    import json
    from gab.report.aggregate import score
    monkeypatch.setenv("GAB_RUNS_DIR", str(tmp_path))
    def res(task, status, cr, fp, vp, p, ov, usd):
        d = tmp_path / "x" / task / "fine"; d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({"trial": {"task": task}, "status": status, "gate": {"pass": status == "completed"},
            "scores": {"C": {"content_realization": cr, "functions_playability": fp, "visual_presentation": vp}, "P": p, "overall": ov},
            "items": [], "cost": {"agent": {"usd": usd}, "eval": {"usd": 0.5}}}))
    res("T-IO-43b34cf1fa", "completed", 0.5, 0.5, 1.0, 1.0, 0.7, 2.0)                  # voxelibre (open)
    res("T-IO-0442e0b41e", "build_failed", 0, 0, 0, None, 0.0, 4.0)                    # stardew_valley (commercial)
    res("T-HY-vid_01f55d4c784a.t1", "infra_error", None, None, None, None, None, 1.0)  # pending, not 0
    e = tmp_path / "x" / "E-T-IO-43b34cf1fa.AP" / "edit"; e.mkdir(parents=True)
    (e / "result@edit.json").write_text(json.dumps({"mode": "edit", "edit": {"edit_task_id": "E-T-IO-43b34cf1fa.AP"}, "status": "completed",
        "scores": {"ES": 0.8, "P": 1.0, "P_fidelity": 0.5, "overall": 0.76}, "cost": {"agent": {"usd": 1.5}}}))
    s = score("x", n_tasks={"all": 125, "open": 77, "commercial": 48}, n_edits={"all": 80, "open": 64, "commercial": 16})
    rows = {r[0]: r for r in json.loads((tmp_path / "x" / "report" / "summary.json").read_text())["main_table"]["rows"]}
    assert rows["all"][1:7] == ["25.0", "25.0", "50.0", "100.0", "35.0", "3.00"] and rows["all"][12] == "2 / 125"
    assert rows["all"][7:12] == ["80.0", "100.0", "50.0", "76.0", "1.50"] and rows["all"][13] == "1 / 80"
    assert rows["open"][5] == "70.0" and rows["commercial"][5] == "0.0" and rows["commercial"][7] == "--"
    assert "| all |" in s["main_table"] and (tmp_path / "x" / "report" / "main_table.md").exists()


def test_dataset_config_and_manifest(monkeypatch, tmp_path):
    """the published dataset is the default; every editing checkpoint has its sha256; a missing token gives a clear error and
    gab passes no token itself (huggingface_hub's own HF_TOKEN / `hf auth login`)"""
    import sys, types
    from gab.setup import media
    from gab.edit.edit import manifest
    monkeypatch.delenv("GAB_HF_DATASET", raising=False)
    assert config.DEFAULTS["hf_dataset"] == "GameAnything/GameAnything-Bench" and media.repo_id() == "GameAnything/GameAnything-Bench"
    m = manifest(); assert len(m) == 36 and all(len(v["sha256"]) == 64 and v["size_bytes"] > 0 for v in m.values())
    seen = {}
    def fake_download(repo, path, repo_type=None, **kw):
        seen.update(repo=repo, path=path, repo_type=repo_type, kw=kw); raise RuntimeError("401 Client Error: Unauthorized")
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(hf_hub_download=fake_download))
    with pytest.raises(RuntimeError, match="HF_TOKEN or run `hf auth login`"): media._get("media/x/a.jpg", tmp_path / "a.jpg")
    assert seen == {"repo": "GameAnything/GameAnything-Bench", "path": "media/x/a.jpg", "repo_type": "dataset", "kw": {}}


def test_setup_disk_checks_warn_below_50_gb(monkeypatch, tmp_path):
    import shutil
    from types import SimpleNamespace
    for k in ("MEDIA_DIR", "EDIT_STORE", "RUNS_DIR"): monkeypatch.setenv("GAB_" + k, str(tmp_path / k.lower()))
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(total=1e12, used=0, free=30e9))
    class B:
        def _run(self, args, timeout=None): return SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n")
    C = cli.disk_checks(B())
    assert len(C) == 2 and C[0][0] == "disk media_dir" and not C[0][1] and "below 50 GB" in C[0][2]   # one line per file system
    assert C[1][0] == "disk docker root" and "30 GB free" in C[1][2]
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(total=1e12, used=0, free=400e9))
    assert all(ok for _, ok, _ in cli.disk_checks(None))

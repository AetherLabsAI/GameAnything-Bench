"""parallel plan and scheduler by game type (gab/run/sched.py)"""
import threading, time

from gab.run import sched


def fake_plan(monkeypatch, gpus=1, cores=10, ram=100.0, host="docker"):
    monkeypatch.setenv("GAB_HOST", host)
    from gab import config                               # the tests' numbers assume 2 trials per GPU, whatever gab.local.json says
    get = config.get
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: 2 if k == "trials_per_gpu" else get(k, *a, **kw))
    monkeypatch.setattr(sched, "machine", lambda: {"cores": cores, "ram_gb": ram, "gpus": ["G"] * gpus})
    real = sched.needs
    monkeypatch.setattr(sched, "needs", lambda g: {**real("flare"), "gpu": g == "g3d", "adapter": g != "nope"})


def test_plan_limits_by_type(monkeypatch):
    fake_plan(monkeypatch)
    p = sched.plan(["g3d"] * 6 + ["g2d"] * 6 + ["nope"] * 2)
    rows = {r["game"]: r for r in p["games"]}
    assert rows["g3d"]["max_parallel"] == 2 and rows["g3d"]["limited_by"] == "gpu"      # 1 GPU x 2 trials per GPU
    assert rows["g2d"]["max_parallel"] == 4 and rows["g2d"]["limited_by"] == "cpu"      # (10 - 2 reserved) / 2 cores
    assert rows["nope"]["max_parallel"] == 0                                             # no adapter: skipped


def test_scheduler_keeps_to_the_plan(monkeypatch):
    fake_plan(monkeypatch)
    p = sched.plan(["g3d"] * 6 + ["g2d"] * 6, workers=5); s = sched.Scheduler(p)
    peak, lock, now = {"g3d": 0, "g2d": 0, "all": 0}, threading.Lock(), {"g3d": 0, "g2d": 0, "all": 0}
    def job(g):
        with s.slot(g):
            with lock:
                now[g] += 1; now["all"] += 1
                for k in now: peak[k] = max(peak[k], now[k])
            time.sleep(0.05)
            with lock: now[g] -= 1; now["all"] -= 1
    ts = [threading.Thread(target=job, args=(g,)) for g in ["g3d"] * 6 + ["g2d"] * 6]
    for t in ts: t.start()
    for t in ts: t.join()
    assert peak["g3d"] <= 2 and peak["all"] <= 5 and peak["g2d"] >= 2


def test_unverified_pool_guard(monkeypatch, tmp_path):
    """a game whose gate items are missing / not ready gets no trials unless allow_unverified (2026-11-05, gate 2026-11-06)"""
    fake_plan(monkeypatch)
    assert sched.unverified_pool("flare") is None and sched.unverified_pool("no_such_game") is None
    assert sched.unverified_pool("cities_skylines") is None          # gate items verified and ready since 2026-11-15
    games = tmp_path / "benchmark" / "pools"; (games / "half").mkdir(parents=True)
    (games / "half" / "pool.yaml").write_text("gate: [A, B]\nalways: []\npool:\n  - {id: A, status: ready}\n  - {id: B, status: unverified}\n")
    monkeypatch.setattr(sched.config, "REPO", tmp_path)
    assert sched.unverified_pool("half") == "gate items not ready: B"
    p = sched.plan(["half"] * 3 + ["g2d"] * 2); rows = {r["game"]: r for r in p["games"]}
    assert rows["half"]["max_parallel"] == 0 and rows["half"]["limited_by"] == "unverified pool" and rows["g2d"]["max_parallel"] > 0
    p = sched.plan(["half"] * 3, allow_unverified=True)
    assert p["games"][0]["max_parallel"] > 0


def test_run_skips_unverified_games(monkeypatch, capsys):
    """`gab run` skips the trials of an unverified game, even a single one, with a log line; --allow-unverified keeps them"""
    from gab import cli
    class T:
        id, game = "T-1", "cities_skylines"
    monkeypatch.setattr(cli, "_trial_jobs", lambda a: [(T, "fine")])
    monkeypatch.setattr("gab.run.sched.unverified_pool", lambda g: "gate items not ready: X")   # Cities is ready since 2026-11-15
    called = []
    monkeypatch.setattr("gab.run.trial.run_trial", lambda *a, **k: called.append(a) or {"status": "done"})
    logs = []; monkeypatch.setattr(cli, "log", lambda m: logs.append(m))
    args = cli.argparse.Namespace(agent="stub", brief="v1", agent_cmd=None, model=None, framework="none", rounds=3, seed=0, workers=None, plan=False, yes=True,
                                  minutes=1.0, force=False, no_eval=True, no_preflight=True, agent_in_container=False, allow_unverified=False, name="x")
    cli.cmd_run(args)
    assert not called and any("skipping 1 trial(s) of cities_skylines" in l and "--allow-unverified" in l for l in logs)
    assert any("nothing to run" in l for l in logs)

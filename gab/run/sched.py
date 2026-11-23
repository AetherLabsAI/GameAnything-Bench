"""Parallel trials by game type: a plan the user confirms before a batch starts, then a scheduler that keeps to it. Every
trial needs RAM, CPU cores and, for a 3D game, a GPU slot; a trial starts when all three are free, so 2D games (CPU only) fill
the machine while 3D games are limited by the GPUs.

    gab run ... --workers N            at most N trials at once (default: the plan's suggestion)
    gab run ... --plan                 print the plan and exit
    gab run ... --yes                  start without asking (scripts); otherwise the plan is shown and must be confirmed
Per-trial needs and the GPU sharing are config keys (gab.local.json or GAB_*): trial_ram_gb (added to the game's mem_gb),
trial_cores, trial_cores_nogpu_3d (3D game in software GL), trials_per_gpu, host_reserve_ram_gb, host_reserve_cores."""
import importlib, json, os, shutil, subprocess, sys, threading
from collections import Counter

from .. import config


def machine():
    """this computer: cores, RAM (GB), NVIDIA GPUs"""
    ram = 0.0
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal:"): ram = int(line.split()[1]) / 1024 / 1024
    except OSError:
        try: ram = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 1024 ** 3   # macOS
        except Exception: pass
    gpus = []
    if shutil.which("nvidia-smi"):
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True)
        gpus = [l.strip() for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []
    if config.get("host") == "docker" and str(config.get("docker_gpus") or "auto") == "none": gpus = []
    return {"cores": os.cpu_count() or 1, "ram_gb": round(ram, 1), "gpus": gpus}


def needs(game):
    """per-trial needs of one game (from its adapter's requirements())"""
    from ..game.base import GAMES
    req = {"gpu": False, "mem_gb": 1.0, "adapter": game in GAMES}
    if game in GAMES:
        cls = importlib.import_module(GAMES[game]).Adapter
        try: req.update(cls.requirements(cls.__new__(cls)))
        except Exception: pass
    return {"adapter": req["adapter"], "gpu": bool(req.get("gpu")), "ram_gb": float(req.get("mem_gb") or 1.0) + float(config.get("trial_ram_gb")),
            "cores": int(config.get("trial_cores"))}


def unverified_pool(game):
    """why a game's gate probes cannot decide the gate yet, or None: no `gate:` list in pool.yaml, or a gate item that is not
    `ready` (a game stays out of batch runs until `gab selfcheck` verified its gate items; `gab run --allow-unverified`
    overrides). A game without a pool file is not judged here."""
    import yaml
    f = config.REPO / "benchmark" / "pools" / game / "pool.yaml"
    if not f.exists(): return None
    try: p = yaml.safe_load(f.read_text()) or {}
    except Exception as e: return f"pool.yaml unreadable: {e}"
    st = {i.get("id"): i.get("status") for i in (p.get("always") or []) + (p.get("pool") or []) if isinstance(i, dict)}
    if not p.get("gate"): return "no gate items (pool.yaml `gate:`)"
    bad = [i for i in p["gate"] if st.get(i) != "ready"]
    return f"gate items not ready: {', '.join(bad)}" if bad else None


def plan(games, workers=None, allow_unverified=False):
    """games: one entry per trial -> the plan (resources, per-game limits, suggested total); a game whose pool is not verified
    (unverified_pool) gets 0 trials, limited by "unverified pool", unless allow_unverified"""
    m = machine(); host = config.get("host"); cnt = Counter(games)
    ram = max(m["ram_gb"] - float(config.get("host_reserve_ram_gb")), 1); cores = max(m["cores"] - int(config.get("host_reserve_cores")), 1)
    gpu_slots = len(m["gpus"]) * int(config.get("trials_per_gpu"))
    rows = []
    for g, n in sorted(cnt.items()):
        nd = needs(g)
        if nd["gpu"] and not m["gpus"]: nd["cores"] = int(config.get("trial_cores_nogpu_3d"))   # software GL
        lim = {"ram": int(ram // nd["ram_gb"]), "cpu": int(cores // nd["cores"])}
        if nd["gpu"] and m["gpus"]: lim["gpu"] = gpu_slots
        if not nd["adapter"]: lim = {"no adapter yet": 0}
        elif not allow_unverified and unverified_pool(g): lim = {"unverified pool": 0}
        rows.append({"game": g, "trials": n, "type": ("3D (GPU)" if nd["gpu"] else "2D (CPU)") if nd["adapter"] else "?", **nd, "max_parallel": max(min(min(lim.values()), n), 0),
                     "limited_by": min(lim, key=lim.get)})
    suggest = max(sum(r["max_parallel"] for r in rows), 1)
    if rows:                                            # the mix shares one machine: suggest what fits when every game runs at once
        suggest = max(1, min(suggest, int(cores // max(min(r["cores"] for r in rows), 1)) if rows else 1))
    W = workers or suggest
    return {"host": host, "machine": m, "budget": {"ram_gb": round(ram, 1), "cores": cores, "gpu_slots": gpu_slots}, "games": rows,
            "suggested_workers": suggest, "workers": W, "trials": len(games)}


def show(p, minutes):
    m = p["machine"]
    print(f"\nParallel plan  (host {p['host']}; {p['trials']} trials)")
    gp = Counter(m["gpus"]); gp = ", ".join(f"{n} x {g}" for g, n in gp.items())
    print(f"  machine: {m['cores']} cores, {m['ram_gb']} GB RAM, GPUs: {gp or 'none (3D games use software OpenGL: slow)'}")
    print(f"  usable for trials: {p['budget']['cores']} cores, {p['budget']['ram_gb']} GB RAM, {p['budget']['gpu_slots']} GPU slots "
          f"({config.get('trials_per_gpu')} trials per GPU)")
    print(f"  {'game':16} {'type':9} {'trials':>6} {'RAM/trial':>9} {'cores':>5} {'max at once':>11}  limited by")
    for r in p["games"]:
        print(f"  {r['game']:16} {r['type']:9} {r['trials']:6d} {r['ram_gb']:8.1f}G {r['cores']:5d} {r['max_parallel']:11d}  {r['limited_by']}")
    print(f"  total at once: {p['workers']} (suggested {p['suggested_workers']}; change with --workers N)")
    skip = sum(r["trials"] for r in p["games"] if r["max_parallel"] == 0)
    if skip: print(f"  {skip} trials cannot run here (see 'limited by'); they are skipped")
    est = (p["trials"] - skip) * (minutes + 20) / max(p["workers"], 1) / 60
    print(f"  rough wall time: about {est:.1f} h ({minutes:.0f} min agent + ~20 min evaluation per trial)\n")


def confirm(p, minutes, yes=False, only_plan=False):
    show(p, minutes)
    if only_plan: return False
    if yes: return True
    if not sys.stdin.isatty():
        print("not started: confirm the plan with --yes (or run interactively)"); return False
    return input("Start with this plan? [y/N] ").strip().lower() in ("y", "yes")


class Scheduler:
    """runs jobs with at most `workers` at once and within the plan's RAM / core / GPU budget and per-game limits"""

    def __init__(self, p):
        self.p = p; self.cv = threading.Condition(); self.run = Counter(); self.busy = {"ram_gb": 0.0, "cores": 0, "gpu": 0, "n": 0}
        self.rows = {r["game"]: r for r in p["games"]}

    def _fits(self, g):
        r, b, B = self.rows[g], self.busy, self.p["budget"]
        if b["n"] >= self.p["workers"] or self.run[g] >= max(r["max_parallel"], 1): return False
        if b["n"] and (b["ram_gb"] + r["ram_gb"] > B["ram_gb"] or b["cores"] + r["cores"] > B["cores"]): return False
        return not (r["gpu"] and B["gpu_slots"] and b["gpu"] >= B["gpu_slots"])

    def reserve_first(self, g):
        """a resumable trial of game g will ask for a slot with first=True: count it before any job thread starts, so a fresh trial
        whose thread gets there first does not take the free slot (otherwise a restarted run starts fresh trials and leaves
        the interrupted ones waiting)"""
        with self.cv:
            if not hasattr(self, "firsts"): self.firsts = []
            if not hasattr(self, "reserved"): self.reserved = {}
            self.firsts.append(g); self.reserved[g] = self.reserved.get(g, 0) + 1

    def unreserve_first(self, g):
        """the reserved trial never asked for its slot (the run stopped first): drop the reservation, or the others wait forever"""
        with self.cv:
            if self.reserved.get(g):
                self.reserved[g] -= 1; self.firsts.remove(g); self.cv.notify_all()

    def slot(self, g, first=False):
        """first: a trial that continues an interrupted one (resume): it gets a free slot before trials that start fresh, so a
        restarted run finishes its resumable trials first (instead of waiting behind new starts at every restart)"""
        sched = self
        if not hasattr(sched, "firsts"): sched.firsts = []
        if not hasattr(sched, "reserved"): sched.reserved = {}
        class _S:
            def __enter__(s):
                with sched.cv:
                    if first:                            # already in firsts when reserved before the threads started
                        if sched.reserved.get(g): sched.reserved[g] -= 1
                        else: sched.firsts.append(g)
                    try: sched.cv.wait_for(lambda: sched._fits(g) and (first or not any(sched._fits(h) for h in sched.firsts)))
                    finally:
                        if first: sched.firsts.remove(g); sched.cv.notify_all()   # fresh trials waited for it (else slots stay
                                                                                         # idle until another slot is freed)
                    r = sched.rows[g]; sched.run[g] += 1; sched.busy["n"] += 1
                    sched.busy["ram_gb"] += r["ram_gb"]; sched.busy["cores"] += r["cores"]; sched.busy["gpu"] += int(r["gpu"])
            def __exit__(s, *exc):
                with sched.cv:
                    r = sched.rows[g]; sched.run[g] -= 1; sched.busy["n"] -= 1
                    sched.busy["ram_gb"] -= r["ram_gb"]; sched.busy["cores"] -= r["cores"]; sched.busy["gpu"] -= int(r["gpu"])
                    sched.cv.notify_all()
        return _S()


class InfraPolicy:
    """what `gab run` does with a trial that ends in infra_error (gab/run/infra.py): only transient causes -> run it again, up to
    `retries` more times, `backoff_s` x attempt later (outside its slot); anything systematic -> no NEW trial starts in this
    run (running ones finish), a banner at the end, exit non-zero. Attempts are recorded in trial.json by run_trial."""

    SKIP = {"status": "skipped", "error": "not started: systematic infra error in this run"}
    INTERRUPTED = {"status": "interrupted", "error": "not started: the run was stopped by the operator"}

    def __init__(self, retries=2, backoff_s=60, sleep=None):
        import time
        self.retries, self.backoff_s, self.sleep = retries, backoff_s, sleep or time.sleep
        self.stop = threading.Event(); self.lock = threading.Lock(); self.blocks = []    # [(trial key, infra block)]

    def run(self, key, attempt):
        """attempt(n) -> result dict of try n (it checks self.stop once it holds its slot)"""
        from . import infra
        r = None
        from .interrupt import requested
        for n in range(1, self.retries + 2):
            if requested(): return r or self.INTERRUPTED
            if self.stop.is_set(): return r or self.SKIP
            r = attempt(n)
            if (r or {}).get("status") != "infra_error" or requested(): return r     # stopped by the operator: no retry
            b = r.get("infra") or {}
            with self.lock: self.blocks.append((key, b))
            if not infra.transient_only(b):
                self.stop.set(); return r
            if n > self.retries: return r
            from ..util import log
            log(f"{key}: transient infra error {b.get('primary')}: attempt {n + 1} of {self.retries + 1} in {self.backoff_s * n:.0f} s")
            self.sleep(self.backoff_s * n)
        return r

    def banner(self):
        from . import infra
        return infra.banner(self.blocks)

"""`docker` backend (the open-source default): the user's own Docker Engine. One container per trial, from the game's image.

  acquire  -> `docker run -d --init --network none ...` of the game image (docker/images.yaml: pinned digest), main process
              gab-seat = the seat's private Xvfb on :101 (1024x768x24, or the adapter's larger screen: GAB_SCREEN); waits until the screen answers. GPU optional.
  exec     -> `docker exec <container> bash -c CMD` as the image's user `player` (the game's user); never feeds stdin.
           + the adapter's docker_mounts() (a commercial game's files from the host, read only: minecraft -> /mc/main).
  put      -> `docker cp` of a temp file (fast path for Seat.put / copy_in); copy_out -> `docker cp`.
  release  -> `docker rm -f -v` (the container and its anonymous volumes are gone: nothing of a trial survives it).

Images: docker/images.yaml maps game -> repository + digest (built from docker/<game>/Dockerfile, shared base layer). The game is
installed in the image at a fixed path (the adapter's install path), so the adapter never downloads at trial time.
A commercial game the user brings (Stardew Valley) is NOT in its image: the adapter's classmethod docker_mounts() names the
user's install (host path from gab config) and where it is bind-mounted read-only in the container (docker/stardew_valley/).

Config keys (gab.local.json via `gab setup`, or env GAB_<KEY>):
  docker              the docker CLI (default `docker`)            docker_host     DOCKER_HOST for it (empty = default)
  docker_images       YAML of images (default docker/images.yaml)  docker_image    image override for every game ("" = manifest)
  docker_tag          use this tag instead of the pinned digest    docker_pull     missing | always | never  (default missing)
  docker_gpus         auto | all | none | a --gpus value (auto = `--gpus all` when the game prefers a GPU AND docker reports an
                      nvidia runtime / CDI; else Mesa software GL. vglrun in the image falls back to software GL by itself)
  docker_network      none (default: the game has no network)      docker_shm      --shm-size (default 1g)
  docker_run_args     extra `docker run` arguments (shell syntax), e.g. "--memory 8g --cpus 4"
  docker_agent_uid    uid of an agent harness run inside the container (default 2000 = user `agent` of the image)
  docker_steam        hub (default) | off: games with `steam: true` in images.yaml use the machine's Steam hub container
                      (gab/host/steam.py: shared IPC + a loopback relay to the hub's client, the game library read only; the trial keeps
                      --network none)
Agent inside the container (`gab run --agent-in-container`): gab/agent/incontainer.py (mounts the trial's work dir, the agent home
and the bridge dir; the model key stays on the host behind gab/host/keyproxy.py).
"""
import importlib, json, os, shlex, subprocess, tempfile, time, uuid
from pathlib import Path

import yaml

from .. import config
from . import steam
from .base import HostBackend, Seat

DISPLAY = ":101"
HOME = "/home/player"


def _clean(s): return "".join(c if c.isalnum() or c in "_.-" else "-" for c in str(s))[:48].strip("-.") or "trial"


def manifest():
    p = Path(config.get("docker_images") or (config.REPO / "docker" / "images.yaml"))
    return yaml.safe_load(p.read_text()) if p.exists() else {"games": {}}


def image_ref(game, requirements=None):
    """game -> image reference: docker_image override > the manifest's repo@digest (or :docker_tag) > adapter's requirements image"""
    if config.get("docker_image"): return config.get("docker_image")
    m = manifest(); g = (m.get("games") or {}).get(game)
    if g:
        repo = g["repo"] if "/" in g["repo"] else f"{m['registry']}/{g['repo']}"
        tag = config.get("docker_tag")
        if tag: return f"{repo}:{tag}"
        if g.get("digest"): return f"{repo}@{g['digest']}"
        return f"{repo}:{g.get('tag') or m.get('tag') or 'latest'}"
    if requirements and requirements.get("image"): return requirements["image"]
    raise RuntimeError(f"no docker image for game {game!r} (docker/images.yaml has: {', '.join(m.get('games') or {})})")


class Backend(HostBackend):
    name = "docker"

    def __init__(self):
        self.docker = shlex.split(config.get("docker") or "docker")

    # ---- docker CLI
    def _run(self, args, timeout=600, **kw):
        try:
            return subprocess.run([*self.docker, *args], capture_output=True, text=True, errors="replace", timeout=timeout,
                                  env=config.docker_env(), **kw)
        except subprocess.TimeoutExpired as x:
            out = x.stdout.decode(errors="replace") if isinstance(x.stdout, bytes) else (x.stdout or "")
            return subprocess.CompletedProcess(x.cmd, 124, out, "timeout")

    def has_image(self, image): return self._run(["image", "inspect", "--format", "{{.Id}}", image], timeout=60).returncode == 0

    def pull(self, image):
        r = self._run(["pull", image], timeout=7200)
        if r.returncode:
            hint = (" (private package: `docker login ghcr.io` with a token that has read:packages, or build it: docker/build.sh)"
                    if "ghcr.io" in image and ("denied" in r.stderr or "unauthorized" in r.stderr) else "")
            raise RuntimeError(f"docker pull {image} failed{hint}: {r.stderr.strip()[-400:]}")
        return r

    def ensure_image(self, image):
        mode = config.get("docker_pull") or "missing"
        if mode == "always" or (mode == "missing" and not self.has_image(image)): self.pull(image)
        elif mode == "never" and not self.has_image(image): raise RuntimeError(f"image {image} is not present and docker_pull is never")

    GPU_LABEL = "org.gameanything-bench.gpu_dev"
    GPU_LOCK = "/tmp/gab-gpu-pick.lock"

    def gpu_count(self):
        try:
            r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=30)
            return sum(1 for l in r.stdout.splitlines() if l.startswith("GPU ")) if r.returncode == 0 else 0
        except (OSError, subprocess.SubprocessError): return 0

    def pick_gpu(self, n):
        """the least used of n GPUs, counted over every running gab container on this machine (all runs, all users: the
        label is machine-wide) -> device index"""
        r = self._run(["ps", "--filter", "label=org.gameanything-bench.gpu=1", "--format",
                       "{{.Label \"%s\"}}" % self.GPU_LABEL], timeout=60)
        use = [0] * n
        for l in (r.stdout or "").splitlines():
            l = l.strip()
            if l.isdigit() and int(l) < n: use[int(l)] += 1
            elif not l: use[0] += 1                  # a GPU container from before the fix (--gpus all): it renders on device 0
        # docker_gpu_exclude: devices kept for something else (e.g. a local model server)
        skip = {int(x) for x in (config.get("docker_gpu_exclude") or []) if str(x).isdigit()}
        ok = [i for i in range(n) if i not in skip] or list(range(n))
        return min(ok, key=lambda i: (use[i], i))

    def gpu_args(self, game):
        """docker args for the game's GPU. auto: a GPU game gets ONE device, the least used one
        (`--gpus device=N` + label), so trials spread over all GPUs; before, every container got `--gpus all` and rendered on
        GPU 0 (the other GPUs idle, the CPU overloaded). all = the old behaviour; none = no GPU; else a --gpus value."""
        g = str(config.get("docker_gpus") or "auto")
        if g in ("none", "0", "false", ""): return []
        if g == "auto":
            want = ((manifest().get("games") or {}).get(game) or {}).get("gpu", False)
            if not want: return []
            r = self._run(["info", "--format", "{{json .Runtimes}} {{json .CDISpecDirs}}"], timeout=60)
            if not (r.returncode == 0 and "nvidia" in r.stdout): return []
            n = self.gpu_count()
            if n <= 1: return ["--gpus", "all"]
            d = self.pick_gpu(n)
            return ["--gpus", f"device={d}", "--label", f"{self.GPU_LABEL}={d}"]
        return ["--gpus", g]

    # ---- HostBackend
    def acquire(self, requirements, project, wait_s=0):
        game = requirements["game"]
        image = image_ref(game, requirements); self.ensure_image(image)
        name = f"gab-{_clean(project)}-{uuid.uuid4().hex[:6]}"
        if steam.needs_steam(game) and (config.get("docker_steam") or "hub") != "off":
            steam.Hub(self).await_logon(game)                          # never under the machine-wide lock below
        import fcntl
        lock = open(self.GPU_LOCK, "a+")                               # pick + create under one machine-wide lock: two runs
        try: os.chmod(self.GPU_LOCK, 0o666)                            # starting at once must not pick the same GPU
        except OSError: pass
        fcntl.flock(lock, fcntl.LOCK_EX)
        try: gpu = self.gpu_args(game)
        except Exception: lock.close(); raise
        # seat screen = the adapter's screen when it does not fit gab-seat's 1024x768 (the 1280x720 games: their clicks and
        # x11grab of 1280x720 fall off a 1024x768 screen, and the window is clamped to it)
        from ..game import base as gbase
        try: A = importlib.import_module(gbase.GAMES[game]).Adapter
        except Exception: A = None
        w, h = getattr(A, "screen", None) or (1024, 768)
        # the game's own mounts (a commercial game the user brings: their install, read-only; never baked into an image)
        game_mounts = list(A.docker_mounts()) if A is not None and hasattr(A, "docker_mounts") else []
        scr = f"{w}x{h}x24" if w > 1024 or h > 768 else "1024x768x24"
        args = ["run", "-d", "--name", name, "--init", "--network", config.get("docker_network") or "none",
                "--shm-size", config.get("docker_shm") or "1g", "--pids-limit", "8192", "--security-opt", "no-new-privileges",
                "--label", "org.gameanything-bench.trial=1", "--label", f"org.gameanything-bench.project={project}",
                "--label", f"org.gameanything-bench.game={game}",
                # who started the container (user, gab process id): lets you find and clean up stale ones
                "--label", f"org.gameanything-bench.owner={os.environ.get('USER') or config.get('user')}",
                "--label", f"org.gameanything-bench.pid={os.getpid()}", "--label", f"org.gameanything-bench.gpu={1 if gpu else 0}",
                "-e", f"GAB_GPU={1 if gpu else 0}", "-e", f"GAB_SCREEN={scr}",
                # .NET (Roslyn's VBCSCompiler, msbuild) defaults to server GC: one GC thread per host core that
                # spin-wait when CPUs are oversubscribed -- idle compiler servers burnt
                # 5-7 cores each for 10+ min. Workstation GC is plenty for compiling a mod. No effect on non-.NET games.
                "-e", "DOTNET_gcServer=0", *gpu]
        for m in game_mounts + list(requirements.get("mounts") or []):   # (host path, container path, "rw" | "ro")
            args += ["-v", f"{m[0]}:{m[1]}:{m[2] if len(m) > 2 else 'rw'}"]
        hub = None
        if steam.needs_steam(game) and (config.get("docker_steam") or "hub") != "off":   # the machine's one Steam client (gab/host/steam.py)
            hub = steam.Hub(self); args += hub.trial_args(game)
        args += shlex.split(config.get("docker_run_args") or "") + [image]
        try: r = self._run(args, timeout=300)
        finally: lock.close()                                          # the container (and its GPU label) exists now
        if r.returncode: raise RuntimeError(f"docker run {image} failed: {r.stderr.strip()[-600:]}")
        seat = Seat(self, name, "1", DISPLAY, "", home=HOME,
                    info={"project": project, "game": game, "image": image, "container": name, "gpu": bool(gpu),
                          "mounts": [list(m) for m in game_mounts + list(requirements.get("mounts") or [])]})
        t0 = time.time()
        while True:                                                    # the seat screen (gab-seat) answers
            x = self.exec(seat, f"xdpyinfo -display {DISPLAY} >/dev/null 2>&1 && echo up", 30, {})
            if x.stdout.strip() == "up": break
            if time.time() - t0 > 60:
                logs = self._run(["logs", "--tail", "20", name], timeout=30)
                self.release(seat)
                raise RuntimeError(f"seat screen {DISPLAY} did not come up in {name}: {x.stderr[-300:]} {logs.stdout[-300:]} {logs.stderr[-300:]}")
            time.sleep(1)
        if hub:
            self.exec(seat, steam.Hub.trial_setup_cmd(), 30, {}, user="0"); seat.info["steam_hub"] = hub.name
        i = self._run(["image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=60)
        try: seat.info["image_digest"] = (json.loads(i.stdout) or [None])[0]
        except Exception: pass
        return seat

    def exec(self, seat, cmd, timeout, env, user=None, workdir=None):
        e = [x for k, v in (env or {}).items() for x in ("-e", f"{k}={v}")]
        u = ["-u", str(user)] if user is not None else []
        w = ["-w", workdir] if workdir else []
        return self._run(["exec", *u, *w, *e, seat.sandbox, "bash", "-c", cmd], timeout=timeout)

    def put_file(self, seat, path, data):
        """Seat.put fast path: docker cp of a temp file, then the seat user copies it into place (owned by the seat user)"""
        with tempfile.NamedTemporaryFile(delete=False, prefix="gab_put_") as f: f.write(data)
        try:
            os.chmod(f.name, 0o644); tmp = f"/tmp/.gab_put_{uuid.uuid4().hex}"
            if self._run(["cp", f.name, f"{seat.sandbox}:{tmp}"], timeout=600).returncode: return False
            q = shlex.quote(path)
            r = self.exec(seat, f"mkdir -p \"$(dirname {q})\" && cat {tmp} > {q}", 600, {})
            self.exec(seat, f"rm -f {tmp}", 60, {}, user="0")
            return r.returncode == 0
        finally: os.unlink(f.name)

    def copy_out(self, seat, src, dst):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        return self._run(["cp", f"{seat.sandbox}:{src}", str(dst)], timeout=600).returncode == 0

    def release(self, seat):
        self._run(["rm", "-f", "-v", seat.sandbox], timeout=120)

    def heartbeat(self, seat): pass

    # ---- `gab setup`
    def doctor(self, games=None):
        """-> list of (check, ok, detail) lines for `gab setup`"""
        out = []
        v = self._run(["version", "--format", "{{.Client.Version}} / server {{.Server.Version}}"], timeout=60)
        out.append(("docker", v.returncode == 0, (v.stdout or v.stderr).strip()[-200:]))
        if v.returncode: return out
        m = manifest()
        for g in games or sorted(m.get("games") or {}):
            try: ref = image_ref(g)
            except RuntimeError as e: out.append((f"image {g}", False, str(e))); continue
            out.append((f"image {g}", self.has_image(ref), ref + ("" if self.has_image(ref) else "   (not pulled: gab setup --pull " + g + ")")))
        gi = self._run(["info", "--format", "{{json .Runtimes}}"], timeout=60)
        out.append(("gpu (nvidia runtime)", "nvidia" in gi.stdout, "docker_gpus=" + str(config.get("docker_gpus")) +
                    ("" if "nvidia" in gi.stdout else ": no nvidia runtime -> software OpenGL (fine for the 2D games; slow 3D)")))
        return out

"""HostBackend interface: where a game runs. A backend leases a Seat; the game adapter runs every command through seat.exec /
seat.put / seat.copy_in / seat.copy_out, so the adapters never know where the game runs (the release backend: one docker
container per trial, gab/host/docker.py)."""
import base64, uuid, importlib, io, os, subprocess, tarfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Seat:
    backend: "HostBackend"
    sandbox: str | None          # container name (None for local)
    seat: str = "1"
    display: str = ":101"        # the seat's X display (games that use the seat's own screen)
    lease: str = ""              # a backend's lease string, passed as M2W_LEASE to every command (empty for docker)
    home: str = "/home/player"   # writable dir inside the sandbox
    info: dict = field(default_factory=dict)

    def exec(self, cmd, timeout=600, env=None, check=False):
        """bash -c cmd inside the seat's sandbox -> CompletedProcess (text). Never feeds stdin (some exec backends never
        deliver EOF on stdin)."""
        e = {"M2W_LEASE": self.lease, "GAMEANYTHING_LEASE": self.lease, **(env or {})}
        r = self.backend.exec(self, cmd, timeout, e)
        if check and r.returncode: raise RuntimeError(f"exec failed ({r.returncode}): {cmd[:200]}\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
        return r

    def put(self, path, data):
        """write a file inside the sandbox (base64 in argv, chunked below a 64 KB exec request limit)"""
        raw = data if isinstance(data, bytes) else data.encode()
        fast = getattr(self.backend, "put_file", None)          # docker backend: docker cp
        if fast and fast(self, path, raw): return
        b = base64.b64encode(raw).decode(); tmp = f"{path}.{uuid.uuid4().hex[:10]}"   # unique temp + atomic mv: trials sharing a
        # sandbox may write the same file at once (they would delete each other's .b64)
        self.exec(f"mkdir -p \"$(dirname '{path}')\" && : > '{tmp}.b64'", check=True)
        for i in range(0, len(b), 50_000): self.exec(f"echo {b[i:i + 50_000]} >> '{tmp}.b64'", check=True)
        self.exec(f"base64 -d '{tmp}.b64' > '{tmp}' && mv -f '{tmp}' '{path}' && rm -f '{tmp}.b64'", check=True)

    def copy_in(self, src, dst):
        """host file or directory -> dst inside the sandbox (dst = the target path itself); owned by the sandbox user"""
        src = Path(src); buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t: t.add(str(src), arcname=Path(dst).name)
        tmp = f"{self.home}/.gab_in_{os.getpid()}_{abs(hash(str(dst)))}.tgz"
        self.put(tmp, buf.getvalue())
        self.exec(f"mkdir -p \"$(dirname '{dst}')\" && rm -rf '{dst}' && tar -xzf '{tmp}' -C \"$(dirname '{dst}')\" && rm -f '{tmp}'", check=True)

    def copy_out(self, src, dst):
        """sandbox file or directory -> host path dst"""
        return self.backend.copy_out(self, src, dst)

    def release(self): self.backend.release(self)


class HostBackend:
    name = "base"

    def acquire(self, requirements: dict, project: str) -> Seat: raise NotImplementedError

    def exec(self, seat, cmd, timeout, env) -> subprocess.CompletedProcess: raise NotImplementedError

    def copy_out(self, seat, src, dst): raise NotImplementedError

    def release(self, seat): pass

    def heartbeat(self, seat): pass


def get(name=None) -> HostBackend:
    from .. import config
    name = name or config.get("host")
    return importlib.import_module(f"gab.host.{name}").Backend()

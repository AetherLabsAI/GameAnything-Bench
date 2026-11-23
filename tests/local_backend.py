"""Test helper: a host backend without a container (the game runs on this machine; no isolation). Seat home = $GAB_LOCAL_HOME
or ~/.gab/local; display :101 (an Xvfb the adapter starts if missing)."""
import os, shutil, subprocess
from pathlib import Path

from gab.host.base import HostBackend, Seat


class Backend(HostBackend):
    name = "local"

    def acquire(self, requirements, project, wait_s=0):
        home = os.environ.get("GAB_LOCAL_HOME", str(Path.home() / ".gab/local")); Path(home).mkdir(parents=True, exist_ok=True)
        return Seat(self, None, "1", os.environ.get("GAB_LOCAL_DISPLAY", ":101"), "", home=home, info={"project": project})

    def exec(self, seat, cmd, timeout, env):
        try: return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout, env={**os.environ, **env})
        except subprocess.TimeoutExpired as x: return subprocess.CompletedProcess(x.cmd, 124, "", "timeout")

    def copy_out(self, seat, src, dst):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if Path(src).is_dir() else shutil.copy)(src, dst); return True

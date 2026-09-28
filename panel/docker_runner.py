"""Run each bot as a Docker container instead of a local process (PANEL_RUNNER=docker).

The panel talks to the Docker Engine API over its unix socket (no extra dependency). Every bot gets one container,
named `ccxt-bot-<name>`, from the same image, with `user_data` bind-mounted at /freqtrade/user_data and host
networking, so everything that relies on 127.0.0.1 (bot APIs, the shared market-data gateway on :8090, FreqUI on
:8089) works exactly as in the process runner. Containers are created with restart policy `unless-stopped`: a crash
or a reboot brings a running bot back, a stop from the panel keeps it stopped.

Environment:
  PANEL_BOT_IMAGE        image for bot containers            (default ccxt-freqtrade:local)
  PANEL_HOST_USER_DATA   host path of user_data, needed when the panel itself runs in a container
  DOCKER_SOCKET          default /var/run/docker.sock
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import time
from datetime import datetime
from urllib.parse import quote

PREFIX = "ccxt-bot-"
CONTAINER_ROOT = "/freqtrade"


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


class DockerAPI:
    """Minimal Docker Engine API client over the unix socket."""

    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path or os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 30):
        conn = _UnixConnection(self.socket_path, timeout)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            data = json.loads(raw) if raw and resp.getheader("Content-Type", "").startswith("application/json") else raw
            return resp.status, data
        finally:
            conn.close()


class Container:
    """What the panel needs from a running bot, mirroring the psutil.Process calls it makes."""

    def __init__(self, backend: DockerBackend, info: dict):
        self._backend = backend
        self.info = info
        self.id = info["Id"]
        self.pid = info["Id"][:12]            # shown as "running"; not an OS pid

    def create_time(self) -> float:
        started = self.info["State"].get("StartedAt", "")      # e.g. 2026-09-28T04:12:33.123456789Z
        try:
            return datetime.fromisoformat(started[:19] + "+00:00").timestamp()
        except ValueError:
            return time.time()

    def memory_info(self):
        class _Mem:
            rss = self._backend.memory_bytes(self.id)
        return _Mem()


class DockerBackend:
    def __init__(self, image: str | None = None, host_user_data: str | None = None, api: DockerAPI | None = None,
                 local_user_data: str | None = None):
        self.api = api or DockerAPI()
        self.image = image or os.environ.get("PANEL_BOT_IMAGE", "ccxt-freqtrade:local")
        self.host_user_data = host_user_data or os.environ.get("PANEL_HOST_USER_DATA") or local_user_data
        self._mem: dict[str, tuple[float, int]] = {}

    @staticmethod
    def name(bot: dict) -> str:
        return PREFIX + bot["name"]

    def inspect(self, bot: dict) -> dict | None:
        status, data = self.api.request("GET", f"/containers/{quote(self.name(bot))}/json")
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"docker inspect {self.name(bot)}: {status} {data}")
        return data

    def find(self, bot: dict) -> Container | None:
        info = self.inspect(bot)
        if info and info["State"].get("Running"):
            return Container(self, info)
        return None

    def _spec(self, command: list[str]) -> dict:
        if not self.host_user_data:
            raise RuntimeError("PANEL_HOST_USER_DATA is not set: the host path of user_data is needed to mount it")
        return {
            "Image": self.image,
            "Entrypoint": [command[0]],
            "Cmd": command[1:],
            "WorkingDir": CONTAINER_ROOT,
            "Labels": {"ccxt.bot": "1"},
            "HostConfig": {
                "NetworkMode": "host",
                "Binds": [f"{self.host_user_data}:{CONTAINER_ROOT}/user_data"],
                "RestartPolicy": {"Name": "unless-stopped"},
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "20m", "max-file": "3"}},
                "Init": True,
            },
        }

    def start(self, bot: dict, command: list[str]) -> None:
        """Start the bot's container, recreating it when its command or image changed."""
        info = self.inspect(bot)
        spec = self._spec(command)
        image_id = self.image_id()
        if info and (info["Config"].get("Cmd") != spec["Cmd"] or info.get("Image") != image_id
                     or info["Config"].get("Entrypoint") != spec["Entrypoint"]
                     or info["HostConfig"].get("Binds") != spec["HostConfig"]["Binds"]):
            self.remove(bot)
            info = None
        if info is None:
            status, data = self.api.request("POST", f"/containers/create?name={quote(self.name(bot))}", spec)
            if status not in (200, 201):
                raise RuntimeError(f"docker create {self.name(bot)}: {status} {data}")
        status, data = self.api.request("POST", f"/containers/{quote(self.name(bot))}/start")
        if status not in (204, 304):
            raise RuntimeError(f"docker start {self.name(bot)}: {status} {data}")

    def image_id(self) -> str:
        """Current id of the image tag, so a rebuilt image (same tag) is noticed."""
        status, data = self.api.request("GET", f"/images/{quote(self.image, safe='')}/json")
        if status != 200:
            raise RuntimeError(f"docker image {self.image} not found ({status}); build it first")
        return data["Id"]

    def stop(self, bot: dict, grace: int) -> None:
        # SIGTERM, then SIGKILL after `grace` seconds; `unless-stopped` keeps it down until started again
        status, data = self.api.request("POST", f"/containers/{quote(self.name(bot))}/stop?t={grace}", timeout=grace + 30)
        if status not in (204, 304, 404):
            raise RuntimeError(f"docker stop {self.name(bot)}: {status} {data}")

    def remove(self, bot: dict) -> None:
        self.api.request("DELETE", f"/containers/{quote(self.name(bot))}?force=true")

    def memory_bytes(self, cid: str) -> int:
        t, value = self._mem.get(cid, (0.0, 0))
        if time.time() - t > 30:
            status, data = self.api.request("GET", f"/containers/{cid}/stats?stream=false&one-shot=true", timeout=10)
            if status == 200 and isinstance(data, dict):
                ms = data.get("memory_stats", {})
                value = int(ms.get("usage", 0) - ms.get("stats", {}).get("inactive_file", 0))
            self._mem[cid] = (time.time(), value)
        return value


def container_command(command: list[str], local_root: str, local_python: str, local_freqtrade: str) -> list[str]:
    """Translate the process runner's command (local paths) into the container's paths."""
    out = []
    for c in command:
        if c == local_freqtrade:
            out.append("freqtrade")
        elif c == local_python:
            out.append("python")
        elif c.startswith(local_root + "/"):
            out.append(CONTAINER_ROOT + c[len(local_root):])
        else:
            out.append(c)
    return out

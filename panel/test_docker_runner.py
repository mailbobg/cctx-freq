"""Tests for the Docker runner with a fake Docker Engine API (no Docker needed).

Run: .venv/bin/python -m pytest user_data/panel/test_docker_runner.py -q
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panel.docker_runner import DockerBackend, container_command  # noqa: E402


class FakeAPI:
    """In-memory stand-in for the Docker Engine API."""

    def __init__(self):
        self.images = {"img:1": "sha256:aaa", "img:2": "sha256:bbb", "ccxt-freqtrade:local": "sha256:ccc"}
        self.containers: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, body=None, timeout=30):
        self.calls.append((method, path))
        if path.startswith("/images/"):
            from urllib.parse import unquote
            tag = unquote(path[len("/images/"):-len("/json")])
            return (200, {"Id": self.images[tag]}) if tag in self.images else (404, {})
        name = path.split("/containers/")[-1].split("/")[0].split("?")[0]
        if method == "GET" and path.endswith("/json"):
            c = self.containers.get(name)
            return (200, c) if c else (404, {"message": "No such container"})
        if method == "POST" and path.startswith("/containers/create"):
            cname = path.split("name=")[1]
            self.containers[cname] = {"Id": "f" * 64, "Image": self.images[body["Image"]], "Config": {"Cmd": body["Cmd"], "Image": body["Image"],
                                      "Entrypoint": body["Entrypoint"]}, "HostConfig": body["HostConfig"],
                                      "State": {"Running": False, "StartedAt": ""}}
            return 201, {"Id": "f" * 64}
        if method == "POST" and path.endswith("/start"):
            c = self.containers.get(name)
            if not c:
                return 404, {}
            if c["State"]["Running"]:
                return 304, b""
            c["State"] = {"Running": True, "StartedAt": "2026-09-28T04:12:33.123456789Z"}
            return 204, b""
        if method == "POST" and "/stop" in path:
            c = self.containers.get(name)
            if not c:
                return 404, {}
            c["State"]["Running"] = False
            return 204, b""
        if method == "DELETE":
            self.containers.pop(name, None)
            return 204, b""
        if "/stats" in path:
            return 200, {"memory_stats": {"usage": 300 * 2**20, "stats": {"inactive_file": 20 * 2**20}}}
        raise AssertionError(f"unexpected call {method} {path}")


BOT = {"name": "x8", "port": 8081}
CMD = ["freqtrade", "trade", "-c", "user_data/bots/x8.json", "--logfile", "user_data/logs/x8.log"]


def test_start_creates_container_with_host_network_and_mount():
    api = FakeAPI()
    d = DockerBackend(image="img:1", host_user_data="/srv/ccxt/user_data", api=api)
    assert d.find(BOT) is None
    d.start(BOT, CMD)
    c = api.containers["ccxt-bot-x8"]
    assert c["Config"]["Entrypoint"] == ["freqtrade"] and c["Config"]["Cmd"] == CMD[1:]
    assert c["HostConfig"]["NetworkMode"] == "host"
    assert c["HostConfig"]["Binds"] == ["/srv/ccxt/user_data:/freqtrade/user_data"]
    assert c["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
    running = d.find(BOT)
    assert running is not None and running.pid == "f" * 12
    assert running.create_time() > 1.7e9
    assert running.memory_info().rss == 280 * 2**20


def test_start_is_idempotent_and_reuses_container():
    api = FakeAPI()
    d = DockerBackend(image="img:1", host_user_data="/u", api=api)
    d.start(BOT, CMD)
    d.start(BOT, CMD)                       # already running: start returns 304, nothing recreated
    assert sum(1 for m, p in api.calls if p.startswith("/containers/create")) == 1


def test_changed_command_or_image_recreates_container():
    api = FakeAPI()
    d = DockerBackend(image="img:1", host_user_data="/u", api=api)
    d.start(BOT, CMD)
    d.stop(BOT, 40)
    api.images["img:1"] = "sha256:rebuilt"      # same tag, rebuilt image
    d.start(BOT, CMD)
    assert ("DELETE", "/containers/ccxt-bot-x8?force=true") in api.calls
    assert api.containers["ccxt-bot-x8"]["Image"] == "sha256:rebuilt"


def test_stop_and_missing_container():
    api = FakeAPI()
    d = DockerBackend(image="img:1", host_user_data="/u", api=api)
    d.stop(BOT, 40)                          # no container yet: no error
    d.start(BOT, CMD)
    d.stop(BOT, 40)
    assert d.find(BOT) is None
    assert ("POST", "/containers/ccxt-bot-x8/stop?t=40") in api.calls


def test_missing_host_path_is_an_error():
    d = DockerBackend(image="img:1", host_user_data=None, api=FakeAPI())
    with pytest.raises(RuntimeError, match="PANEL_HOST_USER_DATA"):
        d.start(BOT, CMD)


def test_container_command_translates_local_paths():
    root, py, ft = "/Users/me/CCXT-Fr", "/Users/me/CCXT-Fr/.venv/bin/python", "/Users/me/CCXT-Fr/.venv/bin/freqtrade"
    assert container_command([ft, "trade", "-c", "user_data/bots/x8.json"], root, py, ft) == \
        ["freqtrade", "trade", "-c", "user_data/bots/x8.json"]
    assert container_command([py, f"{root}/user_data/cross_venue/service.py", "--config", "user_data/bots/f.json"],
                             root, py, ft) == ["python", "/freqtrade/user_data/cross_venue/service.py", "--config",
                                               "user_data/bots/f.json"]


def test_panel_uses_docker_runner(monkeypatch):
    """panel.start_bot / find_process / stop_bot go through Docker when PANEL_RUNNER=docker."""
    monkeypatch.setenv("PANEL_RUNNER", "docker")
    monkeypatch.setenv("PANEL_HOST_USER_DATA", "/srv/ccxt/user_data")
    import panel.panel as panel_mod
    panel_mod = importlib.reload(panel_mod)
    api = FakeAPI()
    panel_mod.docker.api = api
    monkeypatch.setattr(panel_mod, "save_state", lambda s: None)
    monkeypatch.setattr(panel_mod, "load_state", lambda: {})
    name = "x8"
    bot = panel_mod.bots()[name]
    panel_mod.start_bot(name)
    c = api.containers["ccxt-bot-x8"]
    assert c["Config"]["Entrypoint"] == ["freqtrade"]
    assert c["Config"]["Cmd"][:3] == ["trade", "-c", "user_data/bots/x8.json"]
    assert not any(str(panel_mod.ROOT) in a for a in c["Config"]["Cmd"])   # no host paths inside the container
    assert panel_mod.find_process(bot) is not None
    panel_mod.stop_bot(name)
    assert panel_mod.find_process(bot) is None
    monkeypatch.setenv("PANEL_RUNNER", "process")
    importlib.reload(panel_mod)

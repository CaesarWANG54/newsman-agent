"""Explicit local launcher operations; never installs or starts system services."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import http.client
import uuid

from newsman.runtime_identity import workspace_id

ROOT = Path(__file__).resolve().parents[3]


class StartupError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def boot_id():
    """Linux PID/start ticks are only meaningful within the same boot."""
    try:
        return str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
    except (OSError, ValueError):
        return None


def valid_session_identity(value):
    return (
        isinstance(value, dict)
        and set(value) == {"pid_namespace", "init_start_ticks"}
        and isinstance(value["pid_namespace"], str)
        and re.fullmatch(r"pid:\[[1-9][0-9]*\]", value["pid_namespace"]) is not None
        and isinstance(value["init_start_ticks"], str)
        and re.fullmatch(r"[0-9]+", value["init_start_ticks"]) is not None
    )


def session_identity():
    """WSL distro restarts can replace PID namespaces without rebooting the VM."""
    try:
        fields = Path("/proc/1/stat").read_text().rsplit(") ", 1)[1].split()
        value = {
            "pid_namespace": os.readlink("/proc/self/ns/pid"),
            "init_start_ticks": fields[19],
        }
        return value if valid_session_identity(value) else None
    except (OSError, ValueError, IndexError):
        return None


def identity(pid):
    base = Path(f"/proc/{pid}")
    try:
        fields = (base / "stat").read_text().rsplit(") ", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {
            "start_ticks": fields[19],
            "cwd": str((base / "cwd").resolve()),
            "exe": str((base / "exe").resolve()),
        }
    except (OSError, ValueError):
        return None


def owned(record, root=ROOT):
    return (
        record.get("workspace_id") == workspace_id(root)
        and ("boot_id" not in record or record["boot_id"] == boot_id())
        and (
            "session_identity" not in record
            or (
                valid_session_identity(record["session_identity"])
                and record["session_identity"] == session_identity()
            )
        )
        and record.get("identity") is not None
        and identity(record.get("pid", -1)) == record["identity"]
        and record["identity"]["cwd"] == str(root / "backend")
    )


def component_status(component):
    """Absence of a live PID is restartable only with a valid previous enrollment."""
    path = ROOT / f"logs/runtime/{component}.json"
    if not path.exists():
        return {"component": component, "state": "unregistered", "owned_running": False}
    try:
        record = json.loads(path.read_text())
        expected = record["identity"]
        valid = (
            record["component"] == component
            and record["workspace_id"] == workspace_id(ROOT)
            and isinstance(record["pid"], int)
            and record["pid"] > 0
            and str(expected["start_ticks"]).isdigit()
            and expected["cwd"] == str(ROOT / "backend")
            and expected["exe"] == str(Path(sys.executable).resolve())
        )
        if not valid:
            raise ValueError("Invalid enrollment")
        previous_session = record.get("session_identity")
        session_changed = False
        if "session_identity" in record:
            if not valid_session_identity(previous_session):
                raise ValueError("Invalid session enrollment")
            current_session = session_identity()
            if not valid_session_identity(current_session):
                raise ValueError("Session identity unavailable")
            session_changed = previous_session != current_session
        previous_boot = record.get("boot_id")
        boot_changed = False
        if "boot_id" in record:
            if not isinstance(previous_boot, str) or str(uuid.UUID(previous_boot)) != previous_boot:
                raise ValueError("Invalid boot enrollment")
            current_boot = boot_id()
            if current_boot is None:
                raise ValueError("Boot identity unavailable")
            boot_changed = previous_boot != current_boot
        if boot_changed or session_changed:
            return {
                "component": component,
                "state": "managed_missing",
                "owned_running": False,
                "pid": record["pid"],
                "restartable": True,
                "previous_boot": boot_changed,
                "previous_session": session_changed,
            }
        current = identity(record["pid"])
        state = (
            "owned_running"
            if owned(record)
            else "managed_missing"
            if current is None
            else "identity_conflict"
        )
        return {
            "component": component,
            "state": state,
            "owned_running": state == "owned_running",
            "pid": record["pid"],
            "restartable": state == "managed_missing",
        }
    except (OSError, ValueError, TypeError, KeyError):
        return {"component": component, "state": "ownership_unknown", "owned_running": False}


def api_health(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        data = json.loads(response.read())
        if (
            not isinstance(data, dict)
            or response.status != 200
            or data.get("product") != "newsman-agent"
            or data.get("role") != "backend"
            or data.get("workspace_id") != workspace_id(ROOT)
        ):
            raise StartupError("api_identity_conflict")
        return data
    except ConnectionRefusedError:
        return None
    except StartupError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        raise StartupError("api_port_unhealthy") from None
    finally:
        connection.close()


def operate(component, action, port=8000, frontend_port=3000):
    if action == "status":
        return component_status(component)
    directory = ROOT / "logs/runtime"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{component}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / f"{component}.json"
        state = component_status(component)
        if state["state"] in {"identity_conflict", "ownership_unknown"}:
            raise StartupError("ownership_unknown")
        record = json.loads(path.read_text()) if path.exists() else {}
        live = state["owned_running"]
        if action == "stop":
            if not live:
                return {"component": component, "status": "not_owned_or_stopped"}
            if not owned(record):
                raise StartupError("ownership_unknown")
            os.kill(record["pid"], signal.SIGTERM)
            for _ in range(50):
                if not owned(record):
                    return {"component": component, "status": "stopped"}
                time.sleep(0.1)
            raise RuntimeError("Owned process did not stop; no broad kill attempted")
        if live:
            return {"component": component, "status": "already_running"}
        commands = {
            "api": ["uvicorn", "newsman.main:app", "--host", "127.0.0.1", "--port", str(port)],
            "worker": [
                "celery",
                "-A",
                "newsman.celery_app",
                "worker",
                "--pool=prefork",
                "--concurrency=1",
                "--loglevel=INFO",
            ],
            "beat": [
                "celery",
                "-A",
                "newsman.celery_app",
                "beat",
                "--loglevel=INFO",
                "--schedule",
                str(directory / "celerybeat"),
            ],
        }
        current_boot = boot_id()
        if current_boot is None:
            raise StartupError("boot_identity_unavailable")
        current_session = session_identity()
        if not valid_session_identity(current_session):
            raise StartupError("session_identity_unavailable")
        with (directory / f"{component}.log").open("a") as output:
            process = subprocess.Popen(
                [sys.executable, "-m", *commands[component]],
                cwd=ROOT / "backend",
                stdout=output,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={
                    **os.environ,
                    **(
                        {"NEWSMAN_FRONTEND_ORIGIN": f"http://127.0.0.1:{frontend_port}"}
                        if component == "api"
                        else {}
                    ),
                },
            )
        record = {
            "component": component,
            "command": [sys.executable, "-m", *commands[component]],
            "workspace_id": workspace_id(),
            "boot_id": current_boot,
            "session_identity": current_session,
            "pid": process.pid,
            "identity": identity(process.pid),
        }
        temporary = path.with_suffix(".pending")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(path)
        time.sleep(0.2)
        if process.poll() is not None:
            raise StartupError("component_exited")
        return {"component": component, "status": "started", "pid": process.pid}


def ensure_stack(port=8000, frontend_port=3000, initialize=False, timeout=25):
    from newsman.config import get_settings
    from newsman.db import engine
    from newsman.services.runtime import runtime_status

    directory = ROOT / "logs/runtime"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "stack.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = get_settings()
        before = runtime_status(engine, settings)
        if before["database"] != "ready" or before["redis"] != "ready":
            raise StartupError("dependencies_unavailable")
        health = api_health(port)
        states = {role: component_status(role) for role in ("api", "worker", "beat")}
        needed = ["worker"] + (["beat"] if settings.daily_update_enabled else [])
        if health is None:
            needed.insert(0, "api")
        if any(s["state"] in {"ownership_unknown", "identity_conflict"} for s in states.values()):
            raise StartupError("ownership_unknown")
        if health and (
            not states["api"]["owned_running"] or states["api"].get("pid") != health.get("pid")
        ):
            raise StartupError("api_not_managed")
        observed_pids = set(before.get("worker_pids", {}).values())
        enrollment_allowed = (
            initialize
            and (
                before["worker"] == "not_observed"
                or (
                    states["worker"]["owned_running"]
                    and before["worker"] == "ready"
                    and observed_pids == {states["worker"].get("pid")}
                )
            )
        )
        for role in needed:
            if states[role]["state"] == "unregistered" and not enrollment_allowed:
                raise StartupError("component_not_enrolled")
        if before["worker"] == "ready" and (
            not states["worker"]["owned_running"]
            or (initialize and observed_pids - {states["worker"].get("pid")})
        ):
            raise StartupError("unmanaged_worker_observed")
        if health is None and states["api"]["state"] == "owned_running":
            raise StartupError("owned_api_not_ready")
        actions = []
        if (
            health
            and isinstance(health.get("daily_configured"), bool)
            and health["daily_configured"] != settings.daily_update_enabled
        ):
            if not states["api"]["owned_running"] or states["api"].get("pid") != health.get("pid"):
                raise StartupError("api_configuration_changed")
            actions.append(operate("api", "stop"))
            actions.append(operate("api", "start", port, frontend_port))
        for role in ("api", "worker"):
            if role in needed and states[role]["state"] != "owned_running":
                actions.append(operate(role, "start", port, frontend_port))
        if not settings.daily_update_enabled and states["beat"]["owned_running"]:
            actions.append(operate("beat", "stop"))
        deadline = time.monotonic() + timeout
        beat_started = False
        while time.monotonic() < deadline:
            current = runtime_status(engine, settings)
            health = api_health(port)
            worker = component_status("worker")
            worker_ready = (
                worker["owned_running"]
                and worker.get("pid") in current.get("worker_pids", {}).values()
            )
            api = component_status("api")
            api_ready = (
                health
                and api["owned_running"]
                and api.get("pid") == health.get("pid")
                and health.get("daily_configured") == settings.daily_update_enabled
            )
            if current["database"] != "ready" or current["redis"] != "ready":
                raise StartupError("dependencies_unavailable")
            if api_ready and worker_ready:
                if settings.daily_update_enabled and not component_status("beat")["owned_running"]:
                    if beat_started:
                        raise StartupError("component_exited")
                    actions.append(operate("beat", "start", port, frontend_port))
                    beat_started = True
                beat = component_status("beat")
                if not settings.daily_update_enabled or beat["owned_running"]:
                    return {
                        "status": "ready",
                        "worker": "ready",
                        "daily_configured": settings.daily_update_enabled,
                        "api_management": "owned",
                        "scheduler": "process_observed"
                        if settings.daily_update_enabled
                        else "disabled_configured",
                        "components": {role: component_status(role) for role in states},
                        "actions": actions,
                    }
            time.sleep(0.2)
        raise StartupError("managed_readiness_timeout")


def ensure_reader(port=8000, frontend_port=3000, initialize=False, timeout=25):
    """Open existing news through the managed API without waiting for background work."""
    from newsman.config import get_settings
    from newsman.db import engine
    from newsman.services.runtime import runtime_status

    directory = ROOT / "logs/runtime"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "stack.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = get_settings()
        current = runtime_status(engine, settings, probe_worker=False)
        if current["database"] != "ready":
            raise StartupError("dependencies_unavailable")
        health = api_health(port)
        api = component_status("api")
        if api["state"] in {"ownership_unknown", "identity_conflict"}:
            raise StartupError("ownership_unknown")
        if health and (not api["owned_running"] or api.get("pid") != health.get("pid")):
            raise StartupError("api_not_managed")
        if api["state"] == "unregistered" and not initialize:
            raise StartupError("component_not_enrolled")
        actions = []
        if health is None and not api["owned_running"]:
            actions.append(operate("api", "start", port, frontend_port))
        deadline = time.monotonic() + timeout
        while True:
            health = api_health(port)
            api = component_status("api")
            if health:
                if not api["owned_running"] or api.get("pid") != health.get("pid"):
                    raise StartupError("api_not_managed")
                return {
                    "status": "reader_ready",
                    "database": "ready",
                    "worker": current["worker"],
                    "redis": current["redis"],
                    "daily_configured": settings.daily_update_enabled,
                    "api_management": "owned",
                    "components": {role: component_status(role) for role in ("api", "worker", "beat")},
                    "actions": actions,
                }
            if api["state"] in {"ownership_unknown", "identity_conflict"}:
                raise StartupError("ownership_unknown")
            if time.monotonic() >= deadline:
                raise StartupError("managed_readiness_timeout")
            time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "ensure", "ensure-reader", "start", "status", "stop"])
    parser.add_argument("component", nargs="?", choices=["api", "worker", "beat"])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--settings-file")
    parser.add_argument("--initialize", action="store_true")
    args = parser.parse_args()
    if args.settings_file:
        os.environ["NEWSMAN_SETTINGS_FILE"] = str(Path(args.settings_file).resolve())
    try:
        if args.action == "ensure":
            print(json.dumps(ensure_stack(args.port, args.frontend_port, args.initialize)))
            return 0
        if args.action == "ensure-reader":
            print(json.dumps(ensure_reader(args.port, args.frontend_port, args.initialize)))
            return 0
        if args.action == "check":
            from newsman.config import get_settings
            from newsman.db import engine
            from newsman.services.runtime import runtime_status

            result = runtime_status(engine, get_settings())
            print(json.dumps(result))
            return 0 if result["database"] == result["redis"] == "ready" else 2
        if not args.component:
            parser.error("component required")
        print(json.dumps(operate(args.component, args.action, args.port, args.frontend_port)))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_code": exc.code
                    if isinstance(exc, StartupError)
                    else "runtime_operation_failed",
                    "message": "Runtime operation failed; inspect local dependency/ownership state.",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

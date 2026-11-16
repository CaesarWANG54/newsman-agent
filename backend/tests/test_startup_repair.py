"""Launcher safety checks; no real processes, database, broker, or HTTP requests."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from newsman.config import get_settings


@pytest.fixture
def explicit_profile(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("NEWSMAN_SETTINGS_FILE", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_explicit_profile_ignores_dotenv(explicit_profile, monkeypatch):
    from pydantic_settings.sources import DotEnvSettingsSource
    from newsman.config import Settings

    dotenv = Mock(side_effect=AssertionError("Must not load .env"))
    monkeypatch.setitem(Settings.model_config, "env_file", "must-not-load.env")
    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_file", dotenv)
    explicit_profile.write_text(
        json.dumps(
            {
                "DATABASE_URL": "sqlite:///explicit-test",
                "REDIS_URL": "redis://explicit-fixture.invalid:1/9",
                "NEWSMAN_DAILY_UPDATE_ENABLED": False,
            }
        ),
        encoding="utf-8",
    )
    settings = get_settings()
    assert settings.database_url == "sqlite:///explicit-test"
    assert settings.redis_url == "redis://explicit-fixture.invalid:1/9"
    assert settings.daily_update_enabled is False
    dotenv.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [None, "not-json", "[]", '{"NEWSMAN_DAILY_UPDATE_HOUR":99,"OPENAI_API_KEY":"not-for-output"}'],
)
def test_bad_explicit_profile_fails_without_fallback_or_values(explicit_profile, value):
    if value is not None:
        explicit_profile.write_text(value, encoding="utf-8")
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert str(error.value) == "Explicit NewsMan settings file invalid; input values withheld"
    assert error.value.__suppress_context__


@pytest.fixture
def manager(monkeypatch, tmp_path):
    # The actual manager is POSIX-only. Run these tests with the existing WSL Python.
    if os.name != "posix":
        pytest.skip("POSIX process manager; validated by the WSL-focused command")
    from newsman.cli import manage_runtime

    monkeypatch.setattr(manage_runtime, "ROOT", tmp_path)
    monkeypatch.setattr(manage_runtime, "workspace_id", lambda *args: "fixture-workspace")
    original_owned = manage_runtime.owned
    monkeypatch.setattr(
        manage_runtime,
        "owned",
        lambda record: original_owned(record, root=tmp_path),
    )
    return manage_runtime


def enrollment(manager):
    return {
        "component": "worker",
        "workspace_id": "fixture-workspace",
        "pid": 8765,
        "identity": {
            "cwd": str(manager.ROOT / "backend"),
            "exe": str(Path(os.sys.executable).resolve()),
            "start_ticks": "1234",
        },
    }


@pytest.mark.parametrize(
    "state",
    [
        "unregistered",
        "owned_running",
        "managed_missing",
        "identity_conflict",
        "ownership_unknown",
        "malformed",
    ],
)
def test_component_enrollment_never_adopts_unknown_pid(manager, monkeypatch, state):
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    record = enrollment(manager)
    current = record["identity"] if state == "owned_running" else None
    if state == "identity_conflict":
        current = {**record["identity"], "start_ticks": "9876"}
    if state == "ownership_unknown":
        record["workspace_id"] = "foreign"
    if state != "unregistered":
        path.write_text("[]" if state == "malformed" else json.dumps(record))
    monkeypatch.setattr(manager, "identity", lambda pid: current)
    result = manager.component_status("worker")
    assert result["state"] == ("ownership_unknown" if state == "malformed" else state)
    assert result["owned_running"] is (state == "owned_running")
    assert result.get("restartable", False) is (state == "managed_missing")


def test_corrupt_record_stop_fails_with_ownership_diagnostic(manager):
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    path.write_text("broken-json")
    with pytest.raises(manager.StartupError, match="ownership_unknown"):
        manager.operate("worker", "stop")
    assert path.read_text() == "broken-json"


@pytest.mark.parametrize("registered,pid", [(False, 123), (True, 456)])
def test_live_api_requires_managed_matching_pid(manager, monkeypatch, settings, registered, pid):
    from newsman.services import runtime

    settings.daily_update_enabled = False
    monkeypatch.setattr("newsman.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        runtime,
        "runtime_status",
        lambda *args: {"database": "ready", "redis": "ready", "worker": "ready"},
    )
    monkeypatch.setattr(manager, "api_health", lambda port: {"pid": pid, "daily_configured": False})
    monkeypatch.setattr(
        manager,
        "component_status",
        lambda role: {
            "owned_running": registered,
            "state": "owned_running" if registered else "unregistered",
            "pid": 123,
        },
    )
    start = Mock(side_effect=AssertionError("Must not mutate unknown API"))
    monkeypatch.setattr(manager, "operate", start)
    with pytest.raises(manager.StartupError, match="api_not_managed"):
        manager.ensure_stack()
    start.assert_not_called()


@pytest.mark.parametrize("body", [[], {"product": "foreign"}])
def test_api_identity_rejects_malformed_or_foreign_http(manager, monkeypatch, body):
    connection = Mock()
    connection.getresponse.return_value = SimpleNamespace(status=200, read=lambda: json.dumps(body))
    monkeypatch.setattr(manager.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    with pytest.raises(manager.StartupError, match="api_identity_conflict"):
        manager.api_health(4194)
    connection.close.assert_called_once()


def test_corrupt_scheduler_record_is_diagnostic_not_http_crash(manager, settings):
    from newsman.services.runtime import runtime_status
    from unittest.mock import patch

    path = manager.ROOT / "logs/runtime/beat.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]")
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="sqlite"),
        connect=Mock(side_effect=RuntimeError("fixture offline")),
    )
    with patch("redis.Redis.from_url", side_effect=RuntimeError("fixture offline")):
        result = runtime_status(engine, settings)
    assert result["components"]["beat"]["state"] == "ownership_unknown"
    assert result["scheduler"] == "not_observed"


@pytest.mark.parametrize("observed_pid", [123, 999])
def test_readiness_requires_the_owned_worker_pid(manager, monkeypatch, settings, observed_pid):
    from newsman.services import runtime

    settings.daily_update_enabled = False
    monkeypatch.setattr("newsman.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        runtime,
        "runtime_status",
        lambda *args: {
            "database": "ready",
            "redis": "ready",
            "worker": "ready",
            "worker_pids": {"fixture": observed_pid},
        },
    )
    monkeypatch.setattr(manager, "api_health", lambda port: {"pid": 123, "daily_configured": False})
    monkeypatch.setattr(
        manager,
        "component_status",
        lambda role: {
            "owned_running": role != "beat",
            "state": "owned_running" if role != "beat" else "unregistered",
            "pid": 123,
        },
    )
    monkeypatch.setattr(manager.time, "monotonic", Mock(side_effect=[0, 0, 2]))
    monkeypatch.setattr(manager.time, "sleep", lambda *args: None)
    mutate = Mock(side_effect=AssertionError("Do not duplicate a live managed process"))
    monkeypatch.setattr(manager, "operate", mutate)
    if observed_pid == 123:
        assert manager.ensure_stack(timeout=1)["status"] == "ready"
    else:
        with pytest.raises(manager.StartupError, match="managed_readiness_timeout"):
            manager.ensure_stack(timeout=1)
    mutate.assert_not_called()


@pytest.mark.parametrize("initialize", [False, True])
@pytest.mark.parametrize("worker_observed", [False, True])
def test_partial_enrollment_can_finish_only_with_initialize(
    manager, monkeypatch, settings, initialize, worker_observed
):
    from newsman.services import runtime

    settings.daily_update_enabled = True
    states = {
        "api": {"state": "owned_running", "owned_running": True, "pid": 123},
        "worker": {"state": "owned_running", "owned_running": True, "pid": 456},
        "beat": {"state": "unregistered", "owned_running": False},
    }
    before = {
        "database": "ready", "redis": "ready",
        "worker": "ready" if worker_observed else "not_observed",
        "worker_pids": {"fixture": 456} if worker_observed else {},
    }
    observed = {**before, "worker": "ready", "worker_pids": {"fixture": 456}}
    monkeypatch.setattr("newsman.config.get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "runtime_status", Mock(side_effect=[before, observed]))
    monkeypatch.setattr(manager, "api_health", lambda port: {"pid": 123, "daily_configured": True})
    monkeypatch.setattr(manager, "component_status", lambda role: states[role])

    def start(role, action, *args):
        assert (role, action) == ("beat", "start")
        states[role] = {"state": "owned_running", "owned_running": True, "pid": 789}
        return {"component": role, "status": "started"}

    mutation = Mock(side_effect=start)
    monkeypatch.setattr(manager, "operate", mutation)
    if initialize:
        result = manager.ensure_stack(initialize=True)
        assert result["status"] == "ready"
        mutation.assert_called_once()
    else:
        with pytest.raises(manager.StartupError, match="component_not_enrolled"):
            manager.ensure_stack()
        mutation.assert_not_called()


@pytest.mark.parametrize("conflict", ["ownership_unknown", "identity_conflict", "foreign_worker"])
def test_initialize_refuses_foreign_or_damaged_partial_stack(manager, monkeypatch, settings, conflict):
    from newsman.services import runtime

    settings.daily_update_enabled = True
    monkeypatch.setattr("newsman.config.get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "runtime_status", lambda *args: {
        "database": "ready", "redis": "ready", "worker": "ready",
        "worker_pids": {"foreign": 999},
    })
    monkeypatch.setattr(manager, "api_health", lambda port: {"pid": 123})
    monkeypatch.setattr(manager, "component_status", lambda role: {
        "state": conflict if role == "beat" and conflict != "foreign_worker" else "owned_running",
        "owned_running": role != "beat" or conflict == "foreign_worker",
        "pid": 123,
    })
    mutation = Mock(side_effect=AssertionError("No conflicting process may be mutated"))
    monkeypatch.setattr(manager, "operate", mutation)
    with pytest.raises(manager.StartupError):
        manager.ensure_stack(initialize=True)
    mutation.assert_not_called()


@pytest.mark.parametrize("api_running", [False, True])
def test_reader_starts_only_api_without_probing_worker_or_requiring_redis(
    manager, monkeypatch, settings, api_running
):
    from newsman.services import runtime

    monkeypatch.setattr("newsman.config.get_settings", lambda: settings)
    probe = Mock(return_value={"database": "ready", "redis": "unavailable", "worker": "not_probed"})
    monkeypatch.setattr(runtime, "runtime_status", probe)
    state = {"state": "owned_running" if api_running else "managed_missing",
             "owned_running": api_running, "pid": 123}
    monkeypatch.setattr(manager, "component_status", lambda role: state if role == "api" else {
        "state": "identity_conflict", "owned_running": False,
    })
    monkeypatch.setattr(manager, "api_health", lambda port: {"pid": 123} if state["owned_running"] else None)

    def start(role, action, *args):
        assert (role, action) == ("api", "start")
        state.update(state="owned_running", owned_running=True)
        return {"component": "api", "status": "started"}

    mutation = Mock(side_effect=start)
    monkeypatch.setattr(manager, "operate", mutation)
    result = manager.ensure_reader()
    assert result["status"] == "reader_ready"
    assert result["worker"] == "not_probed"
    assert result["redis"] == "unavailable"
    assert mutation.call_count == (0 if api_running else 1)
    assert probe.call_args.kwargs == {"probe_worker": False}


@pytest.mark.parametrize("scenario", ["database_missing", "foreign_api", "unregistered", "corrupt"])
def test_reader_rejects_invalid_database_or_api_before_mutation(manager, monkeypatch, settings, scenario):
    from newsman.services import runtime

    monkeypatch.setattr("newsman.config.get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "runtime_status", lambda *args, **kwargs: {
        "database": "migration_required" if scenario == "database_missing" else "ready",
        "redis": "ready", "worker": "not_probed",
    })
    state = "ownership_unknown" if scenario == "corrupt" else "unregistered"
    monkeypatch.setattr(manager, "component_status", lambda role: {"state": state, "owned_running": False})
    monkeypatch.setattr(manager, "api_health", lambda port: {"pid": 999} if scenario == "foreign_api" else None)
    mutation = Mock(side_effect=AssertionError("Do not mutate unknown API"))
    monkeypatch.setattr(manager, "operate", mutation)
    with pytest.raises(manager.StartupError):
        manager.ensure_reader()
    mutation.assert_not_called()


def test_new_boot_never_signals_reused_pid(manager, monkeypatch):
    record = enrollment(manager)
    record["boot_id"] = "11111111-1111-1111-1111-111111111111"
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    monkeypatch.setattr(manager, "boot_id", lambda: "22222222-2222-2222-2222-222222222222")
    # Even identical PID/start ticks from another boot cannot prove ownership.
    monkeypatch.setattr(manager, "identity", lambda pid: record["identity"])
    kill = Mock(side_effect=AssertionError("Never signal a process from another boot"))
    monkeypatch.setattr(manager.os, "kill", kill)
    result = manager.component_status("worker")
    assert result["state"] == "managed_missing"
    assert result["restartable"] is True
    assert manager.operate("worker", "stop")["status"] == "not_owned_or_stopped"
    kill.assert_not_called()
    assert json.loads(path.read_text()) == record


@pytest.mark.parametrize("record_boot,current_boot,expected", [
    ("11111111-1111-1111-1111-111111111111", "11111111-1111-1111-1111-111111111111", "owned_running"),
    ("11111111-1111-1111-1111-111111111111", None, "ownership_unknown"),
    ("bad-boot", "22222222-2222-2222-2222-222222222222", "ownership_unknown"),
    (None, "22222222-2222-2222-2222-222222222222", "ownership_unknown"),
])
def test_boot_enrollment_validation(manager, monkeypatch, record_boot, current_boot, expected):
    record = enrollment(manager)
    record["boot_id"] = record_boot
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    monkeypatch.setattr(manager, "identity", lambda pid: record["identity"])
    monkeypatch.setattr(manager, "boot_id", lambda: current_boot)
    assert manager.component_status("worker")["state"] == expected


def test_worker_start_uses_separate_control_process_and_enrolls_boot(manager, monkeypatch):
    monkeypatch.setattr(manager, "boot_id", lambda: "11111111-1111-1111-1111-111111111111")
    process = SimpleNamespace(pid=123, poll=lambda: None)
    launch = Mock(return_value=process)
    monkeypatch.setattr(manager.subprocess, "Popen", launch)
    monkeypatch.setattr(manager, "identity", lambda pid: enrollment(manager)["identity"])
    monkeypatch.setattr(manager.time, "sleep", lambda *args: None)
    assert manager.operate("worker", "start")["status"] == "started"
    command = launch.call_args.args[0]
    assert "--pool=prefork" in command
    assert "--concurrency=1" in command
    record = json.loads((manager.ROOT / "logs/runtime/worker.json").read_text())
    assert record["boot_id"] == manager.boot_id()


def test_reader_initialize_enrolls_only_api(manager, monkeypatch, settings):
    from newsman.services import runtime

    monkeypatch.setattr("newsman.config.get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "runtime_status", lambda *args, **kwargs: {
        "database": "ready", "redis": "ready", "worker": "not_probed",
    })
    state = {"state": "unregistered", "owned_running": False}
    monkeypatch.setattr(manager, "component_status", lambda role: state if role == "api" else {
        "state": "unregistered", "owned_running": False,
    })
    monkeypatch.setattr(manager, "api_health", lambda port: {"pid": 123} if state["owned_running"] else None)

    def start(role, action, *args):
        assert (role, action) == ("api", "start")
        state.update(state="owned_running", owned_running=True, pid=123)
        return {"component": role, "status": "started"}

    mutation = Mock(side_effect=start)
    monkeypatch.setattr(manager, "operate", mutation)
    assert manager.ensure_reader(initialize=True)["status"] == "reader_ready"
    mutation.assert_called_once()


def test_new_boot_restarts_enrolled_component_without_adopting_old_pid(manager, monkeypatch):
    record = enrollment(manager)
    record["boot_id"] = "11111111-1111-1111-1111-111111111111"
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    monkeypatch.setattr(manager, "boot_id", lambda: "22222222-2222-2222-2222-222222222222")
    foreign_identity = {**record["identity"], "start_ticks": "5555"}
    new_identity = {**record["identity"], "start_ticks": "9999"}
    monkeypatch.setattr(manager, "identity", lambda pid: foreign_identity if pid == record["pid"] else new_identity)
    kill = Mock(side_effect=AssertionError("Reused PID must remain untouched"))
    monkeypatch.setattr(manager.os, "kill", kill)
    monkeypatch.setattr(manager.subprocess, "Popen", Mock(return_value=SimpleNamespace(pid=7777, poll=lambda: None)))
    monkeypatch.setattr(manager.time, "sleep", lambda *args: None)
    result = manager.operate("worker", "start")
    assert result["pid"] == 7777
    replacement = json.loads(path.read_text())
    assert replacement["pid"] == 7777
    assert replacement["identity"] == new_identity
    assert replacement["boot_id"] == manager.boot_id()
    assert manager.component_status("worker")["state"] == "owned_running"
    kill.assert_not_called()


@pytest.mark.parametrize("current_session", [
    {"pid_namespace": "pid:[4026532000]", "init_start_ticks": "1234"},
    {"pid_namespace": "pid:[4026531836]", "init_start_ticks": "5678"},
    {"pid_namespace": "pid:[4026532000]", "init_start_ticks": "5678"},
])
def test_same_boot_new_session_recovers_without_signaling_reused_pid(manager, monkeypatch, current_session):
    record = enrollment(manager)
    record["boot_id"] = "11111111-1111-1111-1111-111111111111"
    record["session_identity"] = {"pid_namespace": "pid:[4026531836]", "init_start_ticks": "1234"}
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    monkeypatch.setattr(manager, "boot_id", lambda: record["boot_id"])
    monkeypatch.setattr(manager, "session_identity", lambda: current_session)
    new_identity = {**record["identity"], "start_ticks": "9999"}
    # A different session can even recycle the identical PID and start ticks.
    monkeypatch.setattr(manager, "identity", lambda pid: record["identity"] if pid == record["pid"] else new_identity)
    kill = Mock(side_effect=AssertionError("Never signal the old session's reused PID"))
    monkeypatch.setattr(manager.os, "kill", kill)
    result = manager.component_status("worker")
    assert result["state"] == "managed_missing"
    assert result["previous_boot"] is False
    assert result["previous_session"] is True
    assert manager.owned(record) is False
    assert manager.operate("worker", "stop")["status"] == "not_owned_or_stopped"
    assert json.loads(path.read_text()) == record
    monkeypatch.setattr(manager.subprocess, "Popen", Mock(return_value=SimpleNamespace(pid=7777, poll=lambda: None)))
    monkeypatch.setattr(manager.time, "sleep", lambda *args: None)
    assert manager.operate("worker", "start")["pid"] == 7777
    replacement = json.loads(path.read_text())
    assert replacement["pid"] == 7777
    assert replacement["identity"] == new_identity
    assert replacement["session_identity"] == current_session
    assert replacement["boot_id"] == record["boot_id"]
    assert manager.component_status("worker")["state"] == "owned_running"
    kill.assert_not_called()


@pytest.mark.parametrize("invalid_session", [
    None, [], {},
    {"pid_namespace": "pid:[4026531836]"},
    {"pid_namespace": "net:[4026531836]", "init_start_ticks": "1234"},
    {"pid_namespace": "pid:[0]", "init_start_ticks": "1234"},
    {"pid_namespace": "pid:[4026531836]", "init_start_ticks": 1234},
    {"pid_namespace": "pid:[4026531836]", "init_start_ticks": "１２３４"},
    {"pid_namespace": "pid:[4026531836]", "init_start_ticks": "1234", "extra": True},
])
def test_invalid_session_enrollment_is_conservative_even_after_boot_change(manager, monkeypatch, invalid_session):
    record = enrollment(manager)
    record["boot_id"] = "11111111-1111-1111-1111-111111111111"
    record["session_identity"] = invalid_session
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    monkeypatch.setattr(manager, "boot_id", lambda: "22222222-2222-2222-2222-222222222222")
    monkeypatch.setattr(manager, "identity", lambda pid: record["identity"])
    launch = Mock(side_effect=AssertionError("Never start from damaged enrollment"))
    monkeypatch.setattr(manager.subprocess, "Popen", launch)
    kill = Mock(side_effect=AssertionError("Never signal from damaged enrollment"))
    monkeypatch.setattr(manager.os, "kill", kill)
    assert manager.component_status("worker")["state"] == "ownership_unknown"
    for action in ("start", "stop"):
        with pytest.raises(manager.StartupError, match="ownership_unknown"):
            manager.operate("worker", action)
    launch.assert_not_called()
    kill.assert_not_called()
    assert json.loads(path.read_text()) == record


def test_unreadable_current_session_never_launches_or_signals(manager, monkeypatch):
    record = enrollment(manager)
    record["session_identity"] = {"pid_namespace": "pid:[4026531836]", "init_start_ticks": "1234"}
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    monkeypatch.setattr(manager, "session_identity", lambda: None)
    monkeypatch.setattr(manager, "identity", lambda pid: record["identity"])
    launch = Mock(side_effect=AssertionError("Session unavailable"))
    monkeypatch.setattr(manager.subprocess, "Popen", launch)
    kill = Mock(side_effect=AssertionError("Session unavailable"))
    monkeypatch.setattr(manager.os, "kill", kill)
    assert manager.component_status("worker")["state"] == "ownership_unknown"
    assert manager.owned(record) is False
    with pytest.raises(manager.StartupError, match="ownership_unknown"):
        manager.operate("worker", "stop")
    path.unlink()
    with pytest.raises(manager.StartupError, match="session_identity_unavailable"):
        manager.operate("worker", "start")
    launch.assert_not_called()
    kill.assert_not_called()
    assert not path.exists()


def test_legacy_enrollment_does_not_guess_session_migration(manager, monkeypatch):
    record = enrollment(manager)
    record["boot_id"] = "11111111-1111-1111-1111-111111111111"
    path = manager.ROOT / "logs/runtime/worker.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    monkeypatch.setattr(manager, "boot_id", lambda: record["boot_id"])
    session = Mock(side_effect=AssertionError("Legacy enrollment has no session evidence"))
    monkeypatch.setattr(manager, "session_identity", session)
    monkeypatch.setattr(manager, "identity", lambda pid: {**record["identity"], "start_ticks": "9876"})
    assert manager.component_status("worker")["state"] == "identity_conflict"
    with pytest.raises(manager.StartupError, match="ownership_unknown"):
        manager.operate("worker", "start")
    assert json.loads(path.read_text()) == record
    session.assert_not_called()


@pytest.mark.parametrize("failure", [None, "namespace_read", "stat_read", "short_stat", "namespace_format", "ticks_format"])
def test_session_identity_proc_reading_and_failures(manager, monkeypatch, failure):
    ticks = "bad" if failure == "ticks_format" else "12345"
    fields = ["S", *["0"] * 18, ticks, "0"]
    stat = "1 (init (with spaces)) " + " ".join(fields)
    if failure == "short_stat":
        stat = "1 (init) S 0"
    read = Mock(side_effect=OSError("offline fixture")) if failure == "stat_read" else Mock(return_value=stat)
    namespace = "net:[4026531836]" if failure == "namespace_format" else "pid:[4026531836]"
    readlink = Mock(side_effect=OSError("offline fixture")) if failure == "namespace_read" else Mock(return_value=namespace)
    monkeypatch.setattr(manager.Path, "read_text", read)
    monkeypatch.setattr(manager.os, "readlink", readlink)
    result = manager.session_identity()
    if failure is None:
        assert result == {"pid_namespace": "pid:[4026531836]", "init_start_ticks": "12345"}
        readlink.assert_called_once_with("/proc/self/ns/pid")
    else:
        assert result is None

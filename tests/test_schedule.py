import importlib
import plistlib

import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    """在临时数据目录下加载一次 ui.server，避免污染真实配置。"""
    monkeypatch.setenv("DOUYIN_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DOUYIN_DESKTOP_MODE", raising=False)
    monkeypatch.delenv("DOUYIN_SCHEDULE_LAUNCHER", raising=False)

    import ui.server

    return importlib.reload(ui.server)


def test_scheduled_command_carries_catch_up_flags(server) -> None:
    command = server.scheduled_run_command(7, 5)

    assert command[-3:] == ["--catch-up", "--not-before", "07:05"]


def test_plist_runs_on_schedule_and_every_thirty_minutes(server) -> None:
    body = server.plist_body(7, 5)

    assert "<key>StartCalendarInterval</key>" in body
    assert "<key>Hour</key><integer>7</integer>" in body
    assert "<key>Minute</key><integer>5</integer>" in body
    assert "<key>StartInterval</key>\n  <integer>1800</integer>" in body
    # 保存/修复计划任务不应立刻触发一次补跑。
    assert "RunAtLoad" not in body
    assert "<key>DOUYIN_SCHEDULE_SAVED_AT</key>" in body
    assert "<string>--catch-up</string>" in body
    assert "<string>--not-before</string>" in body
    assert "<string>07:05</string>" in body


def test_plist_keeps_the_env_from_the_configured_data_dir(server, tmp_path) -> None:
    body = server.plist_body(8, 0)

    assert f"<string>{tmp_path}</string>" in body
    assert "DOUYIN_DATA_DIR" in body


def _prepare_repair(server, tmp_path, monkeypatch, schedule_data_dir, calls) -> None:
    plist = tmp_path / "schedule.plist"
    plist.write_bytes(
        plistlib.dumps({"EnvironmentVariables": {"DOUYIN_DATA_DIR": schedule_data_dir}})
    )
    monkeypatch.setattr(server, "PACKAGED", True)
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setattr(server, "PLIST", plist)
    monkeypatch.setattr(server, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(server, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(server, "current_schedule", lambda: {"present": True, "hour": 7, "minute": 0})
    monkeypatch.setattr(server, "schedule_command_matches", lambda hour, minute: False)
    monkeypatch.setattr(server, "apply_schedule", lambda hour, minute: calls.append((hour, minute)))


def test_repair_schedule_keeps_another_installs_task(server, tmp_path, monkeypatch) -> None:
    """另一套安装（不同数据目录）的定时任务不能被顺手改写。"""
    calls = []
    _prepare_repair(server, tmp_path, monkeypatch, str(tmp_path / "other-data"), calls)

    server.repair_schedule_once()

    assert calls == []


def test_repair_schedule_repoints_its_own_task(server, tmp_path, monkeypatch) -> None:
    calls = []
    _prepare_repair(server, tmp_path, monkeypatch, str(tmp_path / "data"), calls)

    server.repair_schedule_once()

    assert calls == [(7, 0)]

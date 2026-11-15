import importlib
import plistlib
import subprocess

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


@pytest.mark.parametrize("hour,minute", [(8, 5), (8, 0), (8, 30), (23, 59)])
def test_linux_schedule_runs_on_time_and_keeps_catch_up(server, monkeypatch, hour, minute) -> None:
    installed = []
    unrelated = "15 3 * * * /usr/bin/backup"
    old_task = "*/30 * * * * old-command # douyin-fire-local-schedule"

    def fake_run(command, **kwargs):
        if command == ["/usr/bin/crontab", "-l"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{unrelated}\n{old_task}\n")
        assert command == ["/usr/bin/crontab", "-"]
        installed.append(kwargs["input"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/crontab")
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    monkeypatch.setattr(server, "scheduled_run_command", lambda h, m: ["/app/run", "--catch-up", "--not-before", f"{h:02d}:{m:02d}"])

    server._apply_linux_schedule(hour, minute)

    lines = installed[0].splitlines()
    assert unrelated in lines
    assert "old-command" not in installed[0]
    tasks = [line for line in lines if "# douyin-fire-local-schedule" in line]
    assert any(line.startswith("*/30 * * * * ") for line in tasks)
    for line in tasks:
        assert f"--catch-up --not-before {hour:02d}:{minute:02d}" in line

    # Exactly one launch at the chosen minute, including :00/:30 overlaps.
    matching = [line for line in tasks if line.startswith(f"{minute} {hour} * * * ")
                or (minute in (0, 30) and line.startswith("*/30 * * * * "))]
    assert len(matching) == 1
    assert len(tasks) == (1 if minute in (0, 30) else 2)


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

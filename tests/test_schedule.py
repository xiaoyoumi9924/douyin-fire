import importlib

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

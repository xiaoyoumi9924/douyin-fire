import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

import app.main as main_module
from app.models import Message, Settings, Target, TaskConfig

ZONE = "Asia/Shanghai"
NOW = datetime(2026, 10, 24, 9, 30, tzinfo=ZoneInfo(ZONE))


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        task_config_path=tmp_path / "config.json",
        storage_state=None,
        cookie="[]",
        headless=True,
        browser_path=None,
        artifacts_dir=tmp_path / "artifacts",
        trace=False,
    )


def _task() -> TaskConfig:
    return TaskConfig(
        task_id="daily-streak",
        timezone=ZONE,
        targets=(Target(name="好友A", messages=(Message(type="text", content="测试"),)),),
        stickers={},
        interval_min=0,
        interval_max=0,
        continue_on_error=True,
    )


def _write_result(artifacts: Path, finished_at: str, *, dry_run: bool = False) -> None:
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "result.json").write_text(
        json.dumps({"dry_run": dry_run, "finished_at": finished_at, "results": []}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(main_module, "_task_now", lambda _timezone: NOW)


def test_skips_before_the_configured_time(tmp_path, fixed_now) -> None:
    assert main_module._catch_up_skip_reason(tmp_path, ZONE, "23:00") == "还没到 23:00"


def test_runs_when_nothing_happened_today(tmp_path, fixed_now) -> None:
    assert main_module._catch_up_skip_reason(tmp_path, ZONE, "07:00") is None


def test_skips_when_today_already_finished(tmp_path, fixed_now) -> None:
    _write_result(tmp_path, "2026-10-24T07:04:00+08:00")

    assert main_module._catch_up_skip_reason(tmp_path, ZONE, "07:00") == "今天已经完成过一次运行"


def test_dry_run_today_does_not_count(tmp_path, fixed_now) -> None:
    _write_result(tmp_path, "2026-10-24T07:04:00+08:00", dry_run=True)

    assert main_module._catch_up_skip_reason(tmp_path, ZONE, "07:00") is None


def test_yesterdays_run_does_not_count(tmp_path, fixed_now) -> None:
    _write_result(tmp_path, "2026-10-23T07:04:00+08:00")

    assert main_module._catch_up_skip_reason(tmp_path, ZONE, "07:00") is None


def test_corrupt_result_file_does_not_block_catch_up(tmp_path, fixed_now) -> None:
    (tmp_path / "result.json").write_text("{not json", encoding="utf-8")

    assert main_module._catch_up_skip_reason(tmp_path, ZONE, "07:00") is None


def test_attempt_limit_stops_retrying(tmp_path, fixed_now) -> None:
    (tmp_path / main_module.CATCH_UP_STATE_FILE).write_text(
        json.dumps({"date": "2026-10-24", "attempts": main_module.CATCH_UP_MAX_ATTEMPTS}),
        encoding="utf-8",
    )

    reason = main_module._catch_up_skip_reason(tmp_path, ZONE, "07:00")

    assert reason is not None and "已尝试 3 次" in reason


def test_attempt_counter_resets_on_a_new_day(tmp_path, fixed_now) -> None:
    (tmp_path / main_module.CATCH_UP_STATE_FILE).write_text(
        json.dumps({"date": "2026-10-23", "attempts": 3}), encoding="utf-8"
    )

    assert main_module._catch_up_skip_reason(tmp_path, ZONE, "07:00") is None
    assert main_module._record_catch_up_attempt(tmp_path, ZONE) == 1


def test_record_attempt_increments(tmp_path, fixed_now) -> None:
    assert main_module._record_catch_up_attempt(tmp_path, ZONE) == 1
    assert main_module._record_catch_up_attempt(tmp_path, ZONE) == 2
    assert json.loads((tmp_path / main_module.CATCH_UP_STATE_FILE).read_text()) == {
        "date": "2026-10-24",
        "attempts": 2,
    }


@pytest.mark.asyncio
async def test_catch_up_returns_early_without_opening_browser(monkeypatch, tmp_path, fixed_now) -> None:
    settings = _settings(tmp_path)
    _write_result(settings.artifacts_dir, "2026-10-24T07:04:00+08:00")
    monkeypatch.setattr(main_module, "load_settings", lambda _env=None: settings)
    monkeypatch.setattr(main_module, "load_task", lambda _settings: _task())
    monkeypatch.setattr(main_module, "_configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "open_douyin", MagicMock(side_effect=AssertionError("不应打开浏览器")))

    assert await main_module.run(catch_up=True, not_before="07:00") == 0


@pytest.mark.asyncio
async def test_catch_up_runs_and_records_the_attempt(monkeypatch, tmp_path, fixed_now) -> None:
    settings = _settings(tmp_path)

    @asynccontextmanager
    async def broken_open_douyin(_settings):
        raise RuntimeError("浏览器启动失败")
        yield

    monkeypatch.setattr(main_module, "load_settings", lambda _env=None: settings)
    monkeypatch.setattr(main_module, "load_task", lambda _settings: _task())
    monkeypatch.setattr(main_module, "_configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "open_douyin", broken_open_douyin)
    monkeypatch.setattr(main_module, "_write_results", MagicMock())
    monkeypatch.setattr(main_module, "_notify_dingtalk", AsyncMock())
    monkeypatch.setattr(main_module, "_notify_webhook", AsyncMock())
    monkeypatch.setattr(main_module, "send_macos_notification", AsyncMock())

    with pytest.raises(RuntimeError, match="浏览器启动失败"):
        await main_module.run(catch_up=True, not_before="07:00")

    assert json.loads((settings.artifacts_dir / main_module.CATCH_UP_STATE_FILE).read_text()) == {
        "date": "2026-10-24",
        "attempts": 1,
    }


@pytest.mark.asyncio
async def test_manual_run_ignores_the_catch_up_gate(monkeypatch, tmp_path, fixed_now) -> None:
    settings = _settings(tmp_path)
    _write_result(settings.artifacts_dir, "2026-10-24T07:04:00+08:00")

    @asynccontextmanager
    async def broken_open_douyin(_settings):
        raise RuntimeError("浏览器启动失败")
        yield

    monkeypatch.setattr(main_module, "load_settings", lambda _env=None: settings)
    monkeypatch.setattr(main_module, "load_task", lambda _settings: _task())
    monkeypatch.setattr(main_module, "_configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "open_douyin", broken_open_douyin)
    monkeypatch.setattr(main_module, "_write_results", MagicMock())
    monkeypatch.setattr(main_module, "_notify_dingtalk", AsyncMock())
    monkeypatch.setattr(main_module, "_notify_webhook", AsyncMock())
    monkeypatch.setattr(main_module, "send_macos_notification", AsyncMock())

    with pytest.raises(RuntimeError, match="浏览器启动失败"):
        await main_module.run()

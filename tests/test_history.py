from pathlib import Path
import json
import os
from datetime import datetime

import pytest

from app.history import AlreadyRunningError, History, message_id, run_lock
from app.models import Message, Target


def test_history_persists_success(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    history = History(path)
    key = history.key("task", history.run_date("Asia/Shanghai"), "好友A", "0-abc")

    history.mark_success(key)

    assert History(path).contains(key)


def test_corrupt_history_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="发送历史损坏"):
        History(path)


def test_run_lock_reclaims_a_lock_left_by_a_dead_process(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text("4194304", encoding="utf-8")

    with run_lock(path):
        assert path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_run_lock_rejects_second_process(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"

    with run_lock(path):
        with pytest.raises(AlreadyRunningError):
            with run_lock(path):
                pass

    assert not path.exists()


def test_friend_added_after_a_run_stays_pending(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    run_history = tmp_path / "run-history.jsonl"
    run_history.write_text(
        json.dumps(
            {
                "task_id": "task",
                "dry_run": False,
                "finished_at": "2026-10-25T07:00:00+08:00",
                "results": [
                    {"target": "好友01", "status": "success", "sent": 1, "error": None},
                    {"target": "好友02", "status": "success", "sent": 1, "error": None},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    # 新好友是在当天那次运行之后才加的，旧记录因此被视为 stale。
    config_mtime = datetime.fromisoformat("2026-10-25T09:53:00+08:00").timestamp()
    os.utime(config, (config_mtime, config_mtime))

    targets = tuple(
        Target(name=name, messages=(Message(type="text", content="续火花"),))
        for name in ("好友A", "好友B", "羽毛球")
    )
    history = History(tmp_path / "history.json")
    history.merge_legacy_run_history(
        run_history,
        "task",
        "2026-10-25",
        "Asia/Shanghai",
        targets,
        config_path=config,
    )

    def key(index: int) -> str:
        message = targets[index].messages[0]
        return history.key("task", "2026-10-25", targets[index].name, message_id(0, message))

    # 旧记录覆盖到的人仍按“待核对”保守处理，避免重复发送。
    assert history.status(key(0)) == "unknown"
    assert history.status(key(1)) == "unknown"
    # 当天新加、旧记录从未覆盖的人保持待发送，当天仍然可以发。
    assert history.status(key(2)) is None
    assert history.target_status([key(2)]) == "pending"

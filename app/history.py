from __future__ import annotations

import json
import hashlib
import os
from dataclasses import asdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import Message, Target


class AlreadyRunningError(RuntimeError):
    pass


class History:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries = self._load()

    def run_date(self, timezone: str) -> str:
        try:
            return datetime.now(ZoneInfo(timezone)).date().isoformat()
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"未知时区: {timezone}，请安装 tzdata 或修正配置") from exc

    def key(self, task_id: str, run_date: str, target: str, message_id: str) -> str:
        return f"{task_id}:{run_date}:{target}:{message_id}"

    def contains(self, key: str) -> bool:
        return self.status(key) in {"success", "unknown"}

    def status(self, key: str) -> str | None:
        if key not in self.entries:
            return None
        entry = self.entries[key]
        if not isinstance(entry, dict):
            # Unknown legacy values must fail closed to avoid duplicate sends.
            return "unknown"
        status = entry.get("status")
        return status if isinstance(status, str) and status in {"success", "failed", "unknown"} else "unknown"

    def target_status(self, keys: Sequence[str]) -> str:
        statuses = [self.status(key) for key in keys]
        if not statuses:
            return "pending"
        if "unknown" in statuses:
            return "unknown"
        if all(status == "success" for status in statuses):
            return "success"
        if "failed" in statuses:
            return "failed"
        return "pending"

    def merge_legacy_run_history(
        self,
        path: Path,
        task_id: str,
        run_date: str,
        timezone: str,
        targets: Sequence[Target],
        config_path: Path | None = None,
    ) -> None:
        """Merge same-day target-only results in memory without rewriting the JSONL."""
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"未知时区: {timezone}，请安装 tzdata 或修正配置") from exc

        aliases = {f"好友{index + 1:02d}": index for index in range(len(targets))}
        aliases.update({target.name: index for index, target in enumerate(targets)})
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return

        legacy_records: list[tuple[datetime, dict, str]] = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("task_id") != task_id or record.get("dry_run"):
                continue
            finished_at = record.get("finished_at")
            if not isinstance(finished_at, str):
                continue
            try:
                finished = datetime.fromisoformat(finished_at)
                if finished.tzinfo is None:
                    finished = finished.replace(tzinfo=zone)
                if finished.astimezone(zone).date().isoformat() != run_date:
                    continue
            except (ValueError, OverflowError):
                continue

            legacy_records.append((finished, record, finished_at))

        if not legacy_records:
            return

        if config_path is not None:
            config_modified = datetime.fromtimestamp(config_path.stat().st_mtime, zone)
            # File mtimes can be rounded to a coarse filesystem tick; treat a
            # two-second boundary window as ambiguous to avoid mapping a
            # reordered friend list by an apparently equal timestamp.
            timestamp_tolerance = timedelta(seconds=2)
            stale_records = [
                item for item in legacy_records
                if item[0] <= config_modified + timestamp_tolerance
            ]
            if stale_records:
                # Old run-history targets are privacy-redacted as "好友NN".
                # If the current friend/message config changed after an old run,
                # the old row number may refer to another person. Only use rows
                # from runs made with the current config; uncovered targets fail
                # closed as unknown instead of being resent.
                eligible_records = [
                    item for item in legacy_records
                    if item[0] > config_modified + timestamp_tolerance
                ]
                inferred: dict[str, tuple[str, str]] = {}
                represented: set[int] = set()

                for _, record, current_finished_at in eligible_records:
                    results = record.get("results")
                    if not isinstance(results, list):
                        continue
                    for result in results:
                        if not isinstance(result, dict):
                            continue
                        index = aliases.get(str(result.get("target") or ""))
                        if index is None:
                            continue
                        status = result.get("status")
                        if not isinstance(status, str) or status not in {"success", "failed", "unknown"}:
                            continue
                        represented.add(index)
                        target = targets[index]
                        sent = result.get("sent", 0)
                        if isinstance(sent, bool) or not isinstance(sent, int) or sent < 0:
                            sent = 0
                        for message_index, message in enumerate(target.messages):
                            if status == "success" or (status == "failed" and message_index < sent):
                                inferred_status = "success"
                            else:
                                inferred_status = "unknown"
                            key = self.key(task_id, run_date, target.name, message_id(message_index, message))
                            previous = inferred.get(key)
                            if previous is None or (previous[0] != "success" and inferred_status == "success"):
                                inferred[key] = (inferred_status, current_finished_at)

                for index, target in enumerate(targets):
                    if index in represented:
                        continue
                    for message_index, message in enumerate(target.messages):
                        key = self.key(task_id, run_date, target.name, message_id(message_index, message))
                        inferred.setdefault(key, ("unknown", ""))

                for key, (status, current_finished_at) in inferred.items():
                    if key in self.entries:
                        continue
                    self.entries[key] = {
                        "status": status,
                        "migrated_from_legacy": True,
                        "legacy_config_changed": True,
                        **({"finished_at": current_finished_at} if current_finished_at else {}),
                    }
                return

        for _, record, finished_at in legacy_records:
            results = record.get("results")
            if not isinstance(results, list):
                continue
            for result in results:
                if not isinstance(result, dict):
                    continue
                index = aliases.get(str(result.get("target") or ""))
                if index is None:
                    continue
                target = targets[index]
                status = result.get("status")
                sent = result.get("sent", 0)
                if isinstance(sent, bool) or not isinstance(sent, int) or sent < 0:
                    sent = 0
                if status == "success":
                    message_indices = range(len(target.messages))
                elif status == "failed":
                    message_indices = range(min(sent, len(target.messages)))
                elif status == "unknown":
                    message_indices = range(len(target.messages))
                else:
                    continue

                for message_index in message_indices:
                    key = self.key(task_id, run_date, target.name, message_id(message_index, target.messages[message_index]))
                    if key in self.entries:
                        continue
                    migrated_status = "unknown" if status == "unknown" else "success"
                    self.entries[key] = {
                        "status": migrated_status,
                        "finished_at": finished_at,
                        "migrated_from_legacy": True,
                    }

    def reserve(self, key: str) -> None:
        self.entries[key] = {"status": "unknown", "started_at": datetime.now().astimezone().isoformat()}
        self._save()

    def mark_success(self, key: str) -> None:
        self.entries[key] = {"status": "success", "finished_at": datetime.now().astimezone().isoformat()}
        self._save()

    def mark_failed(self, key: str, error: str | None = None) -> None:
        entry = {"status": "failed", "finished_at": datetime.now().astimezone().isoformat()}
        if error:
            entry["error"] = str(error)[:500]
        self.entries[key] = entry
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def _load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"发送历史格式不正确，为避免重复发送，任务已停止: {self.path}")
            return value
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"发送历史损坏，为避免重复发送，任务已停止: {self.path}") from exc


def message_id(index: int, message: Message) -> str:
    """Return the stable ID used by existing history entries for a configured message."""
    payload = json.dumps(asdict(message), ensure_ascii=False, sort_keys=True, default=str)
    return f"{index}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


@contextmanager
def run_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise AlreadyRunningError(f"已有任务正在运行；如确认没有进程，请删除 {path}") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

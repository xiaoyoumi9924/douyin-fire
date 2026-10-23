from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from app.config import ConfigError

DEFAULT_ACCOUNTS_FILE = Path("config") / "accounts.json"
ACCOUNT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")


@dataclass(frozen=True)
class Account:
    """一个多账号条目。env_file 相对项目根目录解析。"""

    id: str
    enabled: bool
    env_file: Path
    name: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class AccountPaths:
    task_config: Path
    storage_state: Path | None
    artifacts_dir: Path


def validate_account_id(value: object) -> str:
    if not isinstance(value, str) or not ACCOUNT_ID_RE.fullmatch(value):
        raise ConfigError("账号 ID 只能包含字母、数字、短横线或下划线，长度为 1–32 个字符")
    return value


def load_account_records(path: Path = DEFAULT_ACCOUNTS_FILE) -> list[Account] | None:
    """加载所有账号；None 表示未启用注册表，空列表表示注册表中没有账号。"""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"多账号配置不是有效 JSON: {path}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), list):
        raise ConfigError(f"多账号配置缺少 accounts 数组: {path}")

    accounts: list[Account] = []
    seen: set[str] = set()
    for index, item in enumerate(raw["accounts"]):
        label = f"accounts[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{label} 必须是对象")
        account_id = validate_account_id(item.get("id"))
        if account_id in seen:
            raise ConfigError(f"账号 id 重复: {account_id}")
        seen.add(account_id)
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{label}.enabled 必须是布尔值")
        env_file = item.get("env_file")
        if not isinstance(env_file, str) or not env_file.strip():
            raise ConfigError(f"{label}.env_file 必须是非空字符串")
        name = item.get("name")
        if name is None:
            name = account_id
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise ConfigError(f"{label}.name 必须为 1–40 个字符")
        accounts.append(
            Account(
                id=account_id,
                enabled=enabled,
                env_file=Path(env_file.strip()).expanduser(),
                name=name.strip(),
            )
        )
    return accounts


def load_accounts(path: Path = DEFAULT_ACCOUNTS_FILE) -> list[Account] | None:
    """保持运行层旧接口：没有注册表返回 None，否则仅返回启用账号。"""
    records = load_account_records(path)
    if records is None:
        return None
    return [account for account in records if account.enabled]


def account_paths(account: Account, project_root: Path) -> AccountPaths:
    """只解析账号运行需要的路径键，不读取或返回 Cookie 等秘密字段。"""
    root = Path(project_root).expanduser().resolve()
    env_file = account.env_file if account.env_file.is_absolute() else root / account.env_file
    if not env_file.is_file() and account.id != "main":
        raise ConfigError(f"账号环境文件不存在: {env_file}")
    values = dotenv_values(env_file) if env_file.is_file() else {}

    is_main = account.id == "main"
    defaults = {
        "TASK_CONFIG": "config.json" if is_main else f"config/tasks/{account.id}.json",
        "DOUYIN_STORAGE_STATE": "storage-state.json" if is_main else f"storage_state/{account.id}.json",
        "ARTIFACTS_DIR": "artifacts" if is_main else f"artifacts/{account.id}",
    }

    def resolve(key: str) -> Path:
        value = values.get(key)
        path = Path(str(value).strip()).expanduser() if value and str(value).strip() else Path(defaults[key])
        return path.resolve() if path.is_absolute() else (root / path).resolve()

    state_value = values.get("DOUYIN_STORAGE_STATE")
    state = resolve("DOUYIN_STORAGE_STATE") if (state_value and str(state_value).strip()) or is_main else None
    return AccountPaths(
        task_config=resolve("TASK_CONFIG"),
        storage_state=state,
        artifacts_dir=resolve("ARTIFACTS_DIR"),
    )

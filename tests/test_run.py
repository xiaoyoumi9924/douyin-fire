import sys
from pathlib import Path

import pytest

import run as run_module


@pytest.fixture(autouse=True)
def cli_argv(monkeypatch):
    """run.py 会真的解析命令行；运行测试时不能让 pytest 的参数混进来。"""
    monkeypatch.setattr(sys, "argv", ["run.py"])


def _write_accounts(tmp_path: Path, content: str) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "accounts.json").write_text(content, encoding="utf-8")


def test_no_accounts_file_uses_single_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_module, "run_single", lambda: 42)
    monkeypatch.setattr(run_module, "run_all_accounts", lambda **_kwargs: 7)

    assert run_module.main() == 42


def test_accounts_file_uses_multi_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_accounts(tmp_path, '{"accounts": [{"id": "a", "env_file": ".env.a"}]}')
    monkeypatch.setattr(run_module, "run_single", lambda: 42)
    monkeypatch.setattr(run_module, "run_all_accounts", lambda **_kwargs: 7)

    assert run_module.main() == 7


def test_broken_accounts_file_returns_2(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_accounts(tmp_path, "not json")
    monkeypatch.setattr(run_module, "run_single", lambda: 42)
    monkeypatch.setattr(run_module, "run_all_accounts", lambda **_kwargs: 7)

    assert run_module.main() == 2


def test_multi_mode_interrupt_returns_130(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_accounts(tmp_path, '{"accounts": []}')
    monkeypatch.setattr(run_module, "run_single", lambda: 42)

    def interrupted(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_module, "run_all_accounts", interrupted)

    assert run_module.main() == 130


def test_account_flag_rejected_in_single_mode(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run.py", "--account", "main"])
    monkeypatch.setattr(run_module, "run_single", lambda: 42)

    assert run_module.main() == 2
    assert "--account" in capsys.readouterr().out

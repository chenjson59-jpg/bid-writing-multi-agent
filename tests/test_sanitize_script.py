"""T-041 清洗脚本 scripts/sanitize_profile_data.py 的守卫测试（离线）。

脚本是薄编排层，清洗逻辑本身在 src/db/profile_sanitize.py，由
test_profile_sanitize.py 覆盖；本文件只验证编排守卫：
- 未配置 DATABASE_URL 时拒绝执行并以退出码 2 结束（禁止静默成功）；
- 默认 dry-run 只出计划、绝不调用 apply_cleanup；
- --apply 时先出计划再执行一次清洗。
全程 monkeypatch，不连真实数据库、无网络。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from src.config import settings
from src.db.profile_sanitize import CleanupPlan, CleanupReport

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "t041_sanitize_script", _REPO_ROOT / "scripts" / "sanitize_profile_data.py"
)
sanitize_script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sanitize_script)

_DUMMY_DATABASE_URL = "postgresql+asyncpg://localhost/bidding"


class _FakeSanitize:
    """记录编排层调用次数的假清洗器，绝不触碰真实数据库。"""

    def __init__(self) -> None:
        self.plan_calls = 0
        self.apply_calls = 0

    async def collect(self) -> CleanupPlan:
        self.plan_calls += 1
        return CleanupPlan(
            profile_ids=[2],
            task_job_ids=["job-legacy-1"],
            profile_changed_count=1,
            task_changed_count=1,
        )

    async def apply(self) -> CleanupReport:
        self.apply_calls += 1
        return CleanupReport(
            profiles_changed=1,
            tasks_changed=1,
            profile_ids=[2],
            task_job_ids=["job-legacy-1"],
        )


@pytest.fixture
def fake_sanitize(monkeypatch: pytest.MonkeyPatch) -> _FakeSanitize:
    fake = _FakeSanitize()
    monkeypatch.setattr(sanitize_script, "collect_cleanup_plan", fake.collect)
    monkeypatch.setattr(sanitize_script, "apply_cleanup", fake.apply)
    return fake


def test_script_exits_2_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
    fake_sanitize: _FakeSanitize,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(sys, "argv", ["sanitize_profile_data.py"])

    assert sanitize_script.main() == 2

    captured = capsys.readouterr()
    assert "DATABASE_URL" in captured.err
    # 无持久化库配置时，计划扫描与写入都不得发生
    assert fake_sanitize.plan_calls == 0
    assert fake_sanitize.apply_calls == 0


def test_script_dry_run_prints_plan_and_never_writes(
    monkeypatch: pytest.MonkeyPatch,
    fake_sanitize: _FakeSanitize,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "database_url", _DUMMY_DATABASE_URL)
    monkeypatch.setattr(sys, "argv", ["sanitize_profile_data.py"])

    assert sanitize_script.main() == 0

    captured = capsys.readouterr()
    assert fake_sanitize.plan_calls == 1
    assert fake_sanitize.apply_calls == 0
    assert "dry-run" in captured.out
    assert "未写入" in captured.out


def test_script_apply_runs_plan_then_cleanup_once(
    monkeypatch: pytest.MonkeyPatch,
    fake_sanitize: _FakeSanitize,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "database_url", _DUMMY_DATABASE_URL)
    monkeypatch.setattr(sys, "argv", ["sanitize_profile_data.py", "--apply"])

    assert sanitize_script.main() == 0

    captured = capsys.readouterr()
    assert fake_sanitize.plan_calls == 1
    assert fake_sanitize.apply_calls == 1
    assert "清洗完成" in captured.out
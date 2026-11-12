"""T-041 库内历史高敏数据物理清洗脚本（方案 A）。

默认 dry-run 只打印清洗计划、零写入；加 --apply 才在单事务内执行。
执行前必须先完成规格第 6 节的 pg_dump 备份与恢复演练。

用法：
    uv run --frozen python scripts/sanitize_profile_data.py            # 干跑
    uv run --frozen python scripts/sanitize_profile_data.py --apply    # 实际清洗
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.config import settings
from src.db.profile_sanitize import apply_cleanup, collect_cleanup_plan


async def _run(apply: bool) -> int:
    if not settings.database_url:
        print("错误：未配置 DATABASE_URL，拒绝在无持久化库的环境执行清洗。", file=sys.stderr)
        return 2

    plan = await collect_cleanup_plan()
    print("=== T-041 清洗计划（dry-run 预览）===")
    print(f"bidder_profile 待清洗行：{plan.profile_changed_count}，ids={plan.profile_ids}")
    print(f"task_history 待剥离 profile 的任务：{plan.task_changed_count}，job_ids={plan.task_job_ids}")

    if not apply:
        print("dry-run 结束，未写入任何数据。确认计划后使用 --apply 执行。")
        return 0

    report = await apply_cleanup()
    print("=== T-041 清洗完成 ===")
    print(f"bidder_profile 实际变更：{report.profiles_changed} 行，ids={report.profile_ids}")
    print(f"task_history 实际变更：{report.tasks_changed} 行，job_ids={report.task_job_ids}")
    print("可再次运行本脚本验证幂等：两项变更数应为 0。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="T-041 历史高敏数据物理清洗（默认 dry-run）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入清洗结果；不带此参数时仅打印计划、不写库",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
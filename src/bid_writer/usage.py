"""模型调用计量（P1-5）：记录调用次数、token 与耗时，并估算费用。

调用方只要把任意 `ModelCaller` 包一层 `UsageMeter.wrap()`，就能在编写结束后拿到
本次任务的用量摘要；章节并发写作时共用同一份计量器，内部加锁累加。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from src.config import settings


class UsageMeter:
    """线程安全的调用计量器。"""

    def __init__(self, price_input: float = 0.0, price_output: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._duration_ms = 0
        self._models: list[str] = []
        self._price_input = max(0.0, price_input)
        self._price_output = max(0.0, price_output)

    def record(self, result: dict[str, Any], duration_ms: int) -> None:
        """累加一次调用；`usage` 缺失时按 0 计，仅统计调用次数与耗时。"""
        usage = result.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        model = str(result.get("model") or "")

        with self._lock:
            self._calls += 1
            self._prompt_tokens += prompt
            self._completion_tokens += completion
            self._total_tokens += total
            self._duration_ms += duration_ms
            if model and model not in self._models:
                self._models.append(model)

    def summary(self) -> dict[str, Any]:
        """用量摘要，写入 `report.json` 与任务结果。"""
        with self._lock:
            calls = self._calls
            prompt = self._prompt_tokens
            completion = self._completion_tokens
            total = self._total_tokens
            duration = self._duration_ms
            models = list(self._models)

        cost = (
            prompt / 1000 * self._price_input + completion / 1000 * self._price_output
        )
        return {
            "models": models,
            "calls": calls,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "duration_ms": duration,
            "price_input_per_1k": self._price_input,
            "price_output_per_1k": self._price_output,
            "estimated_cost": round(cost, 4),
            "cost_estimated": bool(self._price_input or self._price_output),
        }

    def wrap(self, caller) -> MeteredCaller:
        return MeteredCaller(caller, self)


class MeteredCaller:
    """给任意 `ModelCaller` 套上计时与 token 统计，透传返回值。"""

    def __init__(self, inner, meter: UsageMeter) -> None:
        self._inner = inner
        self._meter = meter

    def generate(self, messages: list[dict], json_mode: bool) -> dict[str, Any]:
        started = time.perf_counter()
        result = self._inner.generate(messages, json_mode)
        duration_ms = int((time.perf_counter() - started) * 1000)
        self._meter.record(result or {}, duration_ms)
        return result


def default_meter() -> UsageMeter:
    """按配置单价构造计量器（单价为 0 时只统计 token，不估算金额）。"""
    return UsageMeter(
        price_input=settings.bid_writer_price_input_per_1k,
        price_output=settings.bid_writer_price_output_per_1k,
    )

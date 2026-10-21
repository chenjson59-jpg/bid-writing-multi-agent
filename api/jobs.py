"""任务状态映射（A22）。

把队列后端的状态映射为统一的四种 API 状态，供接口返回与前端轮询使用。
"""
_PENDING = {"PENDING", "RECEIVED", "pending"}
_SUCCEEDED = {"SUCCESS", "succeeded"}
_FAILED = {"FAILURE", "REVOKED", "failed"}


def map_state(state: str) -> str:
    """队列状态 → API 状态（pending / running / succeeded / failed）。"""
    if state in _PENDING:
        return "pending"
    if state in _SUCCEEDED:
        return "succeeded"
    if state in _FAILED:
        return "failed"
    return "running"  # STARTED / PROGRESS / RETRY / 未知

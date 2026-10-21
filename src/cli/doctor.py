"""doctor 子命令：检查配置、目录与外部服务连通性。"""
from __future__ import annotations

import json
from pathlib import Path

from src.config import settings
from src.pipeline import UPLOAD_DIR


def register(subparsers) -> None:
    p = subparsers.add_parser("doctor", help="检查配置、目录与外部服务连通性")
    p.add_argument("--probe-embeddings", action="store_true", help="做一次真实嵌入探测")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(handler=run)


def _check_dirs() -> dict[str, bool]:
    """检查产物目录是否可写；不存在则尝试创建（环境初始化的一部分）。"""
    result: dict[str, bool] = {}
    for name, path in (("upload", UPLOAD_DIR), ("chroma_db", Path(settings.chroma_dir))):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            result[name] = True
        except OSError:
            result[name] = False
    return result


def _probe_embeddings() -> dict:
    """做一次真实嵌入探测，确认维度与编码格式。"""
    from src.vectorstore.embedding import ZhipuEmbeddingFunction

    try:
        fn = ZhipuEmbeddingFunction()
        vectors = fn(["智谱嵌入探测"])
        return {
            "ok": True,
            "model": settings.embedding_model,
            "dimensions": len(vectors[0]) if vectors else 0,
        }
    except Exception as exc:  # noqa: BLE001 - 探测可能因网络/认证失败，如实报告即可
        return {"ok": False, "error": str(exc)}


def _print(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    for key, ok in report["config"].items():
        print(f"{'[OK]' if ok else '[--]'} 配置 {key}")
    for name, ok in report["dirs"].items():
        print(f"{'[OK]' if ok else '[!!]'} 目录 {name}")
    if "embeddings" in report:
        emb = report["embeddings"]
        if emb["ok"]:
            print(f"[OK] 嵌入 {emb['model']} 维度 {emb['dimensions']}")
        else:
            print(f"[!!] 嵌入探测失败：{emb['error']}")


def run(args) -> int:
    report = {
        "config": {
            "deepseek_api_key": bool(settings.deepseek_api_key),
            "zhipu_api_key": bool(settings.zhipu_api_key),
            "mineru_api_key": bool(settings.mineru_api_key),
        },
        "dirs": _check_dirs(),
    }
    if args.probe_embeddings:
        report["embeddings"] = _probe_embeddings()
    _print(report, args.json)
    return 0

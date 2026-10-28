"""端到端测试：上传 → 解析 → 抽取 → 编写 → 产物下载。

模型调用全部注入 fake caller，任务走 `local` 后端，不依赖真实 API / Redis / PostgreSQL。
"""
import hashlib
import json
import time

import pymupdf

_EXTRACTION = {
    "basic": {"project_name": "测试项目", "tender_no": "TEST-001"},
    "evaluation": {
        "factors": [{"name": "投标报价", "category": "报价", "score": 30}],
        "preliminary_review": [],
        "score_table": [],
    },
    "qualification": {"items": []},
    "bid_format": {"items": [{"title": "投标函"}]},
    "contract": {"highlights": []},
    "tech": {"requirements": []},
}


def _pdf_bytes() -> bytes:
    """生成带书签的两章 PDF，保证本地解析能切出章节。"""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "第一章 投标邀请：投标截止时间 2026 年 1 月 1 日")
    page = doc.new_page()
    page.insert_text((72, 72), "第二章 评标办法：投标报价 30 分")
    doc.set_toc([[1, "第一章 投标邀请", 1], [1, "第二章 评标办法", 2]])
    content = doc.tobytes()
    doc.close()
    return content


class _ExtractCaller:
    """抽取阶段的 fake 模型：每个单元都返回同一份结构化结果。"""

    def generate(self, messages, json_mode):
        return {
            "content": json.dumps(_EXTRACTION, ensure_ascii=False),
            "finish_reason": "stop",
            "json_mode_used": True,
        }


class _BidCaller:
    """编写阶段的 fake 模型：策划返回空走兜底大纲，评审 95 分不触发修订。"""

    def generate(self, messages, json_mode):
        system = messages[0]["content"]
        if json_mode:
            content = ""
            if "评审" in system:
                content = json.dumps(
                    {"score": 95, "covered_points": ["完全响应"], "issues": [], "suggestions": []},
                    ensure_ascii=False,
                )
            return {"content": content, "finish_reason": "stop", "json_mode_used": True}
        return {
            "content": "我方完全响应招标文件的全部实质性要求。",
            "finish_reason": "stop",
            "json_mode_used": False,
        }


def _poll(client, job_id: str) -> dict:
    for _ in range(200):
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in ("succeeded", "failed"):
            return data
        time.sleep(0.01)
    return data


def test_parse_extract_bid_and_download(client, monkeypatch):
    from src import tasks
    from src.bid_writer import agents
    from src.vectorstore import retrieval

    real_extract = tasks.run_extraction
    monkeypatch.setattr(
        tasks,
        "run_extraction",
        lambda doc, force=False: real_extract(doc, caller=_ExtractCaller(), force=force),
    )
    monkeypatch.setattr(agents, "default_caller", lambda: _BidCaller())

    class _EmptyRetriever:
        """写作前的检索直接返回空列表，避免触碰 Chroma。"""

        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, _query, _limit=None):
            return []

    monkeypatch.setattr(retrieval, "ChromaRetriever", _EmptyRetriever)

    content = _pdf_bytes()
    doc_id = hashlib.sha256(content).hexdigest()[:16]

    # 1. 上传 → local worker 解析并抽取
    resp = client.post(
        "/api/parse",
        files={"file": ("demo.pdf", content, "application/pdf")},
        params={"parser": "local", "extract": "true"},
    )
    assert resp.status_code == 202
    assert resp.json()["doc_id"] == doc_id

    parse_job = _poll(client, resp.json()["job_id"])
    assert parse_job["status"] == "succeeded"
    assert parse_job["extraction"]["project_name"] == "测试项目"

    documents = client.get("/api/documents").json()
    assert documents[0]["doc_id"] == doc_id
    assert documents[0]["has_extraction"] is True

    # 2. 编写 → 产物落盘
    resp = client.post(
        "/api/bids",
        json={"doc_id": doc_id, "profile": {"company_name": "测试公司"}},
    )
    assert resp.status_code == 202

    bid_job = _poll(client, resp.json()["job_id"])
    assert bid_job["status"] == "succeeded"
    assert bid_job["bid"]["project_name"] == "测试项目"
    assert bid_job["bid"]["bidder"] == "测试公司"

    # 3. 产物下载
    bid_id = client.get("/api/bids").json()[0]["bid_id"]
    markdown = client.get(f"/api/bids/{bid_id}/files/bid.md")
    assert markdown.status_code == 200
    assert "完全响应" in markdown.text
    assert client.get(f"/api/bids/{bid_id}/files/bid.docx").status_code == 200
    assert client.get(f"/api/bids/{bid_id}/files/report.json").status_code == 200
    assert (
        client.get(
            f"/api/bids/{bid_id}/files/bid.md", params={"download": "true"}
        ).status_code
        == 200
    )
    assert client.get(f"/api/bids/{bid_id}/files/nope.md").status_code == 404

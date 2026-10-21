"""命令行子命令与退出码测试。"""
from dataclasses import dataclass, field

from src import cli
from src.errors import ConfigError, ExtractError, IngestError, ParseError


def test_build_parser_registers_subcommands():
    parser = cli.build_parser()
    # 各子命令应能解析成功
    args = parser.parse_args(["doctor", "--json"])
    assert args.command == "doctor"
    assert args.handler is not None

    args = parser.parse_args(["parse", "a.pdf", "--parser", "local", "--force"])
    assert args.parser == "local"
    assert args.force is True

    args = parser.parse_args(["ingest", "a.pdf", "--no-extract", "--no-ingest"])
    assert args.no_extract is True
    assert args.no_ingest is True

    args = parser.parse_args(["inspect", "tender_document", "--query", "评标办法", "--limit", "3"])
    assert args.limit == 3


def test_main_no_command_returns_zero(capsys):
    assert cli.main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_main_maps_config_error(monkeypatch):
    def boom(args):
        raise ConfigError("缺密钥")

    monkeypatch.setattr(cli.parse, "run", boom)
    assert cli.main(["parse", "x.pdf"]) == 3


def test_main_maps_parse_error(monkeypatch):
    def boom(args):
        raise ParseError("解析失败")

    monkeypatch.setattr(cli.parse, "run", boom)
    assert cli.main(["parse", "x.pdf"]) == 4


def test_main_maps_extract_error(monkeypatch):
    def boom(args):
        raise ExtractError("抽取失败")

    monkeypatch.setattr(cli.extract, "run", boom)
    assert cli.main(["extract", "docid"]) == 5


def test_main_maps_ingest_error(monkeypatch):
    def boom(args):
        raise IngestError("入库失败")

    monkeypatch.setattr(cli.ingest, "run", boom)
    assert cli.main(["ingest", "x.pdf"]) == 6


def test_main_keyboard_interrupt(monkeypatch):
    def interrupt(args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli.parse, "run", interrupt)
    assert cli.main(["parse", "x.pdf"]) == 130


# ---- 各子命令的 run 输出 ----


@dataclass
class _FakeDoc:
    doc_id: str = "abc123"
    doc_type: str = "tender_file"
    parser: str = "local"
    pages: int = 2
    sections: list = field(default_factory=lambda: [1, 2])
    source_name: str = "test.pdf"


@dataclass
class _FakeExtraction:
    doc_id: str = "abc123"
    project_name: str = "测试项目"
    evaluation_factors: list = field(default_factory=lambda: [1, 2])
    bid_format_items: list = field(default_factory=lambda: [1])
    qualifications: list = field(default_factory=list)
    extraction_meta: dict = field(default_factory=lambda: {"ungrounded_fields": []})


@dataclass
class _FakeSummary:
    collection: str = "tender_document"
    added: int = 2
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    total: int = 2

    def to_dict(self):
        return {
            "collection": self.collection,
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "total": self.total,
        }


def test_parse_command(monkeypatch, capsys):
    monkeypatch.setattr(cli.parse, "run_parse", lambda *a, **k: _FakeDoc())
    args = cli.build_parser().parse_args(["parse", "test.pdf", "--parser", "local"])

    assert args.handler(args) == 0
    assert "abc123" in capsys.readouterr().out


def test_extract_command(monkeypatch, capsys):
    monkeypatch.setattr(cli.extract, "run_extract", lambda *a, **k: _FakeExtraction())
    args = cli.build_parser().parse_args(["extract", "abc123"])

    assert args.handler(args) == 0
    assert "测试项目" in capsys.readouterr().out


def test_ingest_command(monkeypatch, capsys):
    monkeypatch.setattr(cli.ingest, "run_parse", lambda *a, **k: _FakeDoc())
    monkeypatch.setattr(cli.ingest, "run_extraction", lambda *a, **k: _FakeExtraction())
    monkeypatch.setattr(cli.ingest, "ingest_document", lambda *a, **k: _FakeSummary())
    args = cli.build_parser().parse_args(["ingest", "test.pdf"])

    assert args.handler(args) == 0
    assert "入库" in capsys.readouterr().out


def test_ingest_command_no_extract(monkeypatch):
    monkeypatch.setattr(cli.ingest, "run_parse", lambda *a, **k: _FakeDoc())
    monkeypatch.setattr(cli.ingest, "ingest_document", lambda *a, **k: _FakeSummary())
    args = cli.build_parser().parse_args(["ingest", "test.pdf", "--no-extract"])

    # 跳过抽取时不应调用 run_extraction
    assert args.handler(args) == 0


def test_inspect_command_list(monkeypatch, capsys):
    class _FakeCollection:
        def get(self, limit, include):
            return {"ids": ["a"], "documents": ["正文"], "metadatas": [{"doc_id": "x"}]}

    monkeypatch.setattr(cli.inspect, "get_collection", lambda name: _FakeCollection())
    args = cli.build_parser().parse_args(["inspect", "tender_document"])

    assert args.handler(args) == 0
    assert "正文" in capsys.readouterr().out


def test_inspect_command_query(monkeypatch):
    class _FakeRetriever:
        def __init__(self, collection_name):
            self.collection_name = collection_name

        def search(self, query, limit=None):
            return [{"id": "a", "text": "命中", "metadata": {}}]

    monkeypatch.setattr(cli.inspect, "ChromaRetriever", _FakeRetriever)
    args = cli.build_parser().parse_args(["inspect", "tender_document", "--query", "评标"])

    assert args.handler(args) == 0


def test_doctor_command(monkeypatch, tmp_path, capsys):
    from src.config import settings

    monkeypatch.setattr(cli.doctor, "UPLOAD_DIR", tmp_path / "upload")
    monkeypatch.setattr(settings, "chroma_dir", str(tmp_path / "chroma"))
    args = cli.build_parser().parse_args(["doctor"])

    assert args.handler(args) == 0
    assert "配置" in capsys.readouterr().out

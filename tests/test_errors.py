"""src.errors 的退出码与异常层级测试。"""
import pytest

from src.errors import (
    BidWriteError,
    BiddingError,
    ConfigError,
    ExtractError,
    IngestError,
    ParseError,
    TaskError,
)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ConfigError, 3),
        (ParseError, 4),
        (ExtractError, 5),
        (IngestError, 6),
        (BidWriteError, 7),
    ],
)
def test_exit_codes(exc, code):
    assert exc.exit_code == code


def test_hierarchy():
    for exc in (
        ConfigError,
        ParseError,
        ExtractError,
        IngestError,
        BidWriteError,
        TaskError,
    ):
        assert issubclass(exc, BiddingError)


def test_message_and_instance_code():
    e = ParseError("云端解析超时")
    assert str(e) == "云端解析超时"
    assert e.exit_code == 4

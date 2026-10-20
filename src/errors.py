"""业务异常层级与 CLI 进程退出码。

退出码约定见 README.md「用法」一节：
- 0 成功、3 配置错误、4 解析失败、5 抽取失败、6 入库失败、7 编写失败、130 取消。
- 130 由 Ctrl+C（SIGINT）在进程层面触发，不映射到异常类，CLI 层单独处理。
"""


class BiddingError(Exception):
    """业务异常基类，所有业务异常由此派生，便于统一捕获。"""

    exit_code: int = 1  # 未明确约定退出码的异常默认以 1 退出


class ConfigError(BiddingError):
    """配置缺失或非法（缺密钥、目录不可用等）。"""

    exit_code = 3


class ParseError(BiddingError):
    """文档解析失败（云端或本地路径均归于此）。"""

    exit_code = 4


class ExtractError(BiddingError):
    """结构化抽取失败。"""

    exit_code = 5


class IngestError(BiddingError):
    """检索索引入库失败。"""

    exit_code = 6


class BidWriteError(BiddingError):
    """投标文件编写失败。"""

    exit_code = 7


class TaskError(BiddingError):
    """队列后端无法还原异常类型时的兜底标记。"""

    exit_code = 1

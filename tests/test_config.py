"""配置默认值回归测试：禁止在代码中内置数据库口令或公网主机地址。

本地 .env 与系统环境变量可能设置了 DATABASE_URL，因此这里直接校验
Settings 字段的声明默认值，而不是实例化后的取值。
"""

from src.config import REPO_ROOT, Settings


def test_database_url_default_is_empty():
    """未配置 .env 时默认不连接数据库，由 session 层降级为无持久化。"""
    assert Settings.model_fields["database_url"].default == ""


def test_config_source_has_no_hardcoded_credential_or_public_host():
    """配置源码中不得出现弱口令或公网 IP，密钥只能来自环境。"""
    source = (REPO_ROOT / "src" / "config.py").read_text(encoding="utf-8")
    assert "123456" not in source
    assert "47.117.189.10" not in source


def test_env_example_keys_match_settings_fields():
    """防配置漂移：.env.example 与 Settings 字段双向一一对应（AGENTS.md 第 7 节）。

    - .env.example 不允许出现 Settings 没有的键（摆设环境变量，配了也不生效）；
    - Settings 字段不允许在 .env.example 缺列（用户无法发现可配置项）。
    """
    fields = set(Settings.model_fields)
    example_keys: set[str] = set()
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            example_keys.add(line.split("=", 1)[0].lower())

    assert example_keys - fields == set(), f"摆设 env 键（Settings 无此字段）: {sorted(example_keys - fields)}"
    assert fields - example_keys == set(), f".env.example 缺漏字段: {sorted(fields - example_keys)}"

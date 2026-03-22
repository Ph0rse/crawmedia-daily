"""
全局配置加载器
从 config.yaml + .env 加载配置，提供统一的访问接口。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_env_vars(value):
    """递归替换 YAML 中的 ${ENV_VAR} 占位符为实际环境变量"""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{(\w+)\}")
        def replacer(match):
            return os.environ.get(match.group(1), "")
        return pattern.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(config_path: str | None = None) -> dict:
    """加载并返回配置字典，自动解析环境变量占位符"""
    path = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _resolve_env_vars(raw)


# 单例配置，首次导入时加载
_config: dict | None = None


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config

"""环境变量与运行模式配置。

从 server.py 抽取。原有 4 处定义分散在不同位置（第 ~1455、~1509、~10040 行），
本模块把它们合并为单一来源，消除「同名函数散落多处」的隐患。

所有函数均为纯函数，不含可变状态、不加锁、不重绑定模块级名字 —— 因此
拆分对行为零影响，且可被任意模块安全 import。
"""

from __future__ import annotations

import os

TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
PRODUCTION_ENV_VALUES = {"prod", "production", "online"}


def raw_env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def raw_env_flag(name: str, default: bool = False) -> bool:
    value = raw_env_value(name)
    if value == "":
        return default
    return value.lower() in TRUTHY_ENV_VALUES


def is_raw_production_mode() -> bool:
    return raw_env_value("XINGYUN_ENV").lower() in PRODUCTION_ENV_VALUES


def is_production_mode() -> bool:
    return raw_env_value("XINGYUN_ENV").lower() in PRODUCTION_ENV_VALUES


def env_flag(name: str, default: bool = False) -> bool:
    return raw_env_flag(name, default)


def env_value(name: str, default: str = "") -> str:
    return raw_env_value(name, default)


def expose_dev_code(name: str) -> bool:
    return env_flag(name, default=not is_production_mode())


def cookie_secure_enabled() -> bool:
    public_base = raw_env_value("XINGYUN_PUBLIC_BASE_URL").lower()
    return env_flag("XINGYUN_COOKIE_SECURE", default=public_base.startswith("https://"))

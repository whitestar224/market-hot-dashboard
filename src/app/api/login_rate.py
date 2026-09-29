"""从 server.py 抽取的模块（Phase 3 拆分，批次 7：登录限流域）。

来源: server.py 第 3044-3062 行（login_rate_limited + record_login_failure +
clear_login_failures 三个紧密耦合的登录失败限流函数）。

这三个函数只依赖 app.core.state 里的锁/字典（AUTH_LOGIN_LOCK、AUTH_LOGIN_ATTEMPTS，
同一对象、不重绑定），无任何 server.py 本地函数依赖，故无需 _server() 懒加载。
"""

from __future__ import annotations

import time

from app.core.state import AUTH_LOGIN_ATTEMPTS, AUTH_LOGIN_LOCK


def login_rate_limited(key: str) -> bool:
    now = time.time()
    with AUTH_LOGIN_LOCK:
        attempts = [item for item in AUTH_LOGIN_ATTEMPTS.get(key, []) if now - item < 300]
        AUTH_LOGIN_ATTEMPTS[key] = attempts
        return len(attempts) >= 8


def record_login_failure(key: str) -> None:
    now = time.time()
    with AUTH_LOGIN_LOCK:
        attempts = [item for item in AUTH_LOGIN_ATTEMPTS.get(key, []) if now - item < 300]
        attempts.append(now)
        AUTH_LOGIN_ATTEMPTS[key] = attempts


def clear_login_failures(key: str) -> None:
    with AUTH_LOGIN_LOCK:
        AUTH_LOGIN_ATTEMPTS.pop(key, None)

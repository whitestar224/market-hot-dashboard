"""路径常量。

⚠️ 拆分类注意事项（本文件是这条铁律的来源）
--------------------------------------------
`ROOT` 原先定义在 server.py：

    ROOT = Path(os.getenv("XINGYUN_APP_ROOT") or Path(__file__).resolve().parent).resolve()

它依赖 `__file__` 来推导项目根目录。**若把这个表达式原样机械搬进
src/app/core/paths.py，`__file__` 会变成 src/app/core/paths.py，
`.parent` 就指向 src/app/core/ 而不是项目根 —— 静态文件服务会静默 404，
且不会有任何报错**。

因此本模块不照抄原表达式，改为：
  1. 优先读环境变量 XINGYUN_APP_ROOT（保持原行为）
  2. 否则从本文件向上逐级查找含 server.py / index.html 的目录（自纠正）
  3. 都找不到才回退到按深度推算的路径

这样无论文件被放在哪一层，ROOT 都能解析到真正的项目根。
"""

from __future__ import annotations

import os
from pathlib import Path

# 项目根目录的识别标记：这些文件只存在于项目根
_ROOT_MARKERS = ("server.py", "index.html", "pyproject.toml")


def _detect_project_root() -> Path:
    """从本文件位置向上查找项目根，避免因文件移动导致 __file__ 深度变化而解析错误。"""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    # 兜底：src/app/core/paths.py -> 上溯 3 级 = 项目根
    return here.parents[2] if len(here.parents) >= 3 else here


ROOT = Path(os.getenv("XINGYUN_APP_ROOT") or _detect_project_root()).resolve()

# 静态资源根：所有 html / css / js / assets 都放在 web/ 下。
# HTTP 侧仍然以【本站根】对外提供（Handler 的 directory 指向这里），
# 所以浏览器地址仍是 /index.html、/assets/...，前端相对路径一行都不用改。
WEB_DIR = ROOT / "web"

# 业务模块根：根目录只留 server.py / 启动脚本 / README，其余代码都在 src/app 下。
# server.py 里所有「以子进程拉起某个 .py」的地方都要用 APP_DIR 拼路径，别再拼 ROOT。
APP_DIR = ROOT / "src" / "app"


def runtime_dir() -> Path:
    """运行态缓存目录。

    ⚠️ 模块搬进 src/app/<pkg>/ 之后，`Path(__file__).resolve().parent` 不再等于项目根，
    以前那套 `Path(__file__).parent / ".runtime-cache"` 会凭空造出
    `src/app/<pkg>/.runtime-cache`，缓存与数据库全部写丢。统一走这里。
    """
    configured = str(os.getenv("XINGYUN_RUNTIME_DIR") or "").strip()
    return Path(configured).expanduser() if configured else ROOT / ".runtime-cache"


CODEX_HOME = Path(os.getenv("CODEX_HOME") or (Path.home() / ".codex"))

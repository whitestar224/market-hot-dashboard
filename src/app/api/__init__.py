"""HTTP 接口层。

按业务域拆分 server.py 的 `Handler` 路由与各域 handler，每个模块对应
一组 `/api/*` 端点。约定：

- 只允许依赖 `app.core.*` 与 `app.domains.*`，禁止互相 import（避免环）
- 需要共享的可变状态一律从 `app.core.state` 引用，禁止在本层新建锁
  （否则会出现两把不同的锁，互斥静默失效）
"""

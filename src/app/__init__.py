"""应用包（Phase 3 拆分目标）。

server.py 保持为进程入口（.cmd 脚本 / service_guard / Electron 均引用它），
逐步把内部实现拆入本包。所有拆分遵循 docs/REFACTOR-BLUEPRINT.md 的验收流程：
每次只拆一个域，拆完立刻跑 Golden Master 结构比对 + 测试，失败即回滚。
"""

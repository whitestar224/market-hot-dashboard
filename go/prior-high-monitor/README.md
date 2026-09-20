# 结构前高监控（Go）

这是起爆台新版前高监控的 Go 原生实现。它不套用 BTC、大周期共振或行情来源提醒许可，但会执行母盘整外沿审查：同一近期箱体中仍有更高的已确认压力位时，较低内部枢轴不会产生 B 或提醒。

核心流程：因果枢轴确认 → 压力位身份锁定 → 空间或时间分离 → 母盘整最高外沿审查 → 从下方首次穿越 → 回落后重新武装。锚点建立后冻结，附近高点只能在有限价格带内归并；触发价使用合并压力区的最高外沿，而不是最初较低锚点。

```powershell
go run . -csv candles.csv -tf 5m -out breakout_events.json
```

CSV 至少包含 `time,open,high,low,close`，`volume` 可选。命令输出的 JSON 事件与浏览器 `prior-high-engine.js` 的正式 B 点字段一一对应。

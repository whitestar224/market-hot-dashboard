# 币安 Web3 接入状态

用户确认已开通币安 Web3 API；不使用托管钱包，每笔仍由外部钱包确认。

## 最新状态：2026-09-09

以下“未切换/未部署/5%”段落为历史实施记录，已被本段更新：同链Binance/跨链Relay已部署，真实资金交易未验收。用户本轮明确选择自动滑点最高20%，采用官方autoSlippage=true + maxAutoSlippagePercent=20，并核对返回滑点与编码最低到账；旧订单仍遵守原确认上限。Gas预算独立于滑点，前端显示后再确认。

用户询问币安跨链：官方钱包产品有跨链，但目前公开Web3 API仅查到同链构建接口，没有找到公开跨链路由/构建参数；暂保留Relay，不使用私有逆向接口。

已完成：

- 官方固定主机 https://web3.binance.com/build，HMAC-SHA256 + Base64，签名包含原始 `/build` 路径及编码后的完整查询。
- 独立本机配置窗口，API Key/Secret 隐藏显示；Windows 当前账户 DPAPI 加密保存到 `%LOCALAPPDATA%/XingyunShe/binance-web3.credentials`，不在项目、安装包或模型输入中。
- 配置后只测试 BNB 链能力；有限超时、不跟随重定向、不输出服务商原始错误，客户端无广播端点。
- 用户已完成本机加密配置。实测先返回 40103：请求时间与服务端约差 11 秒。已改为固定 HTTPS 服务端响应 timestamp（Date 备用）与请求往返中点校准请求时间，仅对只读请求重签一次并生成新 nonce，不调整系统时间、不放宽 recv-window；复用连接后，BNB 支持链接口连续三次返回成功。

尚未完成，不能宣称已切换：

- 配置与连接检查已通过，真实交易执行尚未切换。
- 使用真实官方报价校验返回格式、交易路由合约、ABI/最低到账绑定和模拟结果，再接入现有逐笔钱包签名执行器。
- 保持滑点 5% 上限、金额上限、精确授权，拒绝未知 RFQ/EIP712 和任意合约调用。不能把币安交易数据直接交给 Relay 执行器。
- 官方 Flash quote-and-swap 是同链接口，不冒充跨链执行；跨链流程另行验证。

## 2026-09-09 实施中，未部署

- 用户明确选择同链改为币安 Web3，跨链保留 Relay。
- 新增 `binance_buy.py`；固定 Flash 报价、固定5%上限/关闭自动滑点、已核验EVM路由及实现指纹、调用中金额/CA/最少到账与资金变化模拟、精确授权、按实际目标币转账核验状态。
- 后端按服务商分流，新增只读订单 preflight；前端新增独立币安顺序执行，不把币安数据交给 Relay SDK，同链不回退旧交易服务。原钱包仍逐笔确认。
- 真报价与模拟通过；无任何实际交易。Binance适配器专项/共享买入批次32项通过，后续主回归198项通过。但新前端Binance执行器仍需要专门的端到端模拟。
- 在线能力接口仍显示 Relay；bundle及静态版本未更新，后台未重启。**实现中不是已上线。** 受用户最新要求，本轮先完成遗漏审计，后续按持久待办继续。
- 当前仅接受已验证的BNB/Ethereum/Base调用；其他链、Solana/RFQ及卖出/普通兑换不算完成，详见2026-09-09需求审计。

参考：

- https://web3.binance.com/en/dev-docs/authentication
- https://web3.binance.com/en/dev-docs/catalog/web3-wallet/api/rest-api/trading-api
- https://web3.binance.com/en/dev-docs/catalog/web3-wallet/api/rest-api/wallet-api

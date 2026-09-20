"""Read-only public quote compatibility check. Never connects wallets or sends transactions."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
from monitor_buy import MonitorBuyService, ZERO, SOL_NATIVE, SOLANA, validate_quote

service = MonitorBuyService(Path("unused-readonly.sqlite"))
chains = service.networks()
# Public documentation example address; not an account managed by this application.
evm = "0x03508bb71268bba25ecacc8f620e01866650532c"
from hashlib import sha256
n = int.from_bytes(sha256(b"read-only-no-wallet-quote-probe").digest(), "big")
sol = ""
while n:
    n, r = divmod(n, 58)
    sol = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"[r] + sol
base_usdc = next(t["address"] for t in service.tokens(chains[8453]) if t["symbol"] == "USDC")
for chain, token, amount, owner in [(8453, ZERO, "10000000000000000", evm),
    (8453, base_usdc, "10000000", evm), (SOLANA, SOL_NATIVE, "100000000", sol)]:
    try:
        quote = service.http("/quote/v2", {"user": owner, "recipient": evm, "originChainId": chain,
            "destinationChainId": 4663, "originCurrency": token,
            "destinationCurrency": "0x5fc5360d0400a0fd4f2af552add042d716f1d168", "amount": amount,
            "tradeType": "EXACT_INPUT", "slippageTolerance": "500", "latePaymentSlippageTolerance": "0",
            "usePermit": False, "useDepositAddress": False, "forceSolverExecution": True,
            "includeProtocolData": True, "overridePriceImpact": False, "refundTo": owner, "appFees": [], "ttl": 300})
        source = next(t for t in service.tokens(chains[chain]) if t["address"] == token)
        target = next(t for t in service.tokens(chains[4663]) if t["symbol"] == "USDG")
        validate_quote(quote, {"source": source, "target": target, "sender": owner, "recipient": evm,
            "amount": amount, "slippageBps": 500}, chains)
        print("PASS", chain, source["symbol"], "-> Robinhood USDG", "actual slippage", quote["details"]["slippageTolerance"]["total"], "steps", len(quote["steps"]))
    except Exception as exc:
        print(type(exc).__name__, str(exc))
service.pool.shutdown(wait=False, cancel_futures=True)

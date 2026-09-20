# Global wallet connections and buy progress

Scope: same-origin pages/tabs share wallet selection and connection intent. A
fresh page verifies addresses with the injected wallet; persisted hints are never
treated as permission to sign. Binance remains first, followed by OKX, MetaMask
and Bitget. Explicit disconnect propagates to other tabs and survives refresh.
Do not persist keys, signatures or address-as-authorization credentials.

Use one shared wallet module and a global navigation entry, with News Trade and
the buy dialog consuming that module. An independent copy per page would keep
diverging; server-side custody is unnecessary. Restore passively, connect only on
user action, and check the actual account again at the transaction boundary.

Show a gold circular progress indicator, elapsed time and named stages. Progress
is completed workflow steps, not elapsed-time estimates. The backend streams real
quote checkpoints; waiting for a wallet is distinct from receiving a transaction
hash, source-chain confirmation and destination settlement. Unknown outcomes must
not become success or trigger a resend.

Query target-native funds first, then target stablecoins, then other chains only
when earlier routes are unavailable. Keep final balance/gas/contract/quote checks,
single-send claims and per-transaction wallet confirmation. Parallelize independent
preflight reads, not wallet signing. Do not interrupt the user's in-flight orders.

Verification: mocked multi-page wallet lifecycle, cross-tab disconnect, locked and
late-injected wallets, progress stream parsing and stale-request cancellation,
funding-tier call counts, single-send regressions, and visual checks without real
wallet approval or token transfers.

Protocol references: [EIP-1193](https://eips.ethereum.org/EIPS/eip-1193),
[EIP-6963](https://eips.ethereum.org/EIPS/eip-6963).

## Verification and deployment

- 32 JavaScript tests passed (shared wallet lifecycle, active-request close/reopen,
  streamed UTF-8 progress, parallel preflight, missing hash and single-send guards).
- 21 buy-service tests, 9 HTTP disconnect tests and 5 supervisor tests passed.
- Read-only balance comparison on the existing wallet: target-native 7.21 seconds
  for 1 chain (0 errors); old full-scan behavior 31.01 seconds for 9 chains (1
  unavailable source). This is one network sample, not a promised latency.
- Rebuilt the executor and verified the live page, shared module, executor,
  liveness endpoint and progress/error stream after a supervised restart.
- Old orders remain durable and unmodified: claimed signing steps, no returned
  hashes. No wallet approvals, signatures, resends or real transfers were tested.
- Browser UI verification could not attach a test webview; close/reopen and
  streaming behavior were verified with isolated DOM/provider test doubles.

Closing or pressing Esc hides a pending buy; it does not cancel an on-chain
transaction. Reopening during an unresolved send resumes the same dialog. A
45-second missing-wallet-response notice does not unlock or repeat that send.
Fresh pages passively read wallet permissions; passive Solana recovery requires
the provider to expose its already-connected public key.

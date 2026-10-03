"""Verify candidate "launch platform official X account" handles.

A handle is only trusted when it has published the platform's *whitelisted*
contract address.  Anything else (wrong handle, copycat, abandoned account) is
rejected.  This is the gate that keeps a guessed handle from becoming a trust
anchor -- a wrong anchor would let an attacker's address through.

Usage:  C:/Python314/python.exe tools/verify_platform_official_handles.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

# Candidate handles per platform.  Candidates come from three places: the
# platform's own site links seen on the trench board, the handles copycats
# copy (copycats copy the *real* handle -- proven with SAPLING), and plain
# guesses that we expect the check to reject.
CANDIDATES: dict[str, tuple[str, ...]] = {
    "pump.fun": ("pumpdotfun", "pumpfun", "PumpFun"),
    "LetsBonk": ("LetsBonkFun", "letsbonkfun", "BonkFun"),
    "Virtuals": ("virtuals_io", "virtualsprotocol"),
    "BONK": ("bonk_inu", "BonkInu"),
    "Jupiter": ("JupiterExchange", "JupiterExchange_"),
    "Raydium": ("RaydiumProtocol", "raydium"),
    "boop.fun": ("boopedfun", "boopfun"),
    "Bags": ("BagsApp", "bagsapp"),
    "Clanker": ("clanker_world", "clankercoinwww", "clanker"),
    "Moonshot": ("moonshot", "MoonshotApp"),
    "Believe": ("believeapp", "BelieveApp"),
    "sapling.cash": ("saplingdotcash",),
    "Hoookedpad": ("Hoookedpad", "hookedpad"),
    "Hookr.fun": ("hookrfun", "HookrFun"),
    "Pons": ("ponsdotfamily", "pons"),
    "Argus": ("argusdotfun", "ArgusDotFun"),
    "lift.fun": ("liftdotfun", "liftfun"),
    "Bankr": ("bankrbot", "bankr"),
}


def whitelist_for(platform: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for address, label, ticker in server.PLATFORM_OWN_TOKEN_RAW:
        if label.casefold() == platform.casefold():
            out[address.casefold()] = ticker
    return out


def addresses_in(text: str) -> set[str]:
    found: set[str] = set()
    identity = server.personal_x_onchain_identity_from_text(text)
    if identity.get("contractAddress"):
        found.add(identity["contractAddress"].casefold())
    for candidate in server.chat_opportunity_contract_candidates(text):
        address = str(candidate.get("contractAddress") or "").strip()
        if address:
            found.add(address.casefold())
    return found


def main() -> int:
    verified: dict[str, str] = {}
    rejected: list[str] = []
    for platform, handles in CANDIDATES.items():
        expected = whitelist_for(platform)
        print(f"\n=== {platform}  (whitelist {len(expected)} addr) ===")
        if not expected:
            print("    skipped: platform not in PLATFORM_OWN_TOKEN_RAW")
            continue
        for handle in handles:
            source = server.normalize_x_source(
                {"handle": handle, "category": "project_official"}
            )
            if not source:
                print(f"  {handle:<20} normalize rejected")
                continue
            try:
                payload = server.x_kol_fetch_fxtwitter_timeline(source)
            except Exception as exc:  # noqa: BLE001
                print(f"  {handle:<20} fetch error: {type(exc).__name__}: {exc}")
                continue
            items = payload.get("items") or []
            seen: set[str] = set()
            for item in items:
                text = f"{item.get('text') or ''} {item.get('fullText') or ''}"
                seen |= addresses_in(text)
            matched = seen & set(expected)
            if matched:
                verified[platform] = handle
                print(
                    f"  {handle:<20} VERIFIED  tweets={len(items):<3} "
                    f"addresses={len(seen):<3} matched={sorted(matched)}"
                )
            else:
                rejected.append(f"{platform}/{handle}")
                print(
                    f"  {handle:<20} rejected  tweets={len(items):<3} "
                    f"addresses={len(seen):<3}"
                )
    print("\n================ RESULT ================")
    print("VERIFIED handles:")
    for platform, handle in verified.items():
        print(f"  {platform:<14} @{handle}")
    print(f"\nrejected: {len(rejected)}")
    for row in rejected:
        print(f"  {row}")
    Path(ROOT / "tools" / "_verified_platform_handles.json").write_text(
        json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\nwritten tools/_verified_platform_handles.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

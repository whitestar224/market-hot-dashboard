import json
import math
import os
import re
import sqlite3
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

try:
    import requests
except ImportError:  # The bundled quiet runtime intentionally has no third-party packages.
    requests = None


ROOT = Path(__file__).resolve().parent
FEEDBACK_DB = ROOT / ".runtime-cache" / "dragon_wave_feedback.db"
PRECOMPUTED_ROOT = ROOT / ".runtime-cache" / "dragon-wave-precomputed"
FEEDBACK_LOCK = threading.Lock()
ACCOUNT_API = os.getenv("DRAGON_WAVE_ACCOUNT_API", "http://127.0.0.1:8765/api/dragon-wave-feedback").strip()
MAX_BODY = 8_000_000
DEVICE_RE = re.compile(r"^[A-Za-z0-9_.-]{12,96}$")
BINANCE_PAIR_RE = re.compile(r"^[A-Z0-9]{1,32}(?:USDT|USDC|BUSD)$")
BINANCE_NATIVE_PAIRS = {"币安人生USDT"}
BINANCE_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}
BINANCE_CANDLE_ENDPOINTS = {
    "futures": "https://fapi.binance.com/fapi/v1/klines",
    "spot": "https://api.binance.com/api/v3/klines",
}
BYBIT_PAIR_RE = re.compile(r"^[A-Z0-9]{1,32}(?:USDT|USDC)$")
BYBIT_INTERVALS = {
    "1m": "1", "5m": "5", "15m": "15",
    "1h": "60", "4h": "240", "1d": "D",
}
OKX_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1H", "4h": "4H", "1d": "1Dutc",
}
BITGET_FUTURES_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1H", "4h": "4H", "1d": "1D",
}
BITGET_SPOT_INTERVALS = {
    "1m": "1min", "5m": "5min", "15m": "15min",
    "1h": "1h", "4h": "4h", "1d": "1day",
}
GATE_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}
MEXC_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "60m", "4h": "4h", "1d": "1d",
}
FEEDBACK_DATASET_VERSION = 12
FEEDBACK_STRUCTURE_TAGS = {
    "horizontalLaunch", "trendlineBreakout", "triangle", "box",
    "fallingWedge", "pivot", "previousHighBreakout", "consolidationBreakout", "ema90Pullback",
    "volumeBreakout", "nearPreviousHighConsolidation", "newCoinNotFalling",
    "mainWaveActive", "mainWaveExpected",
}
PRECOMPUTED_VERSION_RE = re.compile(r"^v\d{1,6}$")
PRECOMPUTED_PAIR_RE = re.compile(r"^[A-Z0-9\u4e00-\u9fff]{1,36}(?:USDT|USDC|BUSD)$")
PRECOMPUTED_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRECOMPUTED_INTERVALS = {"5m", "15m", "1h", "4h", "1d"}


def binance_candle_request_url(query):
    market = str((query.get("market") or [""])[0]).lower().strip()
    pair = str((query.get("pair") or [""])[0]).upper().strip()
    interval = str((query.get("interval") or [""])[0]).strip()
    if market not in BINANCE_CANDLE_ENDPOINTS:
        raise ValueError("invalid Binance market")
    if pair not in BINANCE_NATIVE_PAIRS and not BINANCE_PAIR_RE.fullmatch(pair):
        raise ValueError("invalid Binance pair")
    if interval not in BINANCE_INTERVALS:
        raise ValueError("invalid Binance interval")
    try:
        start = max(0, int((query.get("start") or ["0"])[0]))
        end = max(0, int((query.get("end") or ["0"])[0]))
        limit = min(1500, max(1, int((query.get("limit") or ["1500"])[0])))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid Binance candle range") from error
    if not start or not end or end < start:
        raise ValueError("invalid Binance candle range")
    params = {
        "symbol": pair,
        "interval": interval,
        "startTime": str(start),
        "endTime": str(end),
        "limit": str(limit),
    }
    return f"{BINANCE_CANDLE_ENDPOINTS[market]}?{urlencode(params)}"


def fetch_binance_candles(query):
    request = Request(
        binance_candle_request_url(query),
        headers={"Accept": "application/json", "User-Agent": "DragonWave/1.0"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Binance HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Binance candle service unavailable: {error}") from error
    if not isinstance(payload, list):
        raise RuntimeError("Binance returned an invalid candle payload")
    return payload


def bybit_candle_request_url(query):
    market = str((query.get("market") or [""])[0]).lower().strip()
    pair = str((query.get("pair") or [""])[0]).upper().strip()
    interval = str((query.get("interval") or [""])[0]).strip()
    if market not in {"futures", "spot"}:
        raise ValueError("invalid Bybit market")
    if not BYBIT_PAIR_RE.fullmatch(pair):
        raise ValueError("invalid Bybit pair")
    if interval not in BYBIT_INTERVALS:
        raise ValueError("invalid Bybit interval")
    try:
        start = max(0, int((query.get("start") or ["0"])[0]))
        end = max(0, int((query.get("end") or ["0"])[0]))
        limit = min(1000, max(1, int((query.get("limit") or ["1000"])[0])))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid Bybit candle range") from error
    if not start or not end or end < start:
        raise ValueError("invalid Bybit candle range")
    params = {
        "category": "linear" if market == "futures" else "spot",
        "symbol": pair,
        "interval": BYBIT_INTERVALS[interval],
        "start": str(start),
        "end": str(end),
        "limit": str(limit),
    }
    return f"https://api.bybit.com/v5/market/kline?{urlencode(params)}"


def fetch_bybit_candles(query):
    url = bybit_candle_request_url(query)
    payload = fetch_json_request(url, "Bybit")
    if not isinstance(payload, dict) or int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(str(payload.get("retMsg") or "Bybit returned an invalid candle payload"))
    return payload


def fetch_json_request(url, label):
    try:
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 DragonWave/1.0"}
        if requests is not None:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        request = Request(url, headers=headers)
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (ValueError, HTTPError, URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"{label} candle service unavailable: {error}") from error


def exchange_candle_query(query, label, intervals, maximum_limit):
    market = str((query.get("market") or [""])[0]).lower().strip()
    pair = str((query.get("pair") or [""])[0]).upper().strip()
    interval = str((query.get("interval") or [""])[0]).strip()
    if market not in {"futures", "spot"}:
        raise ValueError(f"invalid {label} market")
    if not BYBIT_PAIR_RE.fullmatch(pair):
        raise ValueError(f"invalid {label} pair")
    if interval not in intervals:
        raise ValueError(f"invalid {label} interval")
    try:
        start = max(0, int((query.get("start") or ["0"])[0]))
        end = max(0, int((query.get("end") or ["0"])[0]))
        limit = min(maximum_limit, max(1, int((query.get("limit") or [str(maximum_limit)])[0])))
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid {label} candle range") from error
    if not start or not end or end < start:
        raise ValueError(f"invalid {label} candle range")
    return market, pair, interval, start, end, limit


def fetch_okx_candles(query):
    market, pair, interval, _start, end, limit = exchange_candle_query(query, "OKX", OKX_INTERVALS, 300)
    base = re.sub(r"(?:USDT|USDC)$", "", pair)
    inst_id = f"{base}-USDT-SWAP" if market == "futures" else f"{base}-USDT"
    params = {
        "instId": inst_id,
        "bar": OKX_INTERVALS[interval],
        "after": str(end),
        "limit": str(limit),
    }
    return fetch_json_request(f"https://www.okx.com/api/v5/market/history-candles?{urlencode(params)}", "OKX")


def fetch_bitget_candles(query):
    market, pair, interval, start, end, limit = exchange_candle_query(query, "Bitget", BITGET_FUTURES_INTERVALS, 200)
    base = re.sub(r"(?:USDT|USDC)$", "", pair)
    symbol = f"{base}USDT"
    if market == "futures":
        params = {
            "symbol": symbol,
            "productType": "USDT-FUTURES",
            "granularity": BITGET_FUTURES_INTERVALS[interval],
            "startTime": str(start),
            "endTime": str(end),
            "limit": str(limit),
        }
        url = f"https://api.bitget.com/api/v2/mix/market/history-candles?{urlencode(params)}"
    else:
        params = {
            "symbol": symbol,
            "granularity": BITGET_SPOT_INTERVALS[interval],
            "endTime": str(end),
            "limit": str(min(200, limit)),
        }
        url = f"https://api.bitget.com/api/v2/spot/market/history-candles?{urlencode(params)}"
    return fetch_json_request(url, "Bitget")


def fetch_gate_candles(query):
    market, pair, interval, start, end, limit = exchange_candle_query(query, "Gate", GATE_INTERVALS, 1000)
    if market != "spot":
        raise ValueError("invalid Gate market")
    base = re.sub(r"^1000", "", re.sub(r"(?:USDT|USDC)$", "", pair))
    params = {
        "currency_pair": f"{base}_USDT",
        "interval": interval,
        "from": str(start // 1000),
        "to": str(end // 1000),
        "limit": str(limit),
    }
    return fetch_json_request(f"https://api.gateio.ws/api/v4/spot/candlesticks?{urlencode(params)}", "Gate")


def fetch_mexc_candles(query):
    market, pair, interval, start, end, limit = exchange_candle_query(query, "MEXC", MEXC_INTERVALS, 1000)
    if market != "spot":
        raise ValueError("invalid MEXC market")
    params = {
        "symbol": pair,
        "interval": MEXC_INTERVALS[interval],
        "startTime": str(start),
        "endTime": str(end),
        "limit": str(limit),
    }
    return fetch_json_request(f"https://api.mexc.com/api/v3/klines?{urlencode(params)}", "MEXC")


def precomputed_lookup(query):
    version = str((query.get("version") or [""])[0]).strip()
    pair = str((query.get("pair") or [""])[0]).upper().strip()
    start = str((query.get("start") or [""])[0]).strip()
    end = str((query.get("end") or [""])[0]).strip()
    interval = str((query.get("interval") or [""])[0]).strip()
    market = str((query.get("market") or ["futures"])[0]).lower().strip()
    stage = str((query.get("stage") or ["active"])[0]).lower().strip()
    if not PRECOMPUTED_VERSION_RE.fullmatch(version):
        raise ValueError("invalid precomputed version")
    if not PRECOMPUTED_PAIR_RE.fullmatch(pair):
        raise ValueError("invalid precomputed pair")
    if not PRECOMPUTED_DATE_RE.fullmatch(start) or not PRECOMPUTED_DATE_RE.fullmatch(end):
        raise ValueError("invalid precomputed date range")
    if interval not in PRECOMPUTED_INTERVALS:
        raise ValueError("invalid precomputed interval")
    if market not in {"futures", "spot"}:
        raise ValueError("invalid precomputed market")
    if stage not in {"active", "expected", "auto"}:
        raise ValueError("invalid precomputed stage")
    version_root = (PRECOMPUTED_ROOT / version).resolve()
    if version_root.parent != PRECOMPUTED_ROOT.resolve():
        raise ValueError("invalid precomputed path")
    manifest_path = version_root / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    key = "|".join((version, pair, start, end, interval, market, stage))
    record = (manifest.get("records") or {}).get(key)
    file_name = str((record or {}).get("file") or "")
    if not re.fullmatch(r"[0-9a-f]{64}\.json\.gz", file_name):
        return None
    file_path = (version_root / file_name).resolve()
    if file_path.parent != version_root or not file_path.is_file():
        return None
    return file_path, record


def normalize_feedback_document(value):
    payload = value if isinstance(value, dict) else {}
    raw_records = payload.get("records") if isinstance(payload.get("records"), dict) else {}
    records = {}
    ordered = sorted(
        raw_records.items(),
        key=lambda item: int((item[1] or {}).get("updatedAt") or 0) if isinstance(item[1], dict) else 0,
        reverse=True,
    )[:1200]
    for raw_key, raw_record in ordered:
        if not isinstance(raw_record, dict):
            continue
        key = str(raw_key or raw_record.get("key") or "")[:240]
        decision = str(raw_record.get("decision") or "")
        if not key or decision not in {"confirmed", "pending", "denied", "cleared"}:
            continue
        signal = dict(raw_record.get("signal")) if isinstance(raw_record.get("signal"), dict) else {}
        certainty_grade = "" if decision == "cleared" else str(raw_record.get("certaintyGrade") or signal.get("manualCertaintyGrade") or "").upper().strip()
        if certainty_grade not in {"A+", "A", "B"}:
            certainty_grade = ""
        if certainty_grade:
            signal["manualCertaintyGrade"] = certainty_grade
        else:
            signal.pop("manualCertaintyGrade", None)
        raw_structure_tags = raw_record.get("structureTags")
        if not isinstance(raw_structure_tags, list):
            raw_structure_tags = signal.get("manualStructureTags") if isinstance(signal.get("manualStructureTags"), list) else []
        structure_tags = [] if decision == "cleared" else list(dict.fromkeys(str(item) for item in raw_structure_tags if str(item) in FEEDBACK_STRUCTURE_TAGS))
        if structure_tags:
            signal["manualStructureTags"] = structure_tags
        else:
            signal.pop("manualStructureTags", None)
        raw_predicted_tags = raw_record.get("predictedStructureTags")
        if not isinstance(raw_predicted_tags, list):
            raw_predicted_tags = signal.get("predictedStructureTags") if isinstance(signal.get("predictedStructureTags"), list) else None
        predicted_tags = None if raw_predicted_tags is None else ([] if decision == "cleared" else list(dict.fromkeys(str(item) for item in raw_predicted_tags if str(item) in FEEDBACK_STRUCTURE_TAGS)))
        structure_review = None
        if predicted_tags is not None and decision != "cleared":
            matched = sorted(tag for tag in predicted_tags if tag in structure_tags)
            added = sorted(tag for tag in structure_tags if tag not in predicted_tags)
            removed = sorted(tag for tag in predicted_tags if tag not in structure_tags)
            union_size = len(set(predicted_tags + structure_tags))
            structure_review = {
                "predicted": sorted(predicted_tags), "reviewed": sorted(structure_tags), "matched": matched,
                "addedByUser": added, "removedByUser": removed,
                "agreement": round(len(matched) / union_size, 3) if union_size else 1,
                "exact": not added and not removed,
            }
            signal["predictedStructureTags"] = predicted_tags
            signal["structureReview"] = structure_review
        else:
            signal.pop("predictedStructureTags", None)
            signal.pop("structureReview", None)
        records[key] = {
            "key": key,
            "decision": decision,
            "optimizationLabel": 1 if decision == "confirmed" else -1 if decision == "denied" else 0,
            "optimizationRole": "positive" if decision == "confirmed" else "negative" if decision == "denied" else "unlabeled" if decision == "pending" else "deleted",
            "datasetVersion": FEEDBACK_DATASET_VERSION,
            "createdAt": max(0, int(raw_record.get("createdAt") or 0)),
            "updatedAt": max(0, int(raw_record.get("updatedAt") or 0)),
            "pair": str(raw_record.get("pair") or "")[:32],
            "interval": str(raw_record.get("interval") or "")[:8],
            "venue": str(raw_record.get("venue") or "")[:80],
            "certaintyGrade": certainty_grade,
            "structureTags": structure_tags,
            **({"predictedStructureTags": predicted_tags, "structureReview": structure_review} if predicted_tags is not None else {}),
            "signal": signal,
        }
    updated_at = max([int(item.get("updatedAt") or 0) for item in records.values()] or [0])
    return {"version": 1, "updatedAt": updated_at, "records": records}


def feedback_feature_tokens(signal):
    signal = signal if isinstance(signal, dict) else {}
    foundations = sorted({str(item) for item in signal.get("foundationTypes", []) if item is not None}) if isinstance(signal.get("foundationTypes"), list) else []
    auxiliaries = sorted({str(item) for item in signal.get("auxiliaryTypes", []) if item is not None}) if isinstance(signal.get("auxiliaryTypes"), list) else []
    confluence = sorted({str(item) for item in signal.get("confluence", []) if item is not None}) if isinstance(signal.get("confluence"), list) else []
    interval = str(signal.get("interval") or "").strip()
    structure_shape = str(signal.get("structureShape") or "none").strip() or "none"
    tokens = [f"foundation:{item}" for item in foundations]
    tokens.extend(f"auxiliary:{item}" for item in auxiliaries)
    if signal.get("patternKey"):
        tokens.append(f"pattern:{signal['patternKey']}")
    if confluence:
        tokens.append(f"combo:{'+'.join(confluence)}")
    if interval:
        tokens.append(f"interval:{interval}")
        if foundations and "manualReview" not in foundations:
            tokens.append(f"interval-foundation:{interval}|{'+'.join(foundations)}")
            tokens.append(f"interval-auxiliary:{interval}|{'+'.join(auxiliaries) or 'none'}")
            tokens.append(f"interval-shape:{interval}|{structure_shape}")
            tokens.append(f"interval-setup:{interval}|{'+'.join(foundations)}>{'+'.join(auxiliaries) or 'none'}|{structure_shape}")
    context_tokens = sorted({str(item) for item in signal.get("contextTokens", []) if item is not None}) if isinstance(signal.get("contextTokens"), list) else []
    tokens.extend(f"context:{item}" for item in context_tokens)
    structure_tags = sorted({str(item) for item in signal.get("manualStructureTags", []) if str(item) in FEEDBACK_STRUCTURE_TAGS}) if isinstance(signal.get("manualStructureTags"), list) else []
    tokens.extend(f"manual-structure:{item}" for item in structure_tags)
    predicted_tags = sorted({str(item) for item in signal.get("predictedStructureTags", []) if str(item) in FEEDBACK_STRUCTURE_TAGS}) if isinstance(signal.get("predictedStructureTags"), list) else []
    tokens.extend(f"strategy-structure:{item}" for item in predicted_tags)
    review = signal.get("structureReview") if isinstance(signal.get("structureReview"), dict) else None
    if review:
        for field, prefix in (("matched", "review-match"), ("addedByUser", "review-added"), ("removedByUser", "review-removed")):
            values = sorted({str(item) for item in review.get(field, []) if str(item) in FEEDBACK_STRUCTURE_TAGS}) if isinstance(review.get(field), list) else []
            tokens.extend(f"{prefix}:{item}" for item in values)
        agreement = float(review.get("agreement") or 0)
        tokens.append(f"review-agreement:{'exact' if review.get('exact') else 'partial' if agreement >= 0.5 else 'low'}")
    def metric_band(name, field, boundaries, labels):
        try:
            number = float(signal.get(field))
        except (TypeError, ValueError):
            return
        if not math.isfinite(number):
            return
        index = next((cursor for cursor, boundary in enumerate(boundaries) if number < boundary), len(labels) - 1)
        tokens.append(f"quality:{name}:{labels[index]}")
    metric_band("base-bars", "consolidationBars", (20, 40, 80), ("short", "forming", "mature", "long"))
    metric_band("outer-edge", "outerEdgeScore", (60, 80), ("weak", "clean", "strong"))
    metric_band("ceiling-touches", "ceilingTouches", (2, 3), ("single", "double", "multiple"))
    metric_band("rhythm", "rhythmScore", (60, 75), ("weak", "flowing", "elite"))
    metric_band("certainty", "certaintyScore", (70, 85), ("low", "high", "elite"))
    metric_band("order-flow", "orderFlowScore", (60, 75), ("quiet", "supportive", "strong"))
    metric_band("launch-distance", "launchDistancePercent", (2, 7), ("attached", "near", "far"))
    metric_band("prior-range", "priorRangePercent", (4, 8), ("tight", "controlled", "wide"))
    metric_band("prior-drift", "priorDriftPercent", (2, 6), ("flat", "controlled", "trending"))
    metric_band("prior-volume", "priorVolumeRatio", (1, 1.35), ("dry", "normal", "expanding"))
    metric_band("channel-occupancy", "channelInteriorOccupancy", (0.5, 0.7), ("hollow", "occupied", "full"))
    metric_band("channel-hollow", "channelHollowRatio", (0.25, 0.42), ("low", "moderate", "high"))
    metric_band("channel-transitions", "channelSideTransitions", (2, 5), ("single-side", "rotating", "active"))
    if signal.get("outerEdgeConfirmed") is True:
        tokens.append("quality:outer-edge-confirmed")
    if signal.get("aboveEma90") is True:
        tokens.append("quality:above-ema90")
    if signal.get("breaksPriorHigh") is True:
        tokens.append("quality:breaks-prior-high")
    grade = str(signal.get("manualCertaintyGrade") or "").upper().strip()
    if grade in {"A+", "A", "B"}:
        tokens.append(f"manual-grade:{grade}")
    return list(dict.fromkeys(tokens))


def feedback_supervised_prototype_profile(rows):
    def percentile(values, ratio):
        ordered = sorted(float(value) for value in values)
        if not ordered:
            return 0
        position = (len(ordered) - 1) * ratio
        lower = int(position)
        upper = min(len(ordered) - 1, lower + 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    def build(selected, sample_key):
        groups = {}
        for row in selected:
            tags = sorted(row.get("structureTags") or [])
            setup_token = next((token for token in row.get("featureTokens", []) if token.startswith("interval-setup:")), "")
            setup_signature = setup_token.split("|", 1)[1] if "|" in setup_token else "none>none|none"
            key = f"{row['interval']}|manual:{'+'.join(tags)}" if tags else f"{row['interval']}|setup:{setup_signature}"
            group = groups.setdefault(key, {"interval": row["interval"], "structureTags": tags, "setupSignature": setup_signature, "rows": [], "pairs": set()})
            group["rows"].append(row)
            group["pairs"].add(row["pair"])
        prototypes = []
        for key, group in groups.items():
            metric_names = sorted({name for row in group["rows"] for name in row.get("metrics", {}) if name != "manualCertaintyLevel"})
            metrics = {}
            for name in metric_names:
                values = [row["metrics"][name] for row in group["rows"] if name in row.get("metrics", {})]
                metrics[name] = {"low": percentile(values, 0.25), "median": percentile(values, 0.5), "high": percentile(values, 0.75)}
            counts = {}
            for row in group["rows"]:
                for token in row.get("featureTokens", []):
                    if token.startswith(("quality:", "context:", "manual-structure:", "strategy-structure:", "review-")):
                        counts[token] = counts.get(token, 0) + 1
            prototypes.append({
                "key": key,
                "interval": group["interval"],
                "structureTags": group["structureTags"],
                "setupSignature": group["setupSignature"],
                "sampleCount": len(group["rows"]),
                "pairCount": len(group["pairs"]),
                "metrics": metrics,
                "sharedReasons": sorted((token for token, count in counts.items() if count / len(group["rows"]) >= 0.6), key=lambda token: (-counts[token], token)),
            })
        prototypes.sort(key=lambda item: (-item["sampleCount"], item["key"]))
        return {sample_key: sum(item["sampleCount"] for item in prototypes), "prototypes": prototypes}

    return {
        "positiveAPlus": build([row for row in rows if row["decision"] == "confirmed" and row["certaintyGrade"] == "A+"], "totalAPlusSamples"),
        "negativeDenied": build([row for row in rows if row["decision"] == "denied"], "totalDeniedSamples"),
        "policy": "causal-feature-combination-only",
    }


def feedback_optimization_dataset(value):
    normalized = normalize_feedback_document(value)
    metric_fields = (
        "score", "certaintyScore", "rhythmScore", "sentimentScore",
        "orderFlowScore", "consolidationBars", "relativeVolume", "structuralRiskPercent",
        "ceilingAge", "ceilingTouches", "outerEdgeScore",
        "platformTouchGroups", "launchDistancePercent", "compressionRatioAtDecision",
        "channelInteriorOccupancy", "channelMiddleParticipationRatio", "channelHollowRatio", "channelLongestHollowRun", "channelSideTransitions",
        "ema90AtDecision", "atrAtDecision", "priorHighAtDecision", "priorLowAtDecision",
        "priorRangePercent", "priorDriftPercent", "priorVolumeRatio", "selectedPrice",
    )
    rows = []
    for record in sorted(normalized["records"].values(), key=lambda item: (item["updatedAt"], item["key"])):
        if record["decision"] == "cleared":
            continue
        signal = record.get("signal") if isinstance(record.get("signal"), dict) else {}
        metrics = {}
        for field in metric_fields:
            try:
                number = float(signal.get(field))
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                metrics[field] = number
        certainty_grade = str(record.get("certaintyGrade") or "")
        if certainty_grade:
            metrics["manualCertaintyLevel"] = {"A+": 3, "A": 2, "B": 1}[certainty_grade]
        try:
            signal_time = max(0, int(float(signal.get("time") or 0)))
        except (TypeError, ValueError):
            signal_time = 0
        rows.append({
            "key": record["key"],
            "label": record["optimizationLabel"],
            "role": record["optimizationRole"],
            "decision": record["decision"],
            "certaintyGrade": certainty_grade,
            "structureTags": record.get("structureTags") or [],
            "predictedStructureTags": record.get("predictedStructureTags") or [],
            "structureReview": record.get("structureReview"),
            "pair": record["pair"],
            "interval": record["interval"],
            "time": signal_time,
            "updatedAt": record["updatedAt"],
            "featureTokens": feedback_feature_tokens({**signal, "interval": record["interval"] or signal.get("interval")}),
            "metrics": metrics,
            "visualSignature": signal.get("visualSignature") if isinstance(signal.get("visualSignature"), dict) else None,
        })
    tokens = {token for row in rows for token in row["featureTokens"]}
    supervised = feedback_supervised_prototype_profile(rows)
    return {
        "datasetVersion": FEEDBACK_DATASET_VERSION,
        "generatedAt": normalized["updatedAt"],
        "causality": "decision-time-features-only",
        "excludedOutcomeFields": ["futureReturn", "maxFavorableExcursion", "maxAdverseExcursion", "futureHigh", "futureLow"],
        "summary": {
            "total": len(rows),
            "positiveCount": sum(row["label"] == 1 for row in rows),
            "negativeCount": sum(row["label"] == -1 for row in rows),
            "pendingCount": sum(row["label"] == 0 for row in rows),
            "labeledCount": sum(row["label"] != 0 for row in rows),
            "featureCount": len(tokens),
            "aPlusPrototypeCount": len(supervised["positiveAPlus"]["prototypes"]),
            "aPlusSampleCount": supervised["positiveAPlus"]["totalAPlusSamples"],
            "deniedPrototypeCount": len(supervised["negativeDenied"]["prototypes"]),
            "deniedPrototypeSampleCount": supervised["negativeDenied"]["totalDeniedSamples"],
            "visualLabeledCount": sum(row["label"] != 0 and bool(row["visualSignature"]) for row in rows),
            "visualAPlusCount": sum(row["decision"] == "confirmed" and row["certaintyGrade"] == "A+" and bool(row["visualSignature"]) for row in rows),
            "visualDeniedCount": sum(row["decision"] == "denied" and bool(row["visualSignature"]) for row in rows),
        },
        "supervisedPrototypeProfile": supervised,
        "rows": rows,
    }


def merge_feedback_documents(*documents):
    merged = {"version": 1, "updatedAt": 0, "records": {}}
    for document in documents:
        normalized = normalize_feedback_document(document)
        for key, record in normalized["records"].items():
            existing = merged["records"].get(key)
            if not existing or int(record.get("updatedAt") or 0) >= int(existing.get("updatedAt") or 0):
                merged["records"][key] = record
    return normalize_feedback_document(merged)


def init_feedback_db():
    FEEDBACK_DB.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_LOCK:
        conn = sqlite3.connect(FEEDBACK_DB)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_documents (
                    device_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def load_local_feedback(device_id):
    with FEEDBACK_LOCK:
        conn = sqlite3.connect(FEEDBACK_DB)
        try:
            row = conn.execute(
                "SELECT payload FROM feedback_documents WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return normalize_feedback_document({})
    try:
        return normalize_feedback_document(json.loads(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return normalize_feedback_document({})


def save_local_feedback(device_id, incoming):
    merged = merge_feedback_documents(load_local_feedback(device_id), incoming)
    text = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    with FEEDBACK_LOCK:
        conn = sqlite3.connect(FEEDBACK_DB)
        try:
            conn.execute(
                """
                INSERT INTO feedback_documents (device_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (device_id, text, int(time.time() * 1000)),
            )
            conn.commit()
        finally:
            conn.close()
    return merged


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_precomputed(self, file_path, record):
        stat = file_path.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, max-age=86400")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("ETag", etag)
        self.send_header("X-Dragon-Wave-Precomputed", "1")
        self.send_header("X-Dragon-Wave-Candles", str(int((record or {}).get("candleCount") or 0)))
        self.end_headers()
        with file_path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("invalid request body")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        return payload

    def device_id(self, payload=None):
        parsed = urlparse(self.path)
        query_value = (parse_qs(parsed.query).get("deviceId") or [""])[0]
        value = str((payload or {}).get("deviceId") or query_value or "")
        return value if DEVICE_RE.fullmatch(value) else ""

    def proxy_account(self, method):
        if not ACCOUNT_API:
            self.send_json({"ok": False, "available": False, "error": "account sync disabled"}, 503)
            return
        data = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                self.send_json({"ok": False, "error": "invalid request body"}, 400)
                return
            data = self.rfile.read(length)
        request = Request(ACCOUNT_API, data=data, method=method)
        request.add_header("Accept", "application/json")
        request.add_header("Content-Type", "application/json")
        if self.headers.get("X-Dragon-Wave-Compact") == "1":
            request.add_header("X-Dragon-Wave-Compact", "1")
        cookie = self.headers.get("Cookie") or ""
        if cookie:
            request.add_header("Cookie", cookie)
        try:
            with urlopen(request, timeout=3) as response:
                body = response.read()
                status = response.status
        except HTTPError as error:
            body = error.read()
            status = error.code
        except (URLError, TimeoutError, OSError):
            self.send_json({"ok": False, "available": False, "error": "account service unavailable"}, 503)
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/dragon-wave-candles/binance":
            try:
                self.send_json(fetch_binance_candles(parse_qs(parsed.query)))
            except ValueError as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            except RuntimeError as error:
                self.send_json({"ok": False, "error": str(error)}, 502)
            return
        if path == "/api/dragon-wave-candles/bybit":
            try:
                self.send_json(fetch_bybit_candles(parse_qs(parsed.query)))
            except ValueError as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            except RuntimeError as error:
                self.send_json({"ok": False, "error": str(error)}, 502)
            return
        if path == "/api/dragon-wave-candles/okx":
            try:
                self.send_json(fetch_okx_candles(parse_qs(parsed.query)))
            except ValueError as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            except RuntimeError as error:
                self.send_json({"ok": False, "error": str(error)}, 502)
            return
        if path == "/api/dragon-wave-candles/bitget":
            try:
                self.send_json(fetch_bitget_candles(parse_qs(parsed.query)))
            except ValueError as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            except RuntimeError as error:
                self.send_json({"ok": False, "error": str(error)}, 502)
            return
        if path == "/api/dragon-wave-candles/gate":
            try:
                self.send_json(fetch_gate_candles(parse_qs(parsed.query)))
            except ValueError as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            except RuntimeError as error:
                self.send_json({"ok": False, "error": str(error)}, 502)
            return
        if path == "/api/dragon-wave-candles/mexc":
            try:
                self.send_json(fetch_mexc_candles(parse_qs(parsed.query)))
            except ValueError as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            except RuntimeError as error:
                self.send_json({"ok": False, "error": str(error)}, 502)
            return
        if path == "/api/dragon-wave-precomputed":
            try:
                match = precomputed_lookup(parse_qs(parsed.query))
                if not match:
                    self.send_json({"ok": False, "available": False, "error": "precomputed result not found"}, 404)
                else:
                    self.send_precomputed(*match)
            except ValueError as error:
                self.send_json({"ok": False, "available": False, "error": str(error)}, 400)
            except (OSError, ConnectionError) as error:
                self.send_json({"ok": False, "available": False, "error": f"precomputed result unavailable: {error}"}, 503)
            return
        if path == "/api/dragon-wave-feedback/local":
            device_id = self.device_id()
            if not device_id:
                self.send_json({"ok": False, "error": "invalid device id"}, 400)
                return
            feedback = load_local_feedback(device_id)
            self.send_json({"ok": True, "storage": "local-sqlite", "feedback": feedback, "optimization": feedback_optimization_dataset(feedback)})
            return
        if path == "/api/dragon-wave-feedback/account":
            self.proxy_account("GET")
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/dragon-wave-feedback/local":
            try:
                payload = self.read_json()
                device_id = self.device_id(payload)
                if not device_id:
                    raise ValueError("invalid device id")
                saved = save_local_feedback(device_id, payload.get("feedback"))
                if self.headers.get("X-Dragon-Wave-Compact") == "1":
                    self.send_json({
                        "ok": True,
                        "storage": "local-sqlite",
                        "updatedAt": saved.get("updatedAt", 0),
                        "recordCount": len(saved.get("records") or {}),
                    })
                else:
                    self.send_json({"ok": True, "storage": "local-sqlite", "feedback": saved, "optimization": feedback_optimization_dataset(saved)})
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            return
        if path == "/api/dragon-wave-feedback/account":
            self.proxy_account("POST")
            return
        self.send_json({"ok": False, "error": "not found"}, 404)


if __name__ == "__main__":
    init_feedback_db()
    ThreadingHTTPServer(("127.0.0.1", 8791), QuietHandler).serve_forever()

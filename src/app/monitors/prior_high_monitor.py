"""Causal mother-range outer-edge prior-high detector shared by the live monitor.

The implementation mirrors ``go/prior-high-monitor``.  It does not import the
ignition strategy's BTC, higher-timeframe or main-wave permission gates.  It
does, however, suppress lower pivots while a higher confirmed edge in the same
recent mother range remains overhead.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from statistics import median
from typing import Any, Iterable


VERSION = "v2-mother-range-outer-edge"

LEVEL_RANKS = {"MICRO": 1, "INTERNAL": 2, "SWING": 3, "MAJOR": 4, "HISTORICAL": 5}


@dataclass(slots=True)
class PriorHighConfig:
    atr_period: int = 14
    pivot_left: int = 4
    pivot_right: int = 3
    min_prominence_atr: float = 0.70
    identity_atr: float = 0.35
    identity_pct: float = 0.0015
    max_identity_zone_atr: float = 0.80
    spatial_reset_atr: float = 1.00
    spatial_reset_pct: float = 0.010
    temporal_reset_bars: int = 8
    temporal_below_ratio: float = 0.60
    rearm_atr: float = 1.00
    rearm_pct: float = 0.0050
    rearm_below_closes: int = 4
    rearm_min_bars: int = 4
    trigger_mode: str = "high"
    trigger_buffer_atr: float = 0.03
    trigger_buffer_pct: float = 0.0002
    internal_prominence_atr: float = 1.00
    swing_prominence_atr: float = 1.80
    major_prominence_atr: float = 3.00
    historical_lookback: int = 300
    historical_tolerance_pct: float = 0.001
    mother_range_outer_edge_only: bool = True
    mother_range_lookback_bars: int = 0
    mother_range_max_gap_atr: float = 10.0
    mother_range_max_gap_pct: float = 0.12
    mother_range_dominance_atr: float = 0.20
    mother_range_dominance_pct: float = 0.001
    stage_lookback_bars: int = 96
    alert_micro: bool = False
    near_threshold_atr: float = 0.75
    near_threshold_pct: float = 0.03


@dataclass(slots=True)
class Candle:
    time: Any
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(slots=True)
class Level:
    level_id: int
    native_tf: str
    level_class: str
    anchor: float
    zone_low: float
    zone_high: float
    created_bar: int
    confirmed_bar: int
    created_time: Any
    confirmed_time: Any
    atr_at_confirm: float
    prominence_atr: float
    touch_count: int = 1
    breakout_count: int = 0
    spatial_separated: bool = False
    temporal_separated: bool = False
    separation_complete: bool = False
    armed: bool = False
    triggered: bool = False
    near_active: bool = False
    reminded_since_arm: bool = False
    min_low_since_confirm: float = math.inf
    bars_since_confirm: int = 0
    below_close_count: int = 0
    below_close_streak: int = 0
    active: bool = True
    source_pivots: list[int] = field(default_factory=list)
    last_alert_bar: int = -1
    last_reminder_bar: int = -1

    def separation_type(self) -> str:
        if self.spatial_separated and self.temporal_separated:
            return "SPATIAL+TEMPORAL"
        if self.spatial_separated:
            return "SPATIAL"
        if self.temporal_separated:
            return "TEMPORAL"
        return "NONE"


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def normalize_candles(rows: Iterable[Any]) -> list[Candle]:
    candles: list[Candle] = []
    for index, row in enumerate(rows or []):
        if isinstance(row, dict):
            close = _number(row.get("close"))
            candle = Candle(
                time=row.get("time", row.get("openTime", index)),
                open=_number(row.get("open")),
                high=_number(row.get("high")),
                low=_number(row.get("low")),
                close=close,
                volume=_number(row.get("volume")),
            )
        elif isinstance(row, (list, tuple)) and len(row) >= 3:
            # Existing live-monitor tuple: time, high, close, open?, low?, volume?
            close = _number(row[2])
            open_price = _number(row[3]) if len(row) > 3 else close
            candle = Candle(
                time=row[0],
                open=open_price or close,
                high=_number(row[1]),
                low=(_number(row[4]) if len(row) > 4 else min(open_price or close, close)),
                close=close,
                volume=_number(row[5]) if len(row) > 5 else 0.0,
            )
        else:
            continue
        if candle.open > 0 and candle.high >= candle.low > 0 and candle.close > 0:
            candles.append(candle)
    candles.sort(key=lambda item: _number(item.time))
    return candles


def calculate_atr(candles: list[Candle], period: int) -> list[float]:
    output: list[float] = []
    true_ranges: list[float] = []
    previous_atr = 0.0
    alpha = 1 / max(1, period)
    for index, candle in enumerate(candles):
        true_range = candle.high - candle.low
        if index:
            previous_close = candles[index - 1].close
            true_range = max(
                true_range,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        true_ranges.append(true_range)
        previous_atr = true_range if not index else alpha * true_range + (1 - alpha) * previous_atr
        output.append(float(median(true_ranges)) if index + 1 < period else previous_atr)
    return output


def _price_tolerance(price: float, atr: float, atr_multiple: float, percent: float) -> float:
    return max(atr_multiple * atr if math.isfinite(atr) else 0.0, percent * abs(price), 1e-12)


def _default_mother_range_lookback(native_tf: str) -> int:
    normalized = str(native_tf or "").lower()
    return {
        "1m": 10_080,
        "5m": 2_016,
        "15m": 672,
        "30m": 336,
        "1h": 168,
        "60m": 168,
        "4h": 42,
        "240m": 42,
        "1d": 30,
        "1day": 30,
    }.get(normalized, 168)


def _level_dict(level: Level) -> dict[str, Any]:
    raw = asdict(level)
    return {
        "levelId": raw["level_id"],
        "nativeTf": raw["native_tf"],
        "levelClass": raw["level_class"],
        "anchor": raw["anchor"],
        "zoneLow": raw["zone_low"],
        "zoneHigh": raw["zone_high"],
        "createdBar": raw["created_bar"],
        "confirmedBar": raw["confirmed_bar"],
        "createdTime": raw["created_time"],
        "confirmedTime": raw["confirmed_time"],
        "atrAtConfirm": raw["atr_at_confirm"],
        "prominenceAtr": raw["prominence_atr"],
        "touchCount": raw["touch_count"],
        "breakoutCount": raw["breakout_count"],
        "spatialSeparated": raw["spatial_separated"],
        "temporalSeparated": raw["temporal_separated"],
        "separationComplete": raw["separation_complete"],
        "separationType": level.separation_type(),
        "armed": raw["armed"],
        "triggered": raw["triggered"],
        "active": raw["active"],
        "sourcePivots": raw["source_pivots"],
    }


def _stage_high_reference(level: Level, candles: list[Candle]) -> tuple[float, int, Any]:
    """Return the latest confirmed pivot inside one resistance identity zone.

    ``anchor`` remains frozen for identity/event state, but the live prior-high
    monitor must follow the high of the latest completed main-wave stage.  A
    later confirmed pivot in the same zone is therefore the displayed and
    monitored reference instead of an older anchor or a nearby internal wiggle.
    """
    pivots = [index for index in level.source_pivots if 0 <= index < len(candles)]
    pivot = max(pivots, default=level.created_bar)
    if 0 <= pivot < len(candles):
        candle = candles[pivot]
        return candle.high, pivot, candle.time
    return level.anchor, level.created_bar, level.created_time


def _select_stage_high_level(
    levels: list[Level], candles: list[Candle], cfg: PriorHighConfig
) -> dict[str, Any] | None:
    """Select the next overhead high belonging to a meaningful rise stage.

    SWING/MAJOR/HISTORICAL levels take precedence over INTERNAL levels.  This
    prevents a tiny local pivot close to spot from replacing the actual stage
    high.  INTERNAL remains a fallback so short-history assets are not muted.
    """
    if not candles:
        return None
    current = candles[-1].close
    eligible: list[tuple[Level, float, int, Any]] = []
    for level in levels:
        if not (
            level.active
            and level.separation_complete
            and level.armed
            and not level.triggered
            and (cfg.alert_micro or level.level_class != "MICRO")
        ):
            continue
        reference, pivot, reference_time = _stage_high_reference(level, candles)
        if reference >= current:
            eligible.append((level, reference, pivot, reference_time))
    if not eligible:
        return None

    structural = [
        item for item in eligible
        if LEVEL_RANKS.get(item[0].level_class, 0) >= LEVEL_RANKS["SWING"]
    ]
    stage_window_start = max(0, len(candles) - max(12, cfg.stage_lookback_bars))
    stage_floor = min(
        range(stage_window_start, len(candles)),
        key=lambda index: candles[index].low,
    )
    stage_structural = [item for item in structural if item[2] >= stage_floor]
    if stage_structural:
        # One recovery/main-wave stage can contain several later lower highs.
        # The phase high remains the highest confirmed pivot after the stage
        # floor until price truly breaks it; do not replace it with the latest
        # smaller rebound high merely because that level is closer to spot.
        pool = stage_structural
        pool.sort(key=lambda item: (
            -item[1],
            -LEVEL_RANKS.get(item[0].level_class, 0),
            -item[2],
        ))
    else:
        pool = structural or eligible
        pool.sort(key=lambda item: (
            abs(item[1] - current),
            -LEVEL_RANKS.get(item[0].level_class, 0),
            -item[2],
        ))
    level, reference, pivot, reference_time = pool[0]
    selected = _level_dict(level)
    selected.update({
        "referenceAnchor": reference,
        "referenceBar": pivot,
        "referenceTime": reference_time,
        # This detector selects the structurally meaningful stage high.  The
        # caller still has to prove that the surrounding market regime is a
        # real main wave before it may label or alert it as such.
        "referenceKind": "STAGE_HIGH_CANDIDATE",
    })
    return selected


def analyze_prior_high(
    rows: Iterable[Any], native_tf: str = "1h", config: PriorHighConfig | None = None
) -> dict[str, Any]:
    cfg = config or PriorHighConfig()
    candles = normalize_candles(rows)
    if len(candles) < cfg.pivot_left + cfg.pivot_right + 5:
        return {
            "version": VERSION,
            "nativeTf": native_tf,
            "candles": candles,
            "atr": calculate_atr(candles, cfg.atr_period),
            "levels": [],
            "breakouts": [],
            "reminders": [],
            "actionableLevel": None,
            "reason": "insufficient-candles",
        }

    atr = calculate_atr(candles, cfg.atr_period)
    mother_range_lookback_bars = (
        cfg.mother_range_lookback_bars
        if cfg.mother_range_lookback_bars > 0
        else _default_mother_range_lookback(native_tf)
    )
    levels: list[Level] = []
    breakouts: list[dict[str, Any]] = []
    reminders: list[dict[str, Any]] = []
    next_level_id = 1

    def is_pivot_high(pivot: int, confirmed_at: int) -> bool:
        if pivot - cfg.pivot_left < 0 or pivot + cfg.pivot_right > confirmed_at:
            return False
        high = candles[pivot].high
        return (
            all(high >= candles[index].high for index in range(pivot - cfg.pivot_left, pivot))
            and all(high > candles[index].high for index in range(pivot + 1, pivot + cfg.pivot_right + 1))
        )

    def prominence_atr(pivot: int, confirmed_at: int) -> float:
        left_low = min(candles[index].low for index in range(max(0, pivot - cfg.pivot_left), pivot + 1))
        right_low = min(candles[index].low for index in range(pivot, min(confirmed_at, pivot + cfg.pivot_right) + 1))
        prominence = max(0.0, min(candles[pivot].high - left_low, candles[pivot].high - right_low))
        return prominence / max(atr[confirmed_at], 1e-12)

    def classify_level(pivot: int, prominence: float) -> str:
        price = candles[pivot].high
        start = max(0, pivot - cfg.historical_lookback)
        if pivot > start:
            previous_maximum = max(candles[index].high for index in range(start, pivot))
            if previous_maximum > 0 and price >= previous_maximum * (1 - cfg.historical_tolerance_pct):
                return "HISTORICAL"
        if prominence >= cfg.major_prominence_atr:
            return "MAJOR"
        if prominence >= cfg.swing_prominence_atr:
            return "SWING"
        if prominence >= cfg.internal_prominence_atr:
            return "INTERNAL"
        return "MICRO"

    def add_or_merge_pivot(pivot: int, confirmed_at: int) -> None:
        nonlocal next_level_id
        prominence = prominence_atr(pivot, confirmed_at)
        if prominence < cfg.min_prominence_atr:
            return
        price = candles[pivot].high
        current_atr = atr[confirmed_at]
        tolerance = _price_tolerance(price, current_atr, cfg.identity_atr, cfg.identity_pct)
        targets = [level for level in levels if level.active and abs(price - level.anchor) <= tolerance]
        target = min(targets, key=lambda level: abs(price - level.anchor), default=None)
        level_class = classify_level(pivot, prominence)
        if target:
            target.touch_count += 1
            target.source_pivots.append(pivot)
            target.prominence_atr = max(target.prominence_atr, prominence)
            if LEVEL_RANKS[level_class] > LEVEL_RANKS[target.level_class]:
                target.level_class = level_class
            candidate_low = min(target.zone_low, price)
            candidate_high = max(target.zone_high, price)
            if candidate_high - candidate_low <= cfg.max_identity_zone_atr * max(current_atr, 1e-12):
                target.zone_low = candidate_low
                target.zone_high = candidate_high
            return
        levels.append(Level(
            level_id=next_level_id,
            native_tf=native_tf,
            level_class=level_class,
            anchor=price,
            zone_low=price,
            zone_high=price,
            created_bar=pivot,
            confirmed_bar=confirmed_at,
            created_time=candles[pivot].time,
            confirmed_time=candles[confirmed_at].time,
            atr_at_confirm=current_atr,
            prominence_atr=prominence,
            source_pivots=[pivot],
        ))
        next_level_id += 1

    def process_level(level: Level, index: int) -> None:
        if not level.active or index <= level.confirmed_bar:
            return
        candle = candles[index]
        reference = max(level.anchor, level.zone_high)
        level.bars_since_confirm += 1
        level.min_low_since_confirm = min(level.min_low_since_confirm, candle.low)
        if candle.close < reference:
            level.below_close_count += 1
        spatial_gap = _price_tolerance(reference, atr[index], cfg.spatial_reset_atr, cfg.spatial_reset_pct)
        if level.min_low_since_confirm <= reference - spatial_gap:
            level.spatial_separated = True
        if (
            level.bars_since_confirm >= cfg.temporal_reset_bars
            and level.below_close_count / max(1, level.bars_since_confirm) >= cfg.temporal_below_ratio
        ):
            level.temporal_separated = True
        level.separation_complete = level.spatial_separated or level.temporal_separated
        if level.separation_complete and not level.triggered:
            level.armed = True

        if level.triggered:
            level.below_close_streak = level.below_close_streak + 1 if candle.close < reference else 0
            rearm_gap = _price_tolerance(reference, atr[index], cfg.rearm_atr, cfg.rearm_pct)
            reset_mature = index - level.last_alert_bar >= cfg.rearm_min_bars
            if reset_mature and (
                candle.low <= reference - rearm_gap
                or level.below_close_streak >= cfg.rearm_below_closes
            ):
                level.triggered = False
                level.armed = True
                level.near_active = False
                level.reminded_since_arm = False

        if not level.armed or not level.separation_complete or index <= 0:
            return
        if cfg.mother_range_outer_edge_only:
            start = max(0, index - mother_range_lookback_bars)
            max_gap = _price_tolerance(
                reference, atr[index], cfg.mother_range_max_gap_atr, cfg.mother_range_max_gap_pct
            )
            dominance = _price_tolerance(
                reference,
                atr[index],
                cfg.mother_range_dominance_atr,
                cfg.mother_range_dominance_pct,
            )
            higher_mother_edge = any(
                other is not level
                and other.active
                and other.confirmed_bar < index
                and other.created_bar >= start
                and max(other.anchor, other.zone_high) > reference + dominance
                and max(other.anchor, other.zone_high) - reference <= max_gap
                for other in levels
            )
            if higher_mother_edge:
                level.near_active = False
                return

        trigger_buffer = _price_tolerance(reference, atr[index], cfg.trigger_buffer_atr, cfg.trigger_buffer_pct)
        threshold = reference + trigger_buffer
        previous_close = candles[index - 1].close
        trigger_price = candle.close if cfg.trigger_mode == "close" else candle.high
        crossed = previous_close <= threshold < trigger_price
        if crossed and level.last_alert_bar != index:
            level.breakout_count += 1
            level.armed = False
            level.triggered = True
            level.near_active = False
            level.below_close_streak = 0
            level.last_alert_bar = index
            if level.level_class != "MICRO" or cfg.alert_micro:
                breakouts.append({
                    "barIndex": index,
                    "time": candle.time,
                    "nativeTf": native_tf,
                    "levelId": level.level_id,
                    "levelClass": level.level_class,
                    "anchor": reference,
                    "zoneLow": level.zone_low,
                    "zoneHigh": level.zone_high,
                    "triggerPrice": trigger_price,
                    "breakoutCount": level.breakout_count,
                    "separationType": level.separation_type(),
                    "spatialSeparated": level.spatial_separated,
                    "temporalSeparated": level.temporal_separated,
                    "prominenceAtr": level.prominence_atr,
                    "touchCount": level.touch_count,
                })
            return

        near_gap = _price_tolerance(level.anchor, atr[index], cfg.near_threshold_atr, cfg.near_threshold_pct)
        near_floor = threshold - near_gap
        if candle.close < near_floor:
            level.near_active = False
        entered_near = (
            not level.near_active
            and not level.reminded_since_arm
            and previous_close < near_floor <= candle.high
            and trigger_price <= threshold
        )
        if entered_near and level.last_reminder_bar != index and (level.level_class != "MICRO" or cfg.alert_micro):
            level.near_active = True
            level.reminded_since_arm = True
            level.last_reminder_bar = index
            reminders.append({
                "barIndex": index,
                "time": candle.time,
                "nativeTf": native_tf,
                "levelId": level.level_id,
                "levelClass": level.level_class,
                "anchor": reference,
                "triggerPrice": threshold,
                "currentPrice": candle.close,
                "distancePct": max(0.0, (reference - candle.close) / reference * 100),
                "separationType": level.separation_type(),
                "touchCount": level.touch_count,
            })

    for index in range(len(candles)):
        pivot = index - cfg.pivot_right
        if pivot >= 0 and is_pivot_high(pivot, index):
            add_or_merge_pivot(pivot, index)
        for level in sorted(levels, key=lambda item: item.level_id):
            process_level(level, index)

    def keep_highest_outer_edge_per_bar(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: dict[int, dict[str, Any]] = {}
        for event in events:
            current = selected.get(event["barIndex"])
            if current is None or event["anchor"] > current["anchor"]:
                selected[event["barIndex"]] = event
        return [selected[index] for index in sorted(selected)]

    breakouts = keep_highest_outer_edge_per_bar(breakouts)
    reminders = keep_highest_outer_edge_per_bar(reminders)

    last_index = len(candles) - 1
    final_breakout = next((event for event in reversed(breakouts) if event["barIndex"] == last_index), None)
    selected = _select_stage_high_level(levels, candles, cfg)

    return {
        "version": VERSION,
        "nativeTf": native_tf,
        "atr": atr,
        "levels": [_level_dict(level) for level in levels],
        "breakouts": breakouts,
        "reminders": reminders,
        "actionableLevel": selected,
        "lastBreakout": final_breakout,
        "reason": "structure-level-ready" if selected else "waiting-structure-level",
    }

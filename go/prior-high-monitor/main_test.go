package main

import (
	"fmt"
	"testing"
)

func testCandle(index int, open, high, low, close float64) Candle {
	return Candle{
		TimeRaw: fmt.Sprintf("%d", index),
		Open:    open,
		High:    high,
		Low:     low,
		Close:   close,
		Volume:  100,
	}
}

func compactTestConfig() Config {
	cfg := DefaultConfig()
	cfg.ATRPeriod = 3
	cfg.PivotLeft = 2
	cfg.PivotRight = 2
	cfg.MinProminenceATR = 0.35
	cfg.InternalProminenceATR = 0.5
	cfg.SwingProminenceATR = 1
	cfg.MajorProminenceATR = 2
	cfg.TemporalResetBars = 3
	cfg.TemporalBelowRatio = 0.66
	cfg.SpatialResetATR = 0.8
	cfg.SpatialResetPct = 0.01
	cfg.TriggerBufferATR = 0
	cfg.TriggerBufferPct = 0
	cfg.AlertMicro = true
	return cfg
}

func TestFreshCrossAfterSeparation(t *testing.T) {
	rows := []Candle{
		testCandle(0, 95, 96, 94, 95),
		testCandle(1, 96, 98, 95, 97),
		testCandle(2, 98, 100, 97, 99),
		testCandle(3, 98, 99, 96, 97),
		testCandle(4, 96, 98, 94, 95),
		testCandle(5, 95, 97, 93, 94),
		testCandle(6, 94, 98, 93, 97),
		testCandle(7, 97, 101, 96, 100.5),
		testCandle(8, 100.5, 101, 99, 100),
	}
	detector := NewDetector(compactTestConfig(), "5m")
	events, err := detector.Run(rows)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 {
		t.Fatalf("wanted 1 event, got %d: %#v", len(events), events)
	}
	if events[0].BarIndex != 7 || events[0].Anchor != 100 {
		t.Fatalf("unexpected event: %#v", events[0])
	}
	if events[0].SeparationType == "NONE" {
		t.Fatal("breakout must follow spatial or temporal separation")
	}
}

func TestHigherMotherEdgeBlocksLowerRebreak(t *testing.T) {
	rows := []Candle{
		testCandle(0, 95, 96, 94, 95), testCandle(1, 96, 98, 95, 97),
		testCandle(2, 98, 100, 97, 99), testCandle(3, 98, 99, 96, 97),
		testCandle(4, 96, 98, 94, 95), testCandle(5, 95, 97, 93, 94),
		testCandle(6, 94, 98, 93, 97), testCandle(7, 97, 101, 96, 100.5),
		testCandle(8, 100.5, 102, 100, 101), testCandle(9, 101, 102, 99.5, 100.5),
		testCandle(10, 100, 100.2, 98, 99), testCandle(11, 99, 99.5, 97.5, 98.5),
		testCandle(12, 98.5, 101.5, 98, 101),
	}
	detector := NewDetector(compactTestConfig(), "5m")
	events, err := detector.Run(rows)
	if err != nil {
		t.Fatal(err)
	}
	var same []BreakoutEvent
	for _, event := range events {
		if event.Anchor == 100 {
			same = append(same, event)
		}
	}
	if len(same) != 1 || same[0].BarIndex != 7 {
		t.Fatalf("wanted only the first crossing before the higher edge formed, got %#v", same)
	}
	if same[0].BreakoutCount != 1 {
		t.Fatalf("unexpected breakout counts: %#v", same)
	}
}

func TestMotherRangeHidesInternalCrossesUntilOuterEdgeBreaks(t *testing.T) {
	rows := []Candle{
		testCandle(0, 100, 102, 99, 101), testCandle(1, 101, 105, 100, 104),
		testCandle(2, 104, 110, 103, 108), testCandle(3, 108, 106, 102, 104),
		testCandle(4, 104, 103, 100, 101), testCandle(5, 101, 102, 99, 100),
		testCandle(6, 100, 105, 99.5, 104), testCandle(7, 104, 103, 100, 101),
		testCandle(8, 101, 102, 99, 100), testCandle(9, 100, 106, 99.5, 105),
		testCandle(10, 105, 104, 101, 102), testCandle(11, 102, 103, 100, 101),
		testCandle(12, 101, 111, 100.5, 110.5),
	}
	detector := NewDetector(compactTestConfig(), "15m")
	events, err := detector.Run(rows)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 || events[0].BarIndex != 12 || events[0].Anchor != 110 {
		t.Fatalf("wanted only the true mother-range outer-edge break, got %#v", events)
	}
}

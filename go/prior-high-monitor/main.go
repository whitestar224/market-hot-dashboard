package main

import (
	"bufio"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ============================================================
// 结构前高突破提醒器
// ============================================================
//
// 目标：
//   只判断“一个有结构意义的前高，在经历空间分离或时间分离后，
//   是否再次从下方向上穿越”。
//
// 明确保留：
//   - 慢慢爬回前高
//   - 宽幅震荡后重新穿越
//   - 深V回升
//   - 多次重新突破
//
// 明确不作为硬过滤：
//   - 放量
//   - VCP
//   - 低点抬高
//   - 突破以后是否上涨
//
// 核心机制：
//   1) 因果 Pivot 确认（无未来函数）
//   2) Level Identity Lock（附近高点归为同一前高）
//   3) 空间分离 OR 时间分离
//   4) 母盘整外沿审查（较低内部枢轴不提醒）
//   5) Fresh Crossing
//   6) Re-arm（防止前高附近 tick 抖动刷屏）
//   7) Level 分层：MICRO / INTERNAL / SWING / MAJOR / HISTORICAL
//
// 依赖：只有 Go 标准库。
//
// 用法：
//   go run prior_high_monitor.go -csv candles.csv -tf 5m
//
// CSV 至少需要：
//   time,open,high,low,close
// volume 可有可无。
// time 支持 RFC3339、Unix 秒、Unix 毫秒，解析失败会原样保留。
// ============================================================

type LevelClass string

const (
	LevelMicro      LevelClass = "MICRO"
	LevelInternal   LevelClass = "INTERNAL"
	LevelSwing      LevelClass = "SWING"
	LevelMajor      LevelClass = "MAJOR"
	LevelHistorical LevelClass = "HISTORICAL"
)

type TriggerMode string

const (
	TriggerHigh  TriggerMode = "high"
	TriggerClose TriggerMode = "close"
)

type Config struct {
	ATRPeriod int

	PivotLeft  int
	PivotRight int

	MinProminenceATR float64

	// Level Identity Lock
	IdentityATR float64
	IdentityPct float64

	// 附近 Pivot 允许归入同一 Level 的最大总宽度
	MaxIdentityZoneATR float64

	// 空间分离
	SpatialResetATR float64
	SpatialResetPct float64

	// 时间分离
	TemporalResetBars  int
	TemporalBelowRatio float64

	// Re-arm
	RearmATR         float64
	RearmPct         float64
	RearmBelowCloses int
	RearmMinBars     int

	// 突破触发
	TriggerMode      TriggerMode
	TriggerBufferATR float64
	TriggerBufferPct float64

	// Level 分层阈值
	InternalProminenceATR float64
	SwingProminenceATR    float64
	MajorProminenceATR    float64

	HistoricalLookback     int
	HistoricalTolerancePct float64

	// 同一近期母盘整中仍有更高前高压制时，较低 Level 不触发。
	MotherRangeOuterEdgeOnly bool
	MotherRangeLookbackBars  int
	MotherRangeMaxGapATR     float64
	MotherRangeMaxGapPct     float64
	MotherRangeDominanceATR  float64
	MotherRangeDominancePct  float64

	// 是否输出 MICRO 级别事件。
	// 检测层仍保留 MICRO Level，只控制通知层。
	AlertMicro bool
}

func DefaultConfig() Config {
	return Config{
		ATRPeriod: 14,

		PivotLeft:  4,
		PivotRight: 3,

		MinProminenceATR: 0.70,

		IdentityATR:        0.35,
		IdentityPct:        0.0015, // 0.15%
		MaxIdentityZoneATR: 0.80,

		SpatialResetATR: 1.00,
		SpatialResetPct: 0.010, // 1%

		TemporalResetBars:  8,
		TemporalBelowRatio: 0.60,

		RearmATR:         1.00,
		RearmPct:         0.0050,
		RearmBelowCloses: 4,
		RearmMinBars:     4,

		TriggerMode:      TriggerHigh,
		TriggerBufferATR: 0.03,
		TriggerBufferPct: 0.0002,

		InternalProminenceATR: 1.00,
		SwingProminenceATR:    1.80,
		MajorProminenceATR:    3.00,

		HistoricalLookback:     300,
		HistoricalTolerancePct: 0.001,

		MotherRangeOuterEdgeOnly: true,
		MotherRangeLookbackBars:  0,
		MotherRangeMaxGapATR:     10.0,
		MotherRangeMaxGapPct:     0.12,
		MotherRangeDominanceATR:  0.20,
		MotherRangeDominancePct:  0.001,

		AlertMicro: false,
	}
}

type Candle struct {
	TimeRaw string  `json:"time"`
	Open    float64 `json:"open"`
	High    float64 `json:"high"`
	Low     float64 `json:"low"`
	Close   float64 `json:"close"`
	Volume  float64 `json:"volume,omitempty"`
}

type ResistanceLevel struct {
	ID       int        `json:"level_id"`
	NativeTF string     `json:"native_tf"`
	Class    LevelClass `json:"level_class"`

	// Anchor 一经建立默认冻结，避免 Level Creep。
	Anchor float64 `json:"anchor"`

	// Identity zone 只用来合并“附近的同一个前高”，不是用来追价。
	ZoneLow  float64 `json:"zone_low"`
	ZoneHigh float64 `json:"zone_high"`

	CreatedBar    int    `json:"created_bar"`
	ConfirmedBar  int    `json:"confirmed_bar"`
	CreatedTime   string `json:"created_time"`
	ConfirmedTime string `json:"confirmed_time"`

	ATRAtConfirm  float64 `json:"atr_at_confirm"`
	ProminenceATR float64 `json:"prominence_atr"`

	TouchCount    int `json:"touch_count"`
	BreakoutCount int `json:"breakout_count"`

	SpatialSeparated   bool `json:"spatial_separated"`
	TemporalSeparated  bool `json:"temporal_separated"`
	SeparationComplete bool `json:"separation_complete"`

	Armed     bool `json:"armed"`
	Triggered bool `json:"triggered"`

	MinLowSinceConfirm float64 `json:"min_low_since_confirm"`
	BarsSinceConfirm   int     `json:"bars_since_confirm"`
	BelowCloseCount    int     `json:"below_close_count"`
	BelowCloseStreak   int     `json:"below_close_streak"`

	Active bool `json:"active"`

	SourcePivots []int `json:"source_pivots"`

	LastAlertBar int `json:"last_alert_bar"`
}

func (l *ResistanceLevel) SeparationType() string {
	switch {
	case l.SpatialSeparated && l.TemporalSeparated:
		return "SPATIAL+TEMPORAL"
	case l.SpatialSeparated:
		return "SPATIAL"
	case l.TemporalSeparated:
		return "TEMPORAL"
	default:
		return "NONE"
	}
}

type BreakoutEvent struct {
	BarIndex int    `json:"bar_index"`
	Time     string `json:"time"`
	NativeTF string `json:"native_tf"`

	LevelID    int        `json:"level_id"`
	LevelClass LevelClass `json:"level_class"`

	Anchor   float64 `json:"anchor"`
	ZoneLow  float64 `json:"zone_low"`
	ZoneHigh float64 `json:"zone_high"`

	TriggerPrice  float64 `json:"trigger_price"`
	BreakoutCount int     `json:"breakout_count"`

	SeparationType    string `json:"separation_type"`
	SpatialSeparated  bool   `json:"spatial_separated"`
	TemporalSeparated bool   `json:"temporal_separated"`

	ProminenceATR float64 `json:"prominence_atr"`
	TouchCount    int     `json:"touch_count"`
}

type Detector struct {
	Cfg      Config
	NativeTF string

	Candles []Candle
	ATR     []float64

	Levels map[int]*ResistanceLevel
	Events []BreakoutEvent

	nextLevelID int
}

func NewDetector(cfg Config, nativeTF string) *Detector {
	return &Detector{
		Cfg:         cfg,
		NativeTF:    nativeTF,
		Levels:      make(map[int]*ResistanceLevel),
		nextLevelID: 1,
	}
}

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func min(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

func priceTol(price, atr, atrMult, pct float64) float64 {
	atrPart := 0.0
	if !math.IsNaN(atr) && !math.IsInf(atr, 0) {
		atrPart = atrMult * atr
	}
	pctPart := pct * abs(price)
	return max(max(atrPart, pctPart), 1e-12)
}

func trueRange(cur Candle, prevClose float64, hasPrev bool) float64 {
	tr := cur.High - cur.Low
	if hasPrev {
		tr = max(tr, abs(cur.High-prevClose))
		tr = max(tr, abs(cur.Low-prevClose))
	}
	return tr
}

func calcATR(c []Candle, period int) []float64 {
	out := make([]float64, len(c))
	if len(c) == 0 {
		return out
	}

	alpha := 1.0 / float64(period)
	var prevATR float64
	var trs []float64

	for i := range c {
		tr := trueRange(c[i], func() float64 {
			if i == 0 {
				return 0
			}
			return c[i-1].Close
		}(), i > 0)

		trs = append(trs, tr)

		if i == 0 {
			prevATR = tr
		} else {
			prevATR = alpha*tr + (1.0-alpha)*prevATR
		}

		// 前期 EWM 过于不稳时，用 TR 中位数兜底。
		if i+1 < period {
			tmp := append([]float64(nil), trs...)
			sort.Float64s(tmp)
			n := len(tmp)
			if n%2 == 1 {
				out[i] = tmp[n/2]
			} else {
				out[i] = (tmp[n/2-1] + tmp[n/2]) / 2.0
			}
		} else {
			out[i] = prevATR
		}
	}
	return out
}

func (d *Detector) isPivotHigh(p, confirm int) bool {
	L := d.Cfg.PivotLeft
	R := d.Cfg.PivotRight
	if p-L < 0 || p+R > confirm || p+R >= len(d.Candles) {
		return false
	}

	h := d.Candles[p].High
	for i := p - L; i < p; i++ {
		if h < d.Candles[i].High {
			return false
		}
	}
	for i := p + 1; i <= p+R; i++ {
		// 右侧用严格 >，避免整段等高全部变成 Pivot。
		if h <= d.Candles[i].High {
			return false
		}
	}
	return true
}

func (d *Detector) prominenceATR(p, confirm int) float64 {
	L := d.Cfg.PivotLeft
	R := d.Cfg.PivotRight

	leftStart := p - L
	if leftStart < 0 {
		leftStart = 0
	}
	rightEnd := p + R
	if rightEnd > confirm {
		rightEnd = confirm
	}

	leftLow := math.Inf(1)
	for i := leftStart; i <= p; i++ {
		leftLow = min(leftLow, d.Candles[i].Low)
	}

	rightLow := math.Inf(1)
	for i := p; i <= rightEnd; i++ {
		rightLow = min(rightLow, d.Candles[i].Low)
	}

	prom := min(d.Candles[p].High-leftLow, d.Candles[p].High-rightLow)
	atr := max(d.ATR[confirm], 1e-12)
	if prom < 0 {
		prom = 0
	}
	return prom / atr
}

func (d *Detector) classifyLevel(p int, promATR float64) LevelClass {
	cfg := d.Cfg
	px := d.Candles[p].High

	// Historical：只看 Pivot 之前已经存在的数据，不偷看未来。
	start := p - cfg.HistoricalLookback
	if start < 0 {
		start = 0
	}
	if p > start {
		prevMax := -math.MaxFloat64
		for i := start; i < p; i++ {
			prevMax = max(prevMax, d.Candles[i].High)
		}
		if prevMax > 0 && px >= prevMax*(1.0-cfg.HistoricalTolerancePct) {
			return LevelHistorical
		}
	}

	switch {
	case promATR >= cfg.MajorProminenceATR:
		return LevelMajor
	case promATR >= cfg.SwingProminenceATR:
		return LevelSwing
	case promATR >= cfg.InternalProminenceATR:
		return LevelInternal
	default:
		return LevelMicro
	}
}

func classRank(c LevelClass) int {
	switch c {
	case LevelHistorical:
		return 5
	case LevelMajor:
		return 4
	case LevelSwing:
		return 3
	case LevelInternal:
		return 2
	default:
		return 1
	}
}

func (d *Detector) findMergeTarget(price, atr float64) *ResistanceLevel {
	tol := priceTol(price, atr, d.Cfg.IdentityATR, d.Cfg.IdentityPct)

	var best *ResistanceLevel
	bestDist := math.Inf(1)

	for _, l := range d.Levels {
		if !l.Active {
			continue
		}

		// 用 anchor 而不是 zone_high 追价。
		dist := abs(price - l.Anchor)
		if dist <= tol && dist < bestDist {
			best = l
			bestDist = dist
		}
	}
	return best
}

func (d *Detector) addOrMergePivot(p, confirm int) {
	prom := d.prominenceATR(p, confirm)
	if prom < d.Cfg.MinProminenceATR {
		return
	}

	px := d.Candles[p].High
	atr := d.ATR[confirm]
	cls := d.classifyLevel(p, prom)

	if l := d.findMergeTarget(px, atr); l != nil {
		l.TouchCount++
		l.SourcePivots = append(l.SourcePivots, p)
		if prom > l.ProminenceATR {
			l.ProminenceATR = prom
		}
		if classRank(cls) > classRank(l.Class) {
			l.Class = cls
		}

		// Anchor 冻结；zone 只能有限扩张。
		maxExpand := d.Cfg.MaxIdentityZoneATR * max(atr, 1e-12)
		candidateLow := min(l.ZoneLow, px)
		candidateHigh := max(l.ZoneHigh, px)
		if candidateHigh-candidateLow <= maxExpand {
			l.ZoneLow = candidateLow
			l.ZoneHigh = candidateHigh
		}
		return
	}

	id := d.nextLevelID
	d.nextLevelID++

	d.Levels[id] = &ResistanceLevel{
		ID:                 id,
		NativeTF:           d.NativeTF,
		Class:              cls,
		Anchor:             px,
		ZoneLow:            px,
		ZoneHigh:           px,
		CreatedBar:         p,
		ConfirmedBar:       confirm,
		CreatedTime:        d.Candles[p].TimeRaw,
		ConfirmedTime:      d.Candles[confirm].TimeRaw,
		ATRAtConfirm:       atr,
		ProminenceATR:      prom,
		TouchCount:         1,
		BreakoutCount:      0,
		MinLowSinceConfirm: math.Inf(1),
		Active:             true,
		SourcePivots:       []int{p},
		LastAlertBar:       -1,
	}
}

func (d *Detector) updateSeparation(l *ResistanceLevel, i int) {
	if i <= l.ConfirmedBar {
		return
	}

	c := d.Candles[i]
	atr := d.ATR[i]
	reference := max(l.Anchor, l.ZoneHigh)

	l.BarsSinceConfirm++
	l.MinLowSinceConfirm = min(l.MinLowSinceConfirm, c.Low)

	if c.Close < reference {
		l.BelowCloseCount++
	}

	// ---------- 空间分离 ----------
	spatialGap := priceTol(reference, atr, d.Cfg.SpatialResetATR, d.Cfg.SpatialResetPct)
	if l.MinLowSinceConfirm <= reference-spatialGap {
		l.SpatialSeparated = true
	}

	// ---------- 时间分离 ----------
	if l.BarsSinceConfirm >= d.Cfg.TemporalResetBars {
		ratio := float64(l.BelowCloseCount) / float64(maxInt(l.BarsSinceConfirm, 1))
		if ratio >= d.Cfg.TemporalBelowRatio {
			l.TemporalSeparated = true
		}
	}

	l.SeparationComplete = l.SpatialSeparated || l.TemporalSeparated

	// 第一次只有完成分离以后才能 Armed。
	if l.SeparationComplete && !l.Triggered {
		l.Armed = true
	}
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func (d *Detector) updateRearm(l *ResistanceLevel, i int) {
	if !l.Triggered {
		return
	}

	c := d.Candles[i]
	atr := d.ATR[i]
	reference := max(l.Anchor, l.ZoneHigh)

	if c.Close < reference {
		l.BelowCloseStreak++
	} else {
		l.BelowCloseStreak = 0
	}

	gap := priceTol(reference, atr, d.Cfg.RearmATR, d.Cfg.RearmPct)
	deepEnough := c.Low <= reference-gap
	closesEnough := l.BelowCloseStreak >= d.Cfg.RearmBelowCloses
	resetMature := i-l.LastAlertBar >= d.Cfg.RearmMinBars

	if resetMature && (deepEnough || closesEnough) {
		l.Triggered = false
		l.Armed = true
	}
}

func (d *Detector) triggerThreshold(l *ResistanceLevel, i int) float64 {
	atr := d.ATR[i]
	reference := max(l.Anchor, l.ZoneHigh)
	buf := priceTol(reference, atr, d.Cfg.TriggerBufferATR, d.Cfg.TriggerBufferPct)
	return reference + buf
}

func (d *Detector) hasHigherMotherEdge(l *ResistanceLevel, i int) bool {
	if !d.Cfg.MotherRangeOuterEdgeOnly {
		return false
	}
	reference := max(l.Anchor, l.ZoneHigh)
	lookback := d.Cfg.MotherRangeLookbackBars
	if lookback <= 0 {
		lookback = defaultMotherRangeLookback(d.NativeTF)
	}
	start := i - lookback
	if start < 0 {
		start = 0
	}
	maxGap := priceTol(reference, d.ATR[i], d.Cfg.MotherRangeMaxGapATR, d.Cfg.MotherRangeMaxGapPct)
	dominance := priceTol(reference, d.ATR[i], d.Cfg.MotherRangeDominanceATR, d.Cfg.MotherRangeDominancePct)
	for _, other := range d.Levels {
		if other == l || !other.Active || other.ConfirmedBar >= i || other.CreatedBar < start {
			continue
		}
		otherEdge := max(other.Anchor, other.ZoneHigh)
		if otherEdge > reference+dominance && otherEdge-reference <= maxGap {
			return true
		}
	}
	return false
}

func defaultMotherRangeLookback(nativeTF string) int {
	switch strings.ToLower(strings.TrimSpace(nativeTF)) {
	case "1m":
		return 10080
	case "5m":
		return 2016
	case "15m":
		return 672
	case "30m":
		return 336
	case "1h", "60m":
		return 168
	case "4h", "240m":
		return 42
	case "1d", "1day":
		return 30
	default:
		return 168
	}
}

func (d *Detector) isFreshCross(l *ResistanceLevel, i int) (bool, float64) {
	if i <= 0 || !l.Active || !l.Armed || !l.SeparationComplete {
		return false, 0
	}

	threshold := d.triggerThreshold(l, i)
	prevClose := d.Candles[i-1].Close
	cur := d.Candles[i]

	switch d.Cfg.TriggerMode {
	case TriggerClose:
		if prevClose <= threshold && cur.Close > threshold {
			return true, cur.Close
		}
	default:
		// K线级别近似实时上穿：上一根收盘还在阈值下，本根 High 已穿越。
		// 接 WebSocket tick 时，可把这里换成 prevTick <= threshold && tick > threshold。
		if prevClose <= threshold && cur.High > threshold {
			return true, cur.High
		}
	}
	return false, 0
}

func (d *Detector) shouldNotify(l *ResistanceLevel) bool {
	if l.Class == LevelMicro && !d.Cfg.AlertMicro {
		return false
	}
	return true
}

func (d *Detector) processLevel(l *ResistanceLevel, i int) {
	if !l.Active || i <= l.ConfirmedBar {
		return
	}

	// 先更新 separation / re-arm，再判断本根是否形成新的 crossing。
	d.updateSeparation(l, i)
	d.updateRearm(l, i)
	if d.hasHigherMotherEdge(l, i) {
		return
	}

	crossed, price := d.isFreshCross(l, i)
	if !crossed {
		return
	}

	// 同一 Level 同一根K只记录一次。
	if l.LastAlertBar == i {
		return
	}

	l.BreakoutCount++
	l.Armed = false
	l.Triggered = true
	l.BelowCloseStreak = 0
	l.LastAlertBar = i

	if !d.shouldNotify(l) {
		return
	}

	d.Events = append(d.Events, BreakoutEvent{
		BarIndex:          i,
		Time:              d.Candles[i].TimeRaw,
		NativeTF:          d.NativeTF,
		LevelID:           l.ID,
		LevelClass:        l.Class,
		Anchor:            max(l.Anchor, l.ZoneHigh),
		ZoneLow:           l.ZoneLow,
		ZoneHigh:          l.ZoneHigh,
		TriggerPrice:      price,
		BreakoutCount:     l.BreakoutCount,
		SeparationType:    l.SeparationType(),
		SpatialSeparated:  l.SpatialSeparated,
		TemporalSeparated: l.TemporalSeparated,
		ProminenceATR:     l.ProminenceATR,
		TouchCount:        l.TouchCount,
	})
}

func (d *Detector) Run(candles []Candle) ([]BreakoutEvent, error) {
	if len(candles) < d.Cfg.PivotLeft+d.Cfg.PivotRight+5 {
		return nil, errors.New("K线数量太少")
	}

	d.Candles = candles
	d.ATR = calcATR(candles, d.Cfg.ATRPeriod)
	d.Events = nil
	d.Levels = make(map[int]*ResistanceLevel)
	d.nextLevelID = 1

	R := d.Cfg.PivotRight

	for i := range candles {
		// 1) 在当前 i 时刻，只确认 i-R 那根是否为 Pivot。
		//    因此不会用到 i 之后的未来数据。
		p := i - R
		if p >= 0 && d.isPivotHigh(p, i) {
			d.addOrMergePivot(p, i)
		}

		// 2) 更新所有已确认 Level。
		// 复制 ID 列表，避免未来扩展时迭代 map 带来行为不确定。
		ids := make([]int, 0, len(d.Levels))
		for id := range d.Levels {
			ids = append(ids, id)
		}
		sort.Ints(ids)
		for _, id := range ids {
			d.processLevel(d.Levels[id], i)
		}
	}

	// 同一根 K 可能同时穿越多个历史 Level；盘面和提醒只保留最高外沿。
	selected := make(map[int]BreakoutEvent)
	for _, event := range d.Events {
		current, exists := selected[event.BarIndex]
		if !exists || event.Anchor > current.Anchor {
			selected[event.BarIndex] = event
		}
	}
	bars := make([]int, 0, len(selected))
	for bar := range selected {
		bars = append(bars, bar)
	}
	sort.Ints(bars)
	deduplicated := make([]BreakoutEvent, 0, len(bars))
	for _, bar := range bars {
		deduplicated = append(deduplicated, selected[bar])
	}
	d.Events = deduplicated
	return d.Events, nil
}

// ============================================================
// CSV I/O
// ============================================================

func normalizeHeader(s string) string {
	return strings.ToLower(strings.TrimSpace(s))
}

func readCandlesCSV(path string) ([]Candle, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	r := csv.NewReader(bufio.NewReader(f))
	r.TrimLeadingSpace = true
	header, err := r.Read()
	if err != nil {
		return nil, err
	}

	pos := map[string]int{}
	for i, h := range header {
		pos[normalizeHeader(h)] = i
	}

	required := []string{"open", "high", "low", "close"}
	for _, k := range required {
		if _, ok := pos[k]; !ok {
			return nil, fmt.Errorf("CSV 缺少字段 %q", k)
		}
	}

	var out []Candle
	rowNum := 1
	for {
		rowNum++
		rec, err := r.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("第 %d 行: %w", rowNum, err)
		}

		get := func(k string) string {
			idx, ok := pos[k]
			if !ok || idx >= len(rec) {
				return ""
			}
			return strings.TrimSpace(rec[idx])
		}
		parse := func(k string) (float64, error) {
			s := get(k)
			v, err := strconv.ParseFloat(s, 64)
			if err != nil {
				return 0, fmt.Errorf("第 %d 行字段 %s=%q 无法解析", rowNum, k, s)
			}
			return v, nil
		}

		o, err := parse("open")
		if err != nil {
			return nil, err
		}
		h, err := parse("high")
		if err != nil {
			return nil, err
		}
		l, err := parse("low")
		if err != nil {
			return nil, err
		}
		c, err := parse("close")
		if err != nil {
			return nil, err
		}

		vol := 0.0
		if _, ok := pos["volume"]; ok && get("volume") != "" {
			vol, _ = strconv.ParseFloat(get("volume"), 64)
		}

		t := get("time")
		if t == "" {
			t = strconv.Itoa(len(out))
		} else {
			t = normalizeTimeString(t)
		}

		out = append(out, Candle{TimeRaw: t, Open: o, High: h, Low: l, Close: c, Volume: vol})
	}
	return out, nil
}

func normalizeTimeString(s string) string {
	s = strings.TrimSpace(s)
	if _, err := time.Parse(time.RFC3339, s); err == nil {
		return s
	}
	if n, err := strconv.ParseInt(s, 10, 64); err == nil {
		// 粗略判断毫秒 / 秒
		if n > 1_000_000_000_000 {
			return time.UnixMilli(n).UTC().Format(time.RFC3339)
		}
		if n > 1_000_000_000 {
			return time.Unix(n, 0).UTC().Format(time.RFC3339)
		}
	}
	return s
}

func writeEventsJSON(events []BreakoutEvent, path string) error {
	b, err := json.MarshalIndent(events, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0644)
}

func printSummary(events []BreakoutEvent) {
	fmt.Printf("\n共检测到 %d 个前高突破提醒\n", len(events))
	fmt.Println(strings.Repeat("-", 110))
	fmt.Printf("%-6s %-22s %-8s %-11s %-12s %-12s %-9s %-9s\n",
		"#", "time", "TF", "class", "anchor", "trigger", "break#", "separation")
	fmt.Println(strings.Repeat("-", 110))

	for i, e := range events {
		fmt.Printf("%-6d %-22s %-8s %-11s %-12.8g %-12.8g %-9d %-9s\n",
			i+1, e.Time, e.NativeTF, e.LevelClass, e.Anchor, e.TriggerPrice,
			e.BreakoutCount, e.SeparationType)
	}
}

func main() {
	csvPath := flag.String("csv", "", "OHLC CSV 文件路径")
	tf := flag.String("tf", "5m", "原生周期标签，例如 1m/5m/15m/1h/4h")
	out := flag.String("out", "breakout_events.json", "事件 JSON 输出文件")
	mode := flag.String("mode", "high", "触发方式: high 或 close")
	alertMicro := flag.Bool("micro", false, "是否也输出 MICRO 级别前高提醒")

	// 常用可调参数
	pivotLeft := flag.Int("pivot-left", 4, "Pivot 左侧确认长度")
	pivotRight := flag.Int("pivot-right", 3, "Pivot 右侧确认长度")
	temporalBars := flag.Int("time-reset-bars", 8, "时间分离最少K线数")
	flag.Parse()

	if *csvPath == "" {
		fmt.Println("用法: go run prior_high_monitor.go -csv candles.csv -tf 5m")
		flag.PrintDefaults()
		os.Exit(2)
	}

	cfg := DefaultConfig()
	cfg.PivotLeft = *pivotLeft
	cfg.PivotRight = *pivotRight
	cfg.TemporalResetBars = *temporalBars
	cfg.AlertMicro = *alertMicro
	if strings.EqualFold(*mode, "close") {
		cfg.TriggerMode = TriggerClose
	} else {
		cfg.TriggerMode = TriggerHigh
	}

	candles, err := readCandlesCSV(*csvPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "读取 CSV 失败:", err)
		os.Exit(1)
	}

	detector := NewDetector(cfg, *tf)
	events, err := detector.Run(candles)
	if err != nil {
		fmt.Fprintln(os.Stderr, "检测失败:", err)
		os.Exit(1)
	}

	printSummary(events)

	if err := writeEventsJSON(events, *out); err != nil {
		fmt.Fprintln(os.Stderr, "写入 JSON 失败:", err)
		os.Exit(1)
	}
	fmt.Printf("\n事件已写入: %s\n", *out)
}

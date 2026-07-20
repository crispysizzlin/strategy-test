// Research code. Validate in Quantower's simulator and on untouched data before any funded use.
// The strategy is intentionally single-symbol and single-account. Do not combine it with
// manual orders or another strategy on the same account/symbol.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using TradingPlatform.BusinessLayer;

namespace Quantower.AdaptiveOrb
{
    public sealed class AdaptiveOrbStrategy : Strategy, ICurrentAccount, ICurrentSymbol
    {
        private enum SetupState
        {
            BuildingRange,
            Armed,
            AwaitLongRetest,
            AwaitShortRetest,
            Disabled
        }

        private enum RegimeDirection
        {
            None = 0,
            LongOnly = 1,
            ShortOnly = -1,
            Both = 2
        }

        private sealed class MinuteBar
        {
            public DateTime StartEastern;
            public double Open;
            public double High;
            public double Low;
            public double Close;
            public double Volume;

            public MinuteBar(DateTime startEastern, double price, double size)
            {
                this.StartEastern = startEastern;
                this.Open = price;
                this.High = price;
                this.Low = price;
                this.Close = price;
                this.Volume = Math.Max(0D, size);
            }

            public void Update(double price, double size)
            {
                this.High = Math.Max(this.High, price);
                this.Low = Math.Min(this.Low, price);
                this.Close = price;
                this.Volume += Math.Max(0D, size);
            }
        }

        private sealed class OrderFlowSnapshot
        {
            public DateTime ReceivedUtc;
            public double BestBid;
            public double BestAsk;
            public double BestBidSize;
            public double BestAskSize;
            public double DepthImbalance;
            public double NormalizedOfi;
            public double MicropriceTicks;
            public double Composite;
            public double SignedPersistence;
            public double SpreadTicks;
        }

        [InputParameter("Symbol", 0)]
        public Symbol CurrentSymbol { get; set; }

        [InputParameter("Account", 1)]
        public Account CurrentAccount { get; set; }

        [InputParameter("Use Level 2 confirmation", 10)]
        public bool UseLevel2 { get; set; } = true;

        [InputParameter("Opening range minutes", 11, 5, 60, 5, 0)]
        public int OpeningRangeMinutes { get; set; } = 15;

        [InputParameter("Signal end hour ET", 12, 9, 15, 1, 0)]
        public int SignalEndHourEastern { get; set; } = 11;

        [InputParameter("Signal end minute ET", 13, 0, 59, 1, 0)]
        public int SignalEndMinuteEastern { get; set; } = 30;

        [InputParameter("Flatten hour ET", 14, 12, 16, 1, 0)]
        public int FlattenHourEastern { get; set; } = 15;

        [InputParameter("Flatten minute ET", 15, 0, 59, 1, 0)]
        public int FlattenMinuteEastern { get; set; } = 55;

        [InputParameter("ATR lookback sessions", 20, 5, 60, 1, 0)]
        public int AtrLookbackSessions { get; set; } = 20;

        [InputParameter("Daily ATR override (0 = platform history)", 25, 0.0, 100000.0, 0.25, 2)]
        public double DailyAtrOverride { get; set; } = 0.0;

        [InputParameter("Minimum OR / ATR", 21, 0.01, 1.0, 0.01, 2)]
        public double MinimumOpeningRangeAtr { get; set; } = 0.08;

        [InputParameter("Maximum OR / ATR", 22, 0.02, 2.0, 0.01, 2)]
        public double MaximumOpeningRangeAtr { get; set; } = 0.35;

        [InputParameter("Minimum opening-range ticks", 23, 1, 500, 1, 0)]
        public int MinimumOpeningRangeTicks { get; set; } = 8;

        [InputParameter("Maximum opening-range ticks", 24, 2, 1000, 1, 0)]
        public int MaximumOpeningRangeTicks { get; set; } = 120;

        [InputParameter("Breakout buffer fraction", 30, 0.0, 0.50, 0.01, 2)]
        public double BreakoutBufferFraction { get; set; } = 0.05;

        [InputParameter("Minimum breakout buffer ticks", 31, 1, 20, 1, 0)]
        public int MinimumBreakoutBufferTicks { get; set; } = 2;

        [InputParameter("Retest tolerance fraction", 32, 0.01, 0.50, 0.01, 2)]
        public double RetestToleranceFraction { get; set; } = 0.12;

        [InputParameter("Retest expiry minutes", 33, 1, 60, 1, 0)]
        public int RetestExpiryMinutes { get; set; } = 20;

        [InputParameter("Minimum breakout relative volume", 34, 0.50, 5.0, 0.05, 2)]
        public double MinimumBreakoutRelativeVolume { get; set; } = 1.15;

        [InputParameter("VWAP slope lookback bars", 35, 1, 20, 1, 0)]
        public int VwapSlopeLookbackBars { get; set; } = 3;

        [InputParameter("Minimum VWAP slope ticks", 36, 0.0, 10.0, 0.05, 2)]
        public double MinimumVwapSlopeTicks { get; set; } = 0.25;

        [InputParameter("Minimum L2 composite", 40, 0.0, 1.0, 0.01, 2)]
        public double MinimumL2Composite { get; set; } = 0.15;

        [InputParameter("Minimum L2 persistence", 41, 0.0, 1.0, 0.05, 2)]
        public double MinimumL2Persistence { get; set; } = 0.60;

        [InputParameter("Maximum spread ticks", 42, 1.0, 20.0, 0.5, 1)]
        public double MaximumSpreadTicks { get; set; } = 1.0;

        [InputParameter("L2 freshness milliseconds", 43, 100, 5000, 50, 0)]
        public int Level2FreshnessMilliseconds { get; set; } = 750;

        [InputParameter("Maximum risk per trade", 50, 1.0, 10000.0, 1.0, 2)]
        public double MaximumRiskPerTrade { get; set; } = 75.0;

        [InputParameter("Internal daily loss limit", 51, 1.0, 50000.0, 1.0, 2)]
        public double InternalDailyLossLimit { get; set; } = 225.0;

        [InputParameter("Maximum quantity", 52, 1, 100, 1, 0)]
        public int MaximumQuantity { get; set; } = 4;

        [InputParameter("Minimum stop ticks", 53, 1, 500, 1, 0)]
        public int MinimumStopTicks { get; set; } = 8;

        [InputParameter("Maximum stop ticks", 54, 2, 1000, 1, 0)]
        public int MaximumStopTicks { get; set; } = 48;

        [InputParameter("Structural stop fraction", 55, 0.01, 1.0, 0.01, 2)]
        public double StructuralStopFraction { get; set; } = 0.15;

        [InputParameter("Reward / risk", 56, 0.25, 10.0, 0.05, 2)]
        public double RewardRiskMultiple { get; set; } = 1.75;

        [InputParameter("Failure close fraction", 57, 0.01, 1.0, 0.01, 2)]
        public double FailureCloseFraction { get; set; } = 0.15;

        [InputParameter("Time stop minutes", 58, 1, 240, 1, 0)]
        public int TimeStopMinutes { get; set; } = 45;

        [InputParameter("Maximum trades per session", 59, 1, 3, 1, 0)]
        public int MaximumTradesPerSession { get; set; } = 1;

        [InputParameter("Tick value in account currency", 60, 0.01, 10000.0, 0.01, 2)]
        public double TickValue { get; set; } = 1.25;

        [InputParameter("Commission per contract per side", 61, 0.0, 100.0, 0.01, 2)]
        public double CommissionPerSide { get; set; } = 0.50;

        [InputParameter("Assumed slippage ticks per side", 62, 0.0, 20.0, 0.25, 2)]
        public double AssumedSlippageTicksPerSide { get; set; } = 1.0;

        [InputParameter("Enforce prop liquidation threshold", 70)]
        public bool EnforcePropThreshold { get; set; } = true;

        [InputParameter("Current prop liquidation threshold", 71, 0.0, 10000000.0, 1.0, 2)]
        public double CurrentPropLiquidationThreshold { get; set; } = 0.0;

        [InputParameter("Prop-threshold safety buffer", 72, 0.0, 10000.0, 1.0, 2)]
        public double PropThresholdSafetyBuffer { get; set; } = 100.0;

        [InputParameter("Require daily restart for prop threshold", 73)]
        public bool RequireDailyRestartForPropThreshold { get; set; } = true;

        [InputParameter("External regime CSV path", 80)]
        public string ExternalRegimeCsvPath { get; set; } = string.Empty;

        [InputParameter("Require fresh external regime", 81)]
        public bool RequireExternalRegime { get; set; } = false;

        [InputParameter("Record throttled L2 CSV", 90)]
        public bool RecordLevel2 { get; set; } = false;

        [InputParameter("L2 recording path", 91)]
        public string Level2RecordingPath { get; set; } = string.Empty;

        [InputParameter("Enable live wall-clock watchdog", 92)]
        public bool EnableWallClockWatchdog { get; set; } = true;

        [InputParameter("Flatten selected symbol when strategy stops", 93)]
        public bool FlattenOnStop { get; set; } = true;

        public override string[] MonitoringConnectionsIds
        {
            get
            {
                return new[] { this.CurrentSymbol?.ConnectionId, this.CurrentAccount?.ConnectionId };
            }
        }

        private readonly object sync = new object();
        private readonly Queue<double> orderFlowHistory = new Queue<double>();
        private readonly List<double> vwapHistory = new List<double>();

        private TimeZoneInfo easternTimeZone;
        private Timer watchdog;
        private StreamWriter level2Writer;
        private string marketOrderTypeId;
        private SetupState state;
        private RegimeDirection externalDirection = RegimeDirection.Both;
        private double externalRiskMultiplier = 1D;
        private DateTime currentSessionDateEastern = DateTime.MinValue;
        private DateTime openingRangeEndEastern;
        private DateTime signalEndEastern;
        private DateTime flattenEastern;
        private DateTime retestExpiryEastern;
        private DateTime lastTickWallClockUtc = DateTime.MinValue;
        private DateTime lastLevel2WallClockUtc = DateTime.MinValue;
        private DateTime lastLevel2CalculationWallClockUtc = DateTime.MinValue;
        private DateTime entryTimeEastern = DateTime.MinValue;
        private DateTime orderSentUtc = DateTime.MinValue;

        private MinuteBar currentMinuteBar;
        private OrderFlowSnapshot latestOrderFlow;
        private Position activePosition;
        private Side expectedEntrySide;

        private double openingRangeHigh;
        private double openingRangeLow;
        private double dailyAtr;
        private double sessionVwapNumerator;
        private double sessionVwapDenominator;
        private double volumeEma;
        private double previousTradePrice;
        private double tradeDeltaEma;
        private double tradeVolumeEma;
        private double previousBestBid;
        private double previousBestAsk;
        private double previousBestBidSize;
        private double previousBestAskSize;
        private double ofiEma;
        private double sessionStartingBalance;
        private double decisionPrice;
        private double expectedEntryQuantity;

        private int tradesThisSession;
        private int excessiveSlippageEvents;
        private bool rangeLocked;
        private bool waitingForEntryFill;
        private bool entryFillSeen;
        private bool flattenRequested;
        private bool sessionDisabled;
        private bool strategyStarted;

        public AdaptiveOrbStrategy()
        {
            this.Name = "Adaptive ORB + VWAP + persistent L2 retest";
            this.Description = "Research strategy: cost-aware opening-range retest with VWAP and Level 2 confirmation";
        }

        protected override void OnRun()
        {
            lock (this.sync)
            {
                if (!this.ResolveAndValidateInputs())
                {
                    this.Stop();
                    return;
                }

                if (Core.Instance.Positions.Any(this.IsSelectedAccountPosition)
                    || Core.Instance.Orders.Any(this.IsSelectedAccountOrder))
                {
                    this.Log("Refusing to start: the selected account already has exposure or working orders.", StrategyLoggingLevel.Error);
                    this.Stop();
                    return;
                }

                this.dailyAtr = this.DailyAtrOverride > 0D
                    ? this.DailyAtrOverride
                    : this.LoadMedianDailyTrueRange();
                if (!(this.dailyAtr > 0D))
                {
                    this.Log("Unable to calculate a positive daily ATR; strategy will not start.", StrategyLoggingLevel.Error);
                    this.Stop();
                    return;
                }

                this.OpenLevel2Writer();
                this.CurrentSymbol.NewLast += this.CurrentSymbolOnNewLast;
                this.CurrentSymbol.NewLevel2 += this.CurrentSymbolOnNewLevel2;
                this.CurrentAccount.Updated += this.CurrentAccountOnUpdated;
                Core.Instance.PositionAdded += this.CoreOnPositionAdded;
                Core.Instance.PositionRemoved += this.CoreOnPositionRemoved;
                Core.Instance.OrdersHistoryAdded += this.CoreOnOrdersHistoryAdded;
                Core.Instance.TradeAdded += this.CoreOnTradeAdded;
                this.watchdog = new Timer(this.WatchdogTick, null, 1000, 1000);
                this.strategyStarted = true;
                this.Log("Started. This build is research-only until the repository validation gate passes.");
            }
        }

        protected override void OnStop()
        {
            lock (this.sync)
            {
                if (this.strategyStarted && this.FlattenOnStop)
                    this.FlattenAndDisable("strategy stopped");
                this.strategyStarted = false;

                if (this.CurrentSymbol != null)
                {
                    this.CurrentSymbol.NewLast -= this.CurrentSymbolOnNewLast;
                    this.CurrentSymbol.NewLevel2 -= this.CurrentSymbolOnNewLevel2;
                }
                if (this.CurrentAccount != null)
                    this.CurrentAccount.Updated -= this.CurrentAccountOnUpdated;

                Core.Instance.PositionAdded -= this.CoreOnPositionAdded;
                Core.Instance.PositionRemoved -= this.CoreOnPositionRemoved;
                Core.Instance.OrdersHistoryAdded -= this.CoreOnOrdersHistoryAdded;
                Core.Instance.TradeAdded -= this.CoreOnTradeAdded;

                if (this.watchdog != null)
                {
                    this.watchdog.Dispose();
                    this.watchdog = null;
                }
                if (this.level2Writer != null)
                {
                    this.level2Writer.Flush();
                    this.level2Writer.Dispose();
                    this.level2Writer = null;
                }
            }
            base.OnStop();
        }

        private bool ResolveAndValidateInputs()
        {
            if (this.CurrentSymbol != null && this.CurrentSymbol.State == BusinessObjectState.Fake)
                this.CurrentSymbol = Core.Instance.GetSymbol(this.CurrentSymbol.CreateInfo());
            if (this.CurrentAccount != null && this.CurrentAccount.State == BusinessObjectState.Fake)
                this.CurrentAccount = Core.Instance.GetAccount(this.CurrentAccount.CreateInfo());

            if (this.CurrentSymbol == null || this.CurrentAccount == null)
            {
                this.Log("Symbol and account are required.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.CurrentSymbol.ConnectionId != this.CurrentAccount.ConnectionId)
            {
                this.Log("Symbol and account must use the same connection.", StrategyLoggingLevel.Error);
                return false;
            }
            if (!(this.CurrentSymbol.TickSize > 0D) || !(this.TickValue > 0D))
            {
                this.Log("Tick size and tick value must be positive.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.MinimumOpeningRangeAtr <= 0D || this.MaximumOpeningRangeAtr <= this.MinimumOpeningRangeAtr)
            {
                this.Log("Opening-range ATR bounds are invalid.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.MinimumStopTicks < 1 || this.MaximumStopTicks < this.MinimumStopTicks)
            {
                this.Log("Stop bounds are invalid.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.EnforcePropThreshold && !(this.CurrentPropLiquidationThreshold > 0D))
            {
                this.Log("Enter the current prop liquidation threshold or explicitly disable its enforcement.", StrategyLoggingLevel.Error);
                return false;
            }

            var marketType = Core.Instance.OrderTypes.FirstOrDefault(
                item => item.ConnectionId == this.CurrentSymbol.ConnectionId && item.Behavior == OrderTypeBehavior.Market);
            if (marketType == null)
            {
                this.Log("The selected connection does not expose a market order type.", StrategyLoggingLevel.Error);
                return false;
            }
            this.marketOrderTypeId = marketType.Id;

            try
            {
                this.easternTimeZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
            }
            catch (TimeZoneNotFoundException)
            {
                this.Log("Windows Eastern Time zone was not found.", StrategyLoggingLevel.Error);
                return false;
            }
            return true;
        }

        private double LoadMedianDailyTrueRange()
        {
            IHistoricalData history = null;
            try
            {
                DateTime now = Core.Instance.TimeUtils.DateTimeUtcNow;
                history = this.CurrentSymbol.GetHistory(
                    Period.DAY1,
                    this.CurrentSymbol.HistoryType,
                    now.AddDays(-Math.Max(90, this.AtrLookbackSessions * 4)),
                    now);
                var bars = new List<HistoryItemBar>();
                for (int index = 0; index < history.Count; index++)
                {
                    var bar = history[index] as HistoryItemBar;
                    if (bar != null)
                        bars.Add(bar);
                }
                bars = bars.OrderBy(item => item.TimeLeft).ToList();
                var trueRanges = new List<double>();
                double? previousClose = null;
                // The newest daily bar can be the still-forming session. Excluding one bar is
                // conservative and prevents look-ahead/partial-session volatility leakage.
                foreach (var bar in bars.Take(Math.Max(0, bars.Count - 1)))
                {
                    double high = bar.High;
                    double low = bar.Low;
                    double close = bar.Close;
                    if (high > low && previousClose.HasValue)
                    {
                        trueRanges.Add(Math.Max(high - low, Math.Max(Math.Abs(high - previousClose.Value), Math.Abs(low - previousClose.Value))));
                    }
                    previousClose = close;
                }
                if (trueRanges.Count < this.AtrLookbackSessions)
                    return 0D;
                var sample = trueRanges.Skip(Math.Max(0, trueRanges.Count - this.AtrLookbackSessions)).OrderBy(value => value).ToArray();
                int middle = sample.Length / 2;
                return sample.Length % 2 == 1 ? sample[middle] : 0.5D * (sample[middle - 1] + sample[middle]);
            }
            catch (Exception exception)
            {
                this.Log("ATR history error: " + exception.Message, StrategyLoggingLevel.Error);
                return 0D;
            }
            finally
            {
                if (history != null)
                    history.Dispose();
            }
        }

        private void CurrentSymbolOnNewLast(Symbol symbol, Last last)
        {
            lock (this.sync)
            {
                this.lastTickWallClockUtc = DateTime.UtcNow;
                DateTime eastern = this.ToEastern(this.GetMarketUtc());
                if (this.currentSessionDateEastern != eastern.Date)
                    this.ResetSession(eastern.Date);

                this.UpdateRiskState(eastern);
                if (eastern >= this.flattenEastern)
                {
                    this.FlattenAndDisable("scheduled session flatten");
                    return;
                }

                DateTime minute = new DateTime(eastern.Year, eastern.Month, eastern.Day, eastern.Hour, eastern.Minute, 0, DateTimeKind.Unspecified);
                if (this.currentMinuteBar == null)
                    this.currentMinuteBar = new MinuteBar(minute, last.Price, last.Size);
                else if (minute > this.currentMinuteBar.StartEastern)
                {
                    MinuteBar closedBar = this.currentMinuteBar;
                    this.currentMinuteBar = new MinuteBar(minute, last.Price, last.Size);
                    this.OnMinuteBarClosed(closedBar, last.Price, eastern);
                }
                else
                    this.currentMinuteBar.Update(last.Price, last.Size);

                if (this.IsRegularSession(eastern))
                {
                    double size = Math.Max(0D, last.Size);
                    this.sessionVwapNumerator += last.Price * size;
                    this.sessionVwapDenominator += size;
                    if (eastern < this.openingRangeEndEastern)
                    {
                        this.openingRangeHigh = Math.Max(this.openingRangeHigh, last.Price);
                        this.openingRangeLow = Math.Min(this.openingRangeLow, last.Price);
                    }
                }
                this.UpdateTradeDelta(last);
            }
        }

        private void OnMinuteBarClosed(MinuteBar bar, double nextBarPrice, DateTime marketTimeEastern)
        {
            if (bar.StartEastern.Date != this.currentSessionDateEastern)
                return;
            TimeSpan barTime = bar.StartEastern.TimeOfDay;
            if (barTime < new TimeSpan(9, 30, 0) || barTime >= new TimeSpan(16, 0, 0))
                return;

            double vwap = this.CurrentSessionVwap;
            this.vwapHistory.Add(vwap);
            double relativeVolume = this.volumeEma > 0D ? bar.Volume / this.volumeEma : 0D;

            if (this.activePosition != null)
                this.ManageOpenPosition(bar, vwap, marketTimeEastern);

            if (!this.rangeLocked && bar.StartEastern.AddMinutes(1) >= this.openingRangeEndEastern)
                this.LockOpeningRange();

            if (!this.sessionDisabled
                && this.activePosition == null
                && !this.waitingForEntryFill
                && this.rangeLocked
                && bar.StartEastern >= this.openingRangeEndEastern)
                this.EvaluateSetup(bar, nextBarPrice, relativeVolume, vwap, marketTimeEastern);

            this.volumeEma = this.volumeEma <= 0D ? bar.Volume : 0.12D * bar.Volume + 0.88D * this.volumeEma;
        }

        private void LockOpeningRange()
        {
            this.rangeLocked = true;
            double width = this.OpeningRangeWidth;
            double ticks = width / this.CurrentSymbol.TickSize;
            double ratio = width / this.dailyAtr;
            if (!(width > 0D)
                || ticks < this.MinimumOpeningRangeTicks
                || ticks > this.MaximumOpeningRangeTicks
                || ratio < this.MinimumOpeningRangeAtr
                || ratio > this.MaximumOpeningRangeAtr)
            {
                this.DisableForSession(string.Format(
                    CultureInfo.InvariantCulture,
                    "opening range rejected: width={0:F2}, ticks={1:F1}, OR/ATR={2:F3}", width, ticks, ratio));
                return;
            }
            this.state = SetupState.Armed;
            this.Log(string.Format(
                CultureInfo.InvariantCulture,
                "Opening range locked: low={0:F2}, high={1:F2}, OR/ATR={2:F3}", this.openingRangeLow, this.openingRangeHigh, ratio));
        }

        private void EvaluateSetup(MinuteBar bar, double nextBarPrice, double relativeVolume, double vwap, DateTime marketTimeEastern)
        {
            if (bar.StartEastern > this.signalEndEastern || this.tradesThisSession >= this.MaximumTradesPerSession)
            {
                this.DisableForSession("signal window or trade count exhausted");
                return;
            }
            double width = this.OpeningRangeWidth;
            double buffer = Math.Max(
                this.MinimumBreakoutBufferTicks * this.CurrentSymbol.TickSize,
                this.BreakoutBufferFraction * width);
            double tolerance = this.RetestToleranceFraction * width;

            if (this.state == SetupState.Armed)
            {
                if (relativeVolume < this.MinimumBreakoutRelativeVolume)
                    return;
                if (bar.Close >= this.openingRangeHigh + buffer
                    && this.DirectionAllowed(Side.Buy)
                    && this.TrendConfirmed(Side.Buy, bar.Close, vwap))
                {
                    this.state = SetupState.AwaitLongRetest;
                    this.retestExpiryEastern = bar.StartEastern.AddMinutes(this.RetestExpiryMinutes);
                    this.Log("Long breakout observed; waiting for a retest.");
                }
                else if (bar.Close <= this.openingRangeLow - buffer
                    && this.DirectionAllowed(Side.Sell)
                    && this.TrendConfirmed(Side.Sell, bar.Close, vwap))
                {
                    this.state = SetupState.AwaitShortRetest;
                    this.retestExpiryEastern = bar.StartEastern.AddMinutes(this.RetestExpiryMinutes);
                    this.Log("Short breakout observed; waiting for a retest.");
                }
                return;
            }

            if (bar.StartEastern > this.retestExpiryEastern)
            {
                this.state = SetupState.Armed;
                return;
            }

            if (this.state == SetupState.AwaitLongRetest)
            {
                if (bar.Close < this.openingRangeHigh - this.FailureCloseFraction * width)
                {
                    this.state = SetupState.Armed;
                    return;
                }
                bool retest = bar.Low <= this.openingRangeHigh + tolerance && bar.Close >= this.openingRangeHigh;
                if (retest && this.TrendConfirmed(Side.Buy, bar.Close, vwap) && this.OrderFlowConfirmed(Side.Buy))
                    this.TryEnter(Side.Buy, nextBarPrice, bar.Low, marketTimeEastern);
            }
            else if (this.state == SetupState.AwaitShortRetest)
            {
                if (bar.Close > this.openingRangeLow + this.FailureCloseFraction * width)
                {
                    this.state = SetupState.Armed;
                    return;
                }
                bool retest = bar.High >= this.openingRangeLow - tolerance && bar.Close <= this.openingRangeLow;
                if (retest && this.TrendConfirmed(Side.Sell, bar.Close, vwap) && this.OrderFlowConfirmed(Side.Sell))
                    this.TryEnter(Side.Sell, nextBarPrice, bar.High, marketTimeEastern);
            }
        }

        private bool TrendConfirmed(Side side, double close, double vwap)
        {
            if (this.vwapHistory.Count <= this.VwapSlopeLookbackBars)
                return false;
            double prior = this.vwapHistory[this.vwapHistory.Count - 1 - this.VwapSlopeLookbackBars];
            double slopeTicks = (vwap - prior) / this.CurrentSymbol.TickSize;
            if (side == Side.Sell)
                slopeTicks = -slopeTicks;
            bool aligned = side == Side.Buy ? close > vwap : close < vwap;
            return aligned && slopeTicks >= this.MinimumVwapSlopeTicks;
        }

        private bool OrderFlowConfirmed(Side side)
        {
            if (!this.UseLevel2)
                return true;
            if (this.latestOrderFlow == null || this.orderFlowHistory.Count < 5)
                return false;
            if ((DateTime.UtcNow - this.latestOrderFlow.ReceivedUtc).TotalMilliseconds > this.Level2FreshnessMilliseconds)
                return false;
            if (this.latestOrderFlow.SpreadTicks > this.MaximumSpreadTicks)
                return false;
            double sign = side == Side.Buy ? 1D : -1D;
            return sign * this.latestOrderFlow.Composite >= this.MinimumL2Composite
                && sign * this.latestOrderFlow.SignedPersistence >= this.MinimumL2Persistence;
        }

        private void TryEnter(Side side, double entryReference, double retestExtreme, DateTime marketTimeEastern)
        {
            if (!this.PreTradeRiskChecks())
                return;
            double tick = this.CurrentSymbol.TickSize;
            double structural;
            int rawStopTicks;
            if (side == Side.Buy)
            {
                structural = Math.Min(retestExtreme - tick, this.openingRangeHigh - this.StructuralStopFraction * this.OpeningRangeWidth);
                rawStopTicks = (int)Math.Ceiling((entryReference - structural) / tick);
            }
            else
            {
                structural = Math.Max(retestExtreme + tick, this.openingRangeLow + this.StructuralStopFraction * this.OpeningRangeWidth);
                rawStopTicks = (int)Math.Ceiling((structural - entryReference) / tick);
            }
            int stopTicks = Math.Max(this.MinimumStopTicks, rawStopTicks);
            if (stopTicks > this.MaximumStopTicks)
            {
                this.Log("Retest skipped because its structural stop exceeds the configured maximum.");
                this.state = SetupState.Armed;
                return;
            }

            double riskPerContract = stopTicks * this.TickValue
                + 2D * this.CommissionPerSide
                + 2D * this.AssumedSlippageTicksPerSide * this.TickValue;
            double availableRisk = this.MaximumRiskPerTrade * this.externalRiskMultiplier;
            double currentEquity = this.CurrentEquity;
            double dailyRiskRemaining = this.InternalDailyLossLimit + currentEquity - this.sessionStartingBalance;
            availableRisk = Math.Min(availableRisk, dailyRiskRemaining);
            if (this.EnforcePropThreshold)
            {
                double propRiskRemaining = currentEquity
                    - this.CurrentPropLiquidationThreshold
                    - this.PropThresholdSafetyBuffer;
                availableRisk = Math.Min(availableRisk, propRiskRemaining);
            }
            if (!(availableRisk >= riskPerContract))
            {
                this.Log("Retest skipped because one contract exceeds the all-in, daily, or prop-floor risk budget.");
                this.state = SetupState.Armed;
                return;
            }
            int quantity = Math.Min(this.MaximumQuantity, (int)Math.Floor(availableRisk / riskPerContract));
            if (quantity < 1)
            {
                this.Log("Retest skipped because one contract exceeds the all-in, daily, or prop-floor risk budget.");
                this.state = SetupState.Armed;
                return;
            }

            int targetTicks = (int)Math.Ceiling(stopTicks * this.RewardRiskMultiple);
            var request = new PlaceOrderRequestParameters
            {
                Account = this.CurrentAccount,
                Symbol = this.CurrentSymbol,
                Side = side,
                Quantity = quantity,
                TimeInForce = TimeInForce.Day,
                OrderTypeId = this.marketOrderTypeId,
                StopLoss = SlTpHolder.CreateSL(stopTicks * tick, PriceMeasurement.Offset),
                TakeProfit = SlTpHolder.CreateTP(targetTicks * tick, PriceMeasurement.Offset)
            };

            this.waitingForEntryFill = true;
            this.entryFillSeen = false;
            this.expectedEntrySide = side;
            this.expectedEntryQuantity = quantity;
            this.decisionPrice = entryReference;
            this.orderSentUtc = DateTime.UtcNow;
            var result = Core.Instance.PlaceOrder(request);
            if (result.Status == TradingOperationResultStatus.Failure)
            {
                this.waitingForEntryFill = false;
                this.DisableForSession("entry order rejected: " + result.Message);
                return;
            }
            this.tradesThisSession++;
            this.entryTimeEastern = marketTimeEastern;
            this.state = SetupState.Disabled;
            this.Log(string.Format(
                CultureInfo.InvariantCulture,
                "Entry sent: side={0}, qty={1}, stopTicks={2}, targetTicks={3}, allInRiskPerContract={4:F2}",
                side, quantity, stopTicks, targetTicks, riskPerContract), StrategyLoggingLevel.Trading);
        }

        private void ManageOpenPosition(MinuteBar bar, double vwap, DateTime marketTimeEastern)
        {
            if (this.activePosition == null || this.flattenRequested)
                return;
            double width = this.OpeningRangeWidth;
            bool failed;
            if (this.activePosition.Side == Side.Buy)
                failed = bar.Close < this.openingRangeHigh - this.FailureCloseFraction * width && bar.Close < vwap;
            else
                failed = bar.Close > this.openingRangeLow + this.FailureCloseFraction * width && bar.Close > vwap;
            if (failed)
            {
                this.FlattenAndDisable("failed opening-range retest");
                return;
            }
            if (this.entryTimeEastern != DateTime.MinValue
                && marketTimeEastern - this.entryTimeEastern >= TimeSpan.FromMinutes(this.TimeStopMinutes))
                this.FlattenAndDisable("time stop");
        }

        private void CurrentSymbolOnNewLevel2(Symbol symbol, Level2Quote update, DOMQuote snapshot)
        {
            lock (this.sync)
            {
                DateTime wallClock = DateTime.UtcNow;
                this.lastLevel2WallClockUtc = wallClock;
                if ((wallClock - this.lastLevel2CalculationWallClockUtc).TotalMilliseconds < 200D)
                    return;
                this.lastLevel2CalculationWallClockUtc = wallClock;

                try
                {
                    var depth = this.CurrentSymbol.DepthOfMarket.GetDepthOfMarketAggregatedCollections(
                        new GetDepthOfMarketParameters
                        {
                            GetLevel2ItemsParameters = new GetLevel2ItemsParameters
                            {
                                AggregateMethod = AggregateMethod.ByPriceLVL,
                                LevelsCount = 5,
                                CalculateCumulative = false,
                                CustomTickSize = this.CurrentSymbol.TickSize
                            }
                        });
                    if (depth.Bids == null || depth.Asks == null || depth.Bids.Length == 0 || depth.Asks.Length == 0)
                        return;

                    int levels = Math.Min(5, Math.Min(depth.Bids.Length, depth.Asks.Length));
                    double bidDepth = 0D;
                    double askDepth = 0D;
                    for (int index = 0; index < levels; index++)
                    {
                        double weight = Math.Exp(-0.60D * index);
                        bidDepth += weight * Math.Max(0D, depth.Bids[index].Size);
                        askDepth += weight * Math.Max(0D, depth.Asks[index].Size);
                    }
                    double denominator = bidDepth + askDepth;
                    if (!(denominator > 0D))
                        return;

                    double bestBid = depth.Bids[0].Price;
                    double bestAsk = depth.Asks[0].Price;
                    double bestBidSize = Math.Max(0D, depth.Bids[0].Size);
                    double bestAskSize = Math.Max(0D, depth.Asks[0].Size);
                    double depthImbalance = (bidDepth - askDepth) / denominator;
                    double ofi = this.CalculateNormalizedOfi(bestBid, bestAsk, bestBidSize, bestAskSize);
                    double microprice = bestBidSize + bestAskSize > 0D
                        ? (bestAsk * bestBidSize + bestBid * bestAskSize) / (bestBidSize + bestAskSize)
                        : 0.5D * (bestBid + bestAsk);
                    double mid = 0.5D * (bestBid + bestAsk);
                    double micropriceTicks = this.Clamp((microprice - mid) / this.CurrentSymbol.TickSize, -1D, 1D);
                    double tradeDelta = this.tradeVolumeEma > 0D ? this.Clamp(this.tradeDeltaEma / this.tradeVolumeEma, -1D, 1D) : 0D;
                    double composite = 0.40D * depthImbalance + 0.30D * ofi + 0.20D * tradeDelta + 0.10D * micropriceTicks;

                    this.orderFlowHistory.Enqueue(composite);
                    while (this.orderFlowHistory.Count > 5)
                        this.orderFlowHistory.Dequeue();
                    int positive = this.orderFlowHistory.Count(value => value > 0D);
                    int negative = this.orderFlowHistory.Count(value => value < 0D);
                    double persistence = positive >= negative
                        ? (double)positive / this.orderFlowHistory.Count
                        : -(double)negative / this.orderFlowHistory.Count;

                    this.latestOrderFlow = new OrderFlowSnapshot
                    {
                        ReceivedUtc = wallClock,
                        BestBid = bestBid,
                        BestAsk = bestAsk,
                        BestBidSize = bestBidSize,
                        BestAskSize = bestAskSize,
                        DepthImbalance = depthImbalance,
                        NormalizedOfi = ofi,
                        MicropriceTicks = micropriceTicks,
                        Composite = composite,
                        SignedPersistence = persistence,
                        SpreadTicks = (bestAsk - bestBid) / this.CurrentSymbol.TickSize
                    };
                    this.WriteLevel2Row(this.latestOrderFlow, tradeDelta);
                }
                catch (Exception exception)
                {
                    if (this.activePosition != null)
                        this.FlattenAndDisable("Level 2 processing error: " + exception.Message);
                    else
                        this.DisableForSession("Level 2 processing error: " + exception.Message);
                }
            }
        }

        private double CalculateNormalizedOfi(double bid, double ask, double bidSize, double askSize)
        {
            if (!(this.previousBestBid > 0D) || !(this.previousBestAsk > 0D))
            {
                this.previousBestBid = bid;
                this.previousBestAsk = ask;
                this.previousBestBidSize = bidSize;
                this.previousBestAskSize = askSize;
                return 0D;
            }
            double eventValue = 0D;
            if (bid >= this.previousBestBid)
                eventValue += bidSize;
            if (bid <= this.previousBestBid)
                eventValue -= this.previousBestBidSize;
            if (ask <= this.previousBestAsk)
                eventValue -= askSize;
            if (ask >= this.previousBestAsk)
                eventValue += this.previousBestAskSize;

            double scale = Math.Max(1D, bidSize + askSize + this.previousBestBidSize + this.previousBestAskSize);
            double normalized = Math.Tanh(2D * eventValue / scale);
            this.ofiEma = 0.20D * normalized + 0.80D * this.ofiEma;
            this.previousBestBid = bid;
            this.previousBestAsk = ask;
            this.previousBestBidSize = bidSize;
            this.previousBestAskSize = askSize;
            return this.Clamp(this.ofiEma, -1D, 1D);
        }

        private void UpdateTradeDelta(Last last)
        {
            double sign = 0D;
            if (this.CurrentSymbol.Ask > 0D && last.Price >= this.CurrentSymbol.Ask)
                sign = 1D;
            else if (this.CurrentSymbol.Bid > 0D && last.Price <= this.CurrentSymbol.Bid)
                sign = -1D;
            else if (this.previousTradePrice > 0D)
                sign = Math.Sign(last.Price - this.previousTradePrice);

            double size = Math.Max(0D, last.Size);
            this.tradeDeltaEma = 0.10D * sign * size + 0.90D * this.tradeDeltaEma;
            this.tradeVolumeEma = 0.10D * size + 0.90D * this.tradeVolumeEma;
            this.previousTradePrice = last.Price;
        }

        private void CoreOnPositionAdded(Position position)
        {
            lock (this.sync)
            {
                if (!this.IsSelectedAccountPosition(position))
                    return;
                if (!this.IsSelectedPosition(position))
                {
                    this.FlattenAndDisable("unexpected exposure on another symbol in the selected account");
                    return;
                }
                if (!this.waitingForEntryFill)
                {
                    this.activePosition = position;
                    this.FlattenAndDisable("unexpected position detected");
                    return;
                }
                this.activePosition = position;
                this.waitingForEntryFill = false;
                if (Math.Abs(position.Quantity - this.expectedEntryQuantity) > 0.000001D || position.Side != this.expectedEntrySide)
                    this.FlattenAndDisable("position quantity/side mismatch");
            }
        }

        private void CoreOnPositionRemoved(Position position)
        {
            lock (this.sync)
            {
                if (!this.IsSelectedPosition(position))
                    return;
                this.activePosition = null;
                this.waitingForEntryFill = false;
                this.CancelSelectedOrders();
                if (this.tradesThisSession >= this.MaximumTradesPerSession)
                    this.DisableForSession("maximum trades reached");
                else if (!this.sessionDisabled)
                    this.state = SetupState.Armed;
            }
        }

        private void CoreOnOrdersHistoryAdded(OrderHistory order)
        {
            lock (this.sync)
            {
                if (order.Symbol != this.CurrentSymbol || order.Account != this.CurrentAccount)
                    return;
                if (order.Status != OrderStatus.Refused)
                    return;
                if (this.activePosition != null)
                    this.FlattenAndDisable("an entry or protective order was refused");
                else
                    this.DisableForSession("order refused");
            }
        }

        private void CoreOnTradeAdded(Trade trade)
        {
            lock (this.sync)
            {
                if (trade.Symbol != this.CurrentSymbol || trade.Account != this.CurrentAccount)
                    return;
                if (!this.entryFillSeen && this.orderSentUtc != DateTime.MinValue && trade.DateTime >= this.orderSentUtc.AddSeconds(-5))
                {
                    double signedSlippage = this.expectedEntrySide == Side.Buy
                        ? trade.Price - this.decisionPrice
                        : this.decisionPrice - trade.Price;
                    double slippageTicks = signedSlippage / this.CurrentSymbol.TickSize;
                    this.entryFillSeen = true;
                    if (slippageTicks > Math.Max(2D, 2D * this.AssumedSlippageTicksPerSide))
                    {
                        this.excessiveSlippageEvents++;
                        this.Log(string.Format(CultureInfo.InvariantCulture, "Excessive entry slippage: {0:F2} ticks", slippageTicks), StrategyLoggingLevel.Error);
                        if (this.excessiveSlippageEvents >= 2)
                            this.FlattenAndDisable("repeated excessive slippage");
                    }
                }
            }
        }

        private void CurrentAccountOnUpdated(Account account)
        {
            lock (this.sync)
            {
                this.UpdateRiskState(this.ToEastern(this.GetMarketUtc()));
            }
        }

        private void UpdateRiskState(DateTime marketTimeEastern)
        {
            if (this.currentSessionDateEastern == DateTime.MinValue)
                return;
            if (this.HasUnexpectedAccountActivity())
            {
                this.FlattenAndDisable("unexpected position or working order in the selected account");
                return;
            }
            double equity = this.CurrentEquity;
            double dailyPnl = equity - this.sessionStartingBalance;
            if (dailyPnl <= -this.InternalDailyLossLimit)
            {
                this.FlattenAndDisable("internal daily loss limit reached");
                return;
            }
            if (this.EnforcePropThreshold
                && equity <= this.CurrentPropLiquidationThreshold + this.PropThresholdSafetyBuffer)
                this.FlattenAndDisable("prop liquidation threshold safety buffer reached");
        }

        private bool PreTradeRiskChecks()
        {
            if (this.sessionDisabled || this.flattenRequested || this.activePosition != null || this.waitingForEntryFill)
                return false;
            if (this.HasUnexpectedAccountActivity())
            {
                this.FlattenAndDisable("unexpected position or working order in the selected account");
                return false;
            }
            if (this.tradesThisSession >= this.MaximumTradesPerSession)
                return false;
            if (this.CurrentEquity - this.sessionStartingBalance <= -this.InternalDailyLossLimit)
                return false;
            if (this.EnforcePropThreshold
                && this.CurrentEquity <= this.CurrentPropLiquidationThreshold + this.PropThresholdSafetyBuffer)
                return false;
            if (this.RequireExternalRegime && this.externalDirection == RegimeDirection.None)
                return false;
            return true;
        }

        private void ResetSession(DateTime sessionDateEastern)
        {
            if (this.currentSessionDateEastern != DateTime.MinValue
                && this.EnforcePropThreshold
                && this.RequireDailyRestartForPropThreshold)
            {
                this.currentSessionDateEastern = sessionDateEastern;
                this.flattenEastern = sessionDateEastern.AddHours(this.FlattenHourEastern).AddMinutes(this.FlattenMinuteEastern);
                this.DisableForSession("new ET date requires a restart and refreshed prop liquidation threshold");
                return;
            }
            if (Core.Instance.Positions.Any(this.IsSelectedAccountPosition)
                || Core.Instance.Orders.Any(this.IsSelectedAccountOrder))
            {
                this.FlattenAndDisable("account exposure carried into a new session");
                return;
            }
            double refreshedAtr = this.currentSessionDateEastern == DateTime.MinValue && this.dailyAtr > 0D
                ? this.dailyAtr
                : (this.DailyAtrOverride > 0D ? this.DailyAtrOverride : this.LoadMedianDailyTrueRange());
            if (!(refreshedAtr > 0D))
            {
                this.DisableForSession("unable to refresh a positive daily ATR");
                return;
            }
            this.dailyAtr = refreshedAtr;
            this.currentSessionDateEastern = sessionDateEastern;
            this.openingRangeEndEastern = sessionDateEastern.AddHours(9).AddMinutes(30 + this.OpeningRangeMinutes);
            this.signalEndEastern = sessionDateEastern.AddHours(this.SignalEndHourEastern).AddMinutes(this.SignalEndMinuteEastern);
            this.flattenEastern = sessionDateEastern.AddHours(this.FlattenHourEastern).AddMinutes(this.FlattenMinuteEastern);
            this.openingRangeHigh = double.MinValue;
            this.openingRangeLow = double.MaxValue;
            this.sessionVwapNumerator = 0D;
            this.sessionVwapDenominator = 0D;
            this.volumeEma = 0D;
            this.tradeDeltaEma = 0D;
            this.tradeVolumeEma = 0D;
            this.previousTradePrice = 0D;
            this.previousBestBid = 0D;
            this.previousBestAsk = 0D;
            this.previousBestBidSize = 0D;
            this.previousBestAskSize = 0D;
            this.ofiEma = 0D;
            this.latestOrderFlow = null;
            this.orderFlowHistory.Clear();
            this.vwapHistory.Clear();
            this.currentMinuteBar = null;
            this.tradesThisSession = 0;
            this.excessiveSlippageEvents = 0;
            this.rangeLocked = false;
            this.waitingForEntryFill = false;
            this.entryFillSeen = false;
            this.flattenRequested = false;
            this.sessionDisabled = false;
            this.state = SetupState.BuildingRange;
            this.sessionStartingBalance = this.CurrentAccount.Balance;
            this.externalDirection = RegimeDirection.Both;
            this.externalRiskMultiplier = 1D;
            this.LoadExternalRegime(sessionDateEastern);
            this.Log("Session reset for " + sessionDateEastern.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        }

        private void LoadExternalRegime(DateTime sessionDateEastern)
        {
            if (string.IsNullOrWhiteSpace(this.ExternalRegimeCsvPath))
            {
                if (this.RequireExternalRegime)
                    this.DisableForSession("required external regime path is empty");
                return;
            }
            try
            {
                string expectedDate = sessionDateEastern.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
                string selected = File.ReadLines(this.ExternalRegimeCsvPath)
                    .Where(line => !string.IsNullOrWhiteSpace(line) && !line.StartsWith("session_date", StringComparison.OrdinalIgnoreCase))
                    .LastOrDefault(line => line.Split(',')[0].Trim() == expectedDate);
                if (selected == null)
                    throw new InvalidDataException("no row for the current session");
                string[] fields = selected.Split(',');
                if (fields.Length < 4)
                    throw new InvalidDataException("expected at least four CSV fields");
                string direction = fields[1].Trim().ToLowerInvariant();
                if (direction == "long")
                    this.externalDirection = RegimeDirection.LongOnly;
                else if (direction == "short")
                    this.externalDirection = RegimeDirection.ShortOnly;
                else if (direction == "both")
                    this.externalDirection = RegimeDirection.Both;
                else if (direction == "none")
                    this.externalDirection = RegimeDirection.None;
                else
                    throw new InvalidDataException("direction must be long, short, both, or none");
                this.externalRiskMultiplier = this.Clamp(
                    double.Parse(fields[2].Trim(), CultureInfo.InvariantCulture), 0D, 1D);
                DateTime expiry = DateTime.Parse(fields[3].Trim(), CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal);
                if (expiry <= DateTime.UtcNow)
                    throw new InvalidDataException("regime row has expired");
            }
            catch (Exception exception)
            {
                if (this.RequireExternalRegime)
                {
                    this.externalDirection = RegimeDirection.None;
                    this.externalRiskMultiplier = 0D;
                    this.DisableForSession("external regime unavailable: " + exception.Message);
                }
                else
                {
                    this.externalDirection = RegimeDirection.Both;
                    this.externalRiskMultiplier = 1D;
                    this.Log("Ignoring optional external regime: " + exception.Message, StrategyLoggingLevel.Error);
                }
            }
        }

        private bool DirectionAllowed(Side side)
        {
            if (this.externalDirection == RegimeDirection.None)
                return false;
            if (this.externalDirection == RegimeDirection.Both)
                return true;
            return side == Side.Buy
                ? this.externalDirection == RegimeDirection.LongOnly
                : this.externalDirection == RegimeDirection.ShortOnly;
        }

        private void WatchdogTick(object stateObject)
        {
            lock (this.sync)
            {
                if (!this.EnableWallClockWatchdog)
                    return;
                if (this.currentSessionDateEastern == DateTime.MinValue || this.sessionDisabled)
                    return;
                DateTime eastern = this.ToEastern(DateTime.UtcNow);
                if (!this.IsRegularSession(eastern) || eastern >= this.flattenEastern)
                    return;
                if (this.lastTickWallClockUtc != DateTime.MinValue && (DateTime.UtcNow - this.lastTickWallClockUtc).TotalSeconds > 5D)
                {
                    this.FlattenAndDisable("last-trade feed stale for more than five seconds");
                    return;
                }
                if (this.UseLevel2
                    && this.lastLevel2WallClockUtc != DateTime.MinValue
                    && (DateTime.UtcNow - this.lastLevel2WallClockUtc).TotalMilliseconds > Math.Max(2000, 2 * this.Level2FreshnessMilliseconds))
                    this.FlattenAndDisable("Level 2 feed stale");
            }
        }

        private void FlattenAndDisable(string reason)
        {
            if (this.flattenRequested)
                return;
            this.flattenRequested = true;
            this.sessionDisabled = true;
            this.state = SetupState.Disabled;
            this.Log("Risk shutdown: " + reason, StrategyLoggingLevel.Error);
            foreach (var position in Core.Instance.Positions.Where(this.IsSelectedPosition).ToArray())
            {
                var result = position.Close();
                if (result.Status == TradingOperationResultStatus.Failure)
                    this.Log("Emergency close failed: " + result.Message, StrategyLoggingLevel.Error);
            }
            this.CancelSelectedOrders();
        }

        private void CancelSelectedOrders()
        {
            foreach (var order in Core.Instance.Orders.Where(this.IsSelectedOrder).ToArray())
            {
                var result = order.Cancel();
                if (result.Status == TradingOperationResultStatus.Failure)
                    this.Log("Order cancellation failed: " + result.Message, StrategyLoggingLevel.Error);
            }
        }

        private void DisableForSession(string reason)
        {
            this.sessionDisabled = true;
            this.state = SetupState.Disabled;
            this.Log("Session disabled: " + reason, StrategyLoggingLevel.Error);
        }

        private bool IsSelectedPosition(Position position)
        {
            return position != null && position.Symbol == this.CurrentSymbol && position.Account == this.CurrentAccount;
        }

        private bool IsSelectedAccountPosition(Position position)
        {
            return position != null && position.Account == this.CurrentAccount;
        }

        private bool IsSelectedOrder(Order order)
        {
            return order != null && order.Symbol == this.CurrentSymbol && order.Account == this.CurrentAccount;
        }

        private bool IsSelectedAccountOrder(Order order)
        {
            return order != null && order.Account == this.CurrentAccount;
        }

        private bool HasUnexpectedAccountActivity()
        {
            return Core.Instance.Positions.Any(
                    position => this.IsSelectedAccountPosition(position) && !this.IsSelectedPosition(position))
                || Core.Instance.Orders.Any(
                    order => this.IsSelectedAccountOrder(order) && !this.IsSelectedOrder(order));
        }

        private double CurrentEquity
        {
            get
            {
                double openPnl = Core.Instance.Positions
                    .Where(this.IsSelectedAccountPosition)
                    .Sum(position => position.NetPnL == null ? 0D : position.NetPnL.Value);
                return this.CurrentAccount.Balance + openPnl;
            }
        }

        private double CurrentSessionVwap
        {
            get
            {
                return this.sessionVwapDenominator > 0D
                    ? this.sessionVwapNumerator / this.sessionVwapDenominator
                    : this.CurrentSymbol.Last;
            }
        }

        private double OpeningRangeWidth
        {
            get { return this.openingRangeHigh - this.openingRangeLow; }
        }

        private bool IsRegularSession(DateTime eastern)
        {
            TimeSpan value = eastern.TimeOfDay;
            return value >= new TimeSpan(9, 30, 0) && value < new TimeSpan(16, 0, 0);
        }

        private DateTime GetMarketUtc()
        {
            DateTime value = this.CurrentSymbol.LastDateTime;
            if (value == default(DateTime))
                value = Core.Instance.TimeUtils.DateTimeUtcNow;
            if (value.Kind == DateTimeKind.Unspecified)
                return DateTime.SpecifyKind(value, DateTimeKind.Utc);
            return value.ToUniversalTime();
        }

        private DateTime ToEastern(DateTime utc)
        {
            if (utc.Kind == DateTimeKind.Unspecified)
                utc = DateTime.SpecifyKind(utc, DateTimeKind.Utc);
            return TimeZoneInfo.ConvertTimeFromUtc(utc.ToUniversalTime(), this.easternTimeZone);
        }

        private double Clamp(double value, double minimum, double maximum)
        {
            return Math.Max(minimum, Math.Min(maximum, value));
        }

        private void OpenLevel2Writer()
        {
            if (!this.RecordLevel2 || string.IsNullOrWhiteSpace(this.Level2RecordingPath))
                return;
            try
            {
                bool writeHeader = !File.Exists(this.Level2RecordingPath) || new FileInfo(this.Level2RecordingPath).Length == 0;
                this.level2Writer = new StreamWriter(this.Level2RecordingPath, true);
                this.level2Writer.AutoFlush = true;
                if (writeHeader)
                    this.level2Writer.WriteLine("received_utc,best_bid,best_ask,best_bid_size,best_ask_size,depth_imbalance,ofi_norm,trade_delta_norm,microprice_ticks,composite,signed_persistence,spread_ticks");
            }
            catch (Exception exception)
            {
                this.level2Writer = null;
                this.Log("L2 recording disabled: " + exception.Message, StrategyLoggingLevel.Error);
            }
        }

        private void WriteLevel2Row(OrderFlowSnapshot snapshot, double tradeDelta)
        {
            if (this.level2Writer == null)
                return;
            try
            {
                this.level2Writer.WriteLine(string.Join(",", new[]
                {
                    snapshot.ReceivedUtc.ToString("O", CultureInfo.InvariantCulture),
                    snapshot.BestBid.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.BestAsk.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.BestBidSize.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.BestAskSize.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.DepthImbalance.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.NormalizedOfi.ToString("R", CultureInfo.InvariantCulture),
                    tradeDelta.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.MicropriceTicks.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.Composite.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.SignedPersistence.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.SpreadTicks.ToString("R", CultureInfo.InvariantCulture)
                }));
            }
            catch (Exception exception)
            {
                this.Log("L2 recording stopped: " + exception.Message, StrategyLoggingLevel.Error);
                this.level2Writer.Dispose();
                this.level2Writer = null;
            }
        }
    }
}

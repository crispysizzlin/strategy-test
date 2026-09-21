// Research code. Validate in Quantower's simulator and on untouched data before any funded use.
// The strategy is intentionally single-symbol and single-account. Do not combine it with
// manual orders or another strategy on the same account/symbol.
//
// Default profile: MNQ on a Lucid Pro 100k evaluation. Trades up to three intraday windows
// per CME trading day (Globex reopen 18:00 ET, London 03:00 ET, New York 09:30 ET), each with
// its own opening range. Defaults use price/volume signals with no Level 2 subscription.
// Level 2 confirmation and wall execution are optional, separate research modes. Round-number levels
// (…00/20/40/50/60/80) only adjust exits; they never create signals.

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

        private sealed class TradingWindow
        {
            public string Name;
            public DateTime OpenEastern;
            public DateTime RangeEndEastern;
            public DateTime SignalEndEastern;
            public DateTime FlattenEastern;
            public double RiskFraction;
            public double MinOrAtr;
            public double MaxOrAtr;
            public int MinOrTicks;
            public int MaxOrTicks;
            public double MaxSpreadTicks;
            public double SlippageTicksPerSide;
            public double MinRelativeVolume;

            public bool Contains(DateTime eastern)
            {
                return eastern >= this.OpenEastern && eastern < this.FlattenEastern;
            }
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
            public double? BidWallPrice;
            public double? BidWallRatio;
            public double? AskWallPrice;
            public double? AskWallRatio;
        }

        private static readonly double[] RoundLevelOffsets = { 0D, 20D, 40D, 50D, 60D, 80D };
        private static readonly TimeSpan TradeDateRollover = new TimeSpan(17, 0, 0);
        private static readonly TimeSpan HardFlattenGuardEastern = new TimeSpan(16, 40, 0);

        [InputParameter("Symbol", 0)]
        public Symbol CurrentSymbol { get; set; }

        [InputParameter("Account", 1)]
        public Account CurrentAccount { get; set; }

        [InputParameter("Use Level 2 confirmation", 10)]
        public bool UseLevel2 { get; set; } = false;

        [InputParameter("Opening range minutes", 11, 5, 60, 5, 0)]
        public int OpeningRangeMinutes { get; set; } = 15;

        [InputParameter("Enable New York session (09:30 ET)", 12)]
        public bool EnableNewYorkSession { get; set; } = true;

        [InputParameter("NY signal end hour ET", 13, 9, 15, 1, 0)]
        public int SignalEndHourEastern { get; set; } = 11;

        [InputParameter("NY signal end minute ET", 14, 0, 59, 1, 0)]
        public int SignalEndMinuteEastern { get; set; } = 30;

        [InputParameter("NY flatten hour ET", 15, 12, 16, 1, 0)]
        public int FlattenHourEastern { get; set; } = 15;

        [InputParameter("NY flatten minute ET", 16, 0, 59, 1, 0)]
        public int FlattenMinuteEastern { get; set; } = 55;

        [InputParameter("Enable Globex reopen session (18:00 ET)", 17)]
        public bool EnableGlobexReopenSession { get; set; } = true;

        [InputParameter("Globex signal end hour ET", 18, 18, 23, 1, 0)]
        public int GlobexSignalEndHourEastern { get; set; } = 20;

        [InputParameter("Globex flatten hour ET", 19, 19, 23, 1, 0)]
        public int GlobexFlattenHourEastern { get; set; } = 21;

        [InputParameter("Globex flatten minute ET", 110, 0, 59, 1, 0)]
        public int GlobexFlattenMinuteEastern { get; set; } = 30;

        [InputParameter("Enable London session", 111)]
        public bool EnableLondonSession { get; set; } = true;

        [InputParameter("London open hour ET", 112, 0, 8, 1, 0)]
        public int LondonOpenHourEastern { get; set; } = 3;

        [InputParameter("London signal end hour ET", 113, 1, 9, 1, 0)]
        public int LondonSignalEndHourEastern { get; set; } = 5;

        [InputParameter("London flatten hour ET", 114, 2, 9, 1, 0)]
        public int LondonFlattenHourEastern { get; set; } = 8;

        [InputParameter("London flatten minute ET", 115, 0, 59, 1, 0)]
        public int LondonFlattenMinuteEastern { get; set; } = 30;

        [InputParameter("GTH risk fraction", 116, 0.05, 1.0, 0.05, 2)]
        public double GthRiskFraction { get; set; } = 0.50;

        [InputParameter("GTH minimum OR / ATR", 117, 0.005, 1.0, 0.005, 3)]
        public double GthMinimumOpeningRangeAtr { get; set; } = 0.02;

        [InputParameter("GTH maximum OR / ATR", 118, 0.01, 2.0, 0.005, 3)]
        public double GthMaximumOpeningRangeAtr { get; set; } = 0.15;

        [InputParameter("GTH minimum opening-range ticks", 119, 1, 500, 1, 0)]
        public int GthMinimumOpeningRangeTicks { get; set; } = 16;

        [InputParameter("GTH maximum opening-range ticks", 120, 2, 1000, 1, 0)]
        public int GthMaximumOpeningRangeTicks { get; set; } = 240;

        [InputParameter("GTH maximum spread ticks", 121, 1.0, 20.0, 0.5, 1)]
        public double GthMaximumSpreadTicks { get; set; } = 3.0;

        [InputParameter("GTH assumed slippage ticks per side", 122, 0.0, 20.0, 0.25, 2)]
        public double GthAssumedSlippageTicksPerSide { get; set; } = 2.0;

        [InputParameter("GTH minimum breakout relative volume", 123, 0.50, 5.0, 0.05, 2)]
        public double GthMinimumBreakoutRelativeVolume { get; set; } = 1.25;

        [InputParameter("ATR lookback sessions", 20, 5, 60, 1, 0)]
        public int AtrLookbackSessions { get; set; } = 20;

        [InputParameter("Daily ATR override (0 = platform history)", 25, 0.0, 100000.0, 0.25, 2)]
        public double DailyAtrOverride { get; set; } = 0.0;

        [InputParameter("Minimum OR / ATR", 21, 0.01, 1.0, 0.01, 2)]
        public double MinimumOpeningRangeAtr { get; set; } = 0.08;

        [InputParameter("Maximum OR / ATR", 22, 0.02, 2.0, 0.01, 2)]
        public double MaximumOpeningRangeAtr { get; set; } = 0.35;

        [InputParameter("Minimum opening-range ticks", 23, 1, 500, 1, 0)]
        public int MinimumOpeningRangeTicks { get; set; } = 40;

        [InputParameter("Maximum opening-range ticks", 24, 2, 1000, 1, 0)]
        public int MaximumOpeningRangeTicks { get; set; } = 600;

        [InputParameter("Breakout buffer fraction", 30, 0.0, 0.50, 0.01, 2)]
        public double BreakoutBufferFraction { get; set; } = 0.05;

        [InputParameter("Minimum breakout buffer ticks", 31, 1, 20, 1, 0)]
        public int MinimumBreakoutBufferTicks { get; set; } = 4;

        [InputParameter("Retest tolerance fraction", 32, 0.01, 0.50, 0.01, 2)]
        public double RetestToleranceFraction { get; set; } = 0.12;

        [InputParameter("Retest expiry minutes", 33, 1, 60, 1, 0)]
        public int RetestExpiryMinutes { get; set; } = 20;

        [InputParameter("Minimum breakout relative volume", 34, 0.50, 5.0, 0.05, 2)]
        public double MinimumBreakoutRelativeVolume { get; set; } = 1.15;

        [InputParameter("VWAP slope lookback bars", 35, 1, 20, 1, 0)]
        public int VwapSlopeLookbackBars { get; set; } = 3;

        [InputParameter("Minimum VWAP slope ticks", 36, 0.0, 20.0, 0.05, 2)]
        public double MinimumVwapSlopeTicks { get; set; } = 2.0;

        [InputParameter("Minimum L2 composite", 40, 0.0, 1.0, 0.01, 2)]
        public double MinimumL2Composite { get; set; } = 0.15;

        [InputParameter("Minimum L2 persistence", 41, 0.0, 1.0, 0.05, 2)]
        public double MinimumL2Persistence { get; set; } = 0.60;

        [InputParameter("Maximum spread ticks", 42, 1.0, 20.0, 0.5, 1)]
        public double MaximumSpreadTicks { get; set; } = 2.0;

        [InputParameter("L2 freshness milliseconds", 43, 100, 5000, 50, 0)]
        public int Level2FreshnessMilliseconds { get; set; } = 750;

        [InputParameter("Use wall-offset limit entries", 44)]
        public bool UseWallEntries { get; set; } = false;

        [InputParameter("Maximum L1 quote age milliseconds", 138, 100, 10000, 100, 0)]
        public int MaximumQuoteAgeMilliseconds { get; set; } = 2000;

        [InputParameter("Wall detection depth levels", 45, 5, 30, 1, 0)]
        public int WallDepthLevels { get; set; } = 15;

        [InputParameter("Minimum wall ratio vs median depth", 46, 1.0, 50.0, 0.5, 1)]
        public double MinimumWallRatio { get; set; } = 4.0;

        [InputParameter("Wall persistence snapshots", 47, 1, 100, 1, 0)]
        public int WallPersistenceSnapshots { get; set; } = 10;

        [InputParameter("Wall offset ticks", 48, 1, 40, 1, 0)]
        public int WallOffsetTicks { get; set; } = 6;

        [InputParameter("Wall stop pad ticks", 49, 0, 40, 1, 0)]
        public int WallStopPadTicks { get; set; } = 4;

        [InputParameter("Maximum wall chase ticks", 130, 1, 200, 1, 0)]
        public int MaximumWallChaseTicks { get; set; } = 24;

        [InputParameter("Wall entry timeout minutes", 131, 1, 30, 1, 0)]
        public int WallEntryTimeoutMinutes { get; set; } = 5;

        [InputParameter("Fall back to market entry on wall timeout", 132)]
        public bool WallTimeoutFallbackToMarket { get; set; } = true;

        [InputParameter("Use round-number exit adjustment", 133)]
        public bool UseRoundNumberExits { get; set; } = true;

        [InputParameter("Round-number front ticks", 134, 0, 40, 1, 0)]
        public int RoundNumberFrontTicks { get; set; } = 4;

        [InputParameter("Round-number target window ticks", 135, 0, 60, 1, 0)]
        public int RoundNumberTargetWindowTicks { get; set; } = 8;

        [InputParameter("Round-number stop trigger ticks", 136, 0, 40, 1, 0)]
        public int RoundNumberStopTriggerTicks { get; set; } = 4;

        [InputParameter("Round-number stop pad ticks", 137, 0, 60, 1, 0)]
        public int RoundNumberStopPadTicks { get; set; } = 6;

        [InputParameter("Maximum risk per trade", 50, 1.0, 10000.0, 1.0, 2)]
        public double MaximumRiskPerTrade { get; set; } = 150.0;

        [InputParameter("Internal daily loss limit", 51, 1.0, 50000.0, 1.0, 2)]
        public double InternalDailyLossLimit { get; set; } = 450.0;

        [InputParameter("Maximum quantity", 52, 1, 100, 1, 0)]
        public int MaximumQuantity { get; set; } = 10;

        [InputParameter("Minimum stop ticks", 53, 1, 500, 1, 0)]
        public int MinimumStopTicks { get; set; } = 16;

        [InputParameter("Maximum stop ticks", 54, 2, 1000, 1, 0)]
        public int MaximumStopTicks { get; set; } = 240;

        [InputParameter("Structural stop fraction", 55, 0.01, 1.0, 0.01, 2)]
        public double StructuralStopFraction { get; set; } = 0.15;

        [InputParameter("Reward / risk", 56, 0.25, 10.0, 0.05, 2)]
        public double RewardRiskMultiple { get; set; } = 1.75;

        [InputParameter("Failure close fraction", 57, 0.01, 1.0, 0.01, 2)]
        public double FailureCloseFraction { get; set; } = 0.15;

        [InputParameter("Time stop minutes", 58, 1, 240, 1, 0)]
        public int TimeStopMinutes { get; set; } = 45;

        [InputParameter("Maximum trades per window", 59, 1, 3, 1, 0)]
        public int MaximumTradesPerWindow { get; set; } = 1;

        [InputParameter("Tick value in account currency", 60, 0.01, 10000.0, 0.01, 2)]
        public double TickValue { get; set; } = 0.50;

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
        private readonly List<TradingWindow> tradingWindows = new List<TradingWindow>();

        private TimeZoneInfo easternTimeZone;
        private Timer watchdog;
        private StreamWriter level2Writer;
        private string marketOrderTypeId;
        private string limitOrderTypeId;
        private SetupState state;
        private RegimeDirection externalDirection = RegimeDirection.Both;
        private double externalRiskMultiplier = 1D;
        private DateTime currentTradingDateEastern = DateTime.MinValue;
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
        private TradingWindow activeWindow;

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

        private double lastBidWallPrice;
        private int bidWallStreak;
        private double lastAskWallPrice;
        private int askWallStreak;

        private bool wallEntryPending;
        private bool wallEntryCancelling;
        private bool wallFallbackArmed;
        private DateTime wallEntryDeadlineEastern = DateTime.MinValue;
        private DateTime wallFallbackDeadlineEastern = DateTime.MinValue;
        private double wallEntryRetestExtreme;
        private string wallEntryOrderId;

        private int tradesThisWindow;
        private int excessiveSlippageEvents;
        private bool rangeLocked;
        private bool waitingForEntryFill;
        private bool entryFillSeen;
        private bool flattenRequested;
        private bool tradingDayDisabled;
        private bool strategyStarted;

        public AdaptiveOrbStrategy()
        {
            this.Name = "Adaptive ORB multi-session (price/volume default)";
            this.Description = "Research strategy: Globex/London/NY ORB retests with VWAP, volume, L1 spread checks, and round-number exits; optional L2";
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

                if (this.UseLevel2)
                {
                    this.OpenLevel2Writer();
                    this.CurrentSymbol.NewLevel2 += this.CurrentSymbolOnNewLevel2;
                }
                this.CurrentSymbol.NewLast += this.CurrentSymbolOnNewLast;
                this.CurrentSymbol.NewQuote += this.CurrentSymbolOnNewQuote;
                this.CurrentAccount.Updated += this.CurrentAccountOnUpdated;
                Core.Instance.PositionAdded += this.CoreOnPositionAdded;
                Core.Instance.PositionRemoved += this.CoreOnPositionRemoved;
                Core.Instance.OrdersHistoryAdded += this.CoreOnOrdersHistoryAdded;
                Core.Instance.TradeAdded += this.CoreOnTradeAdded;
                this.watchdog = new Timer(this.WatchdogTick, null, 1000, 1000);
                this.strategyStarted = true;
                this.Log(this.UseLevel2
                    ? "Signal mode: optional L2 confirmation."
                    : "Signal mode: price/volume; no depth subscription, wall entries, or L2 recording. L1 execution checks remain active.");
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
                    this.CurrentSymbol.NewQuote -= this.CurrentSymbolOnNewQuote;
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
            if (!this.EnableNewYorkSession && !this.EnableGlobexReopenSession && !this.EnableLondonSession)
            {
                this.Log("At least one trading session must be enabled.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.MaximumQuoteAgeMilliseconds <= 0 || this.MaximumSpreadTicks <= 0D || this.GthMaximumSpreadTicks <= 0D)
            {
                this.Log("L1 freshness and spread limits must be positive.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.MinimumOpeningRangeAtr <= 0D || this.MaximumOpeningRangeAtr <= this.MinimumOpeningRangeAtr)
            {
                this.Log("Opening-range ATR bounds are invalid.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.GthMinimumOpeningRangeAtr <= 0D || this.GthMaximumOpeningRangeAtr <= this.GthMinimumOpeningRangeAtr)
            {
                this.Log("GTH opening-range ATR bounds are invalid.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.MinimumStopTicks < 1 || this.MaximumStopTicks < this.MinimumStopTicks)
            {
                this.Log("Stop bounds are invalid.", StrategyLoggingLevel.Error);
                return false;
            }
            if (this.UseLevel2 && this.UseWallEntries && this.MaximumWallChaseTicks < this.WallOffsetTicks)
            {
                this.Log("Maximum wall chase ticks must be at least the wall offset.", StrategyLoggingLevel.Error);
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

            var limitType = Core.Instance.OrderTypes.FirstOrDefault(
                item => item.ConnectionId == this.CurrentSymbol.ConnectionId && item.Behavior == OrderTypeBehavior.Limit);
            if (limitType == null)
            {
                if (this.UseLevel2 && this.UseWallEntries)
                {
                    this.Log("The selected connection does not expose a limit order type; wall entries are unavailable.", StrategyLoggingLevel.Error);
                    return false;
                }
            }
            else
            {
                this.limitOrderTypeId = limitType.Id;
            }

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
                // Provider DAY1 futures bars cover the full Globex session, matching the
                // research engine's full-trading-day true range. The newest daily bar can be
                // the still-forming session; excluding one bar prevents look-ahead leakage.
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

        // ------------------------------------------------------------------
        // Trading-date and window scheduling
        // ------------------------------------------------------------------

        private DateTime TradeDateOf(DateTime eastern)
        {
            return eastern.TimeOfDay >= TradeDateRollover ? eastern.Date.AddDays(1) : eastern.Date;
        }

        private void BuildTradingWindows(DateTime tradeDateEastern)
        {
            this.tradingWindows.Clear();
            if (this.EnableGlobexReopenSession)
            {
                // The 18:00 ET reopen occurs on the calendar date before its trading date.
                DateTime calendarDate = tradeDateEastern.AddDays(-1);
                this.tradingWindows.Add(this.MakeWindow(
                    "globex_reopen",
                    calendarDate.AddHours(18),
                    calendarDate.AddHours(this.GlobexSignalEndHourEastern),
                    calendarDate.AddHours(this.GlobexFlattenHourEastern).AddMinutes(this.GlobexFlattenMinuteEastern),
                    isGth: true));
            }
            if (this.EnableLondonSession)
            {
                this.tradingWindows.Add(this.MakeWindow(
                    "london_open",
                    tradeDateEastern.AddHours(this.LondonOpenHourEastern),
                    tradeDateEastern.AddHours(this.LondonSignalEndHourEastern),
                    tradeDateEastern.AddHours(this.LondonFlattenHourEastern).AddMinutes(this.LondonFlattenMinuteEastern),
                    isGth: true));
            }
            if (this.EnableNewYorkSession)
            {
                this.tradingWindows.Add(this.MakeWindow(
                    "ny_open",
                    tradeDateEastern.AddHours(9).AddMinutes(30),
                    tradeDateEastern.AddHours(this.SignalEndHourEastern).AddMinutes(this.SignalEndMinuteEastern),
                    tradeDateEastern.AddHours(this.FlattenHourEastern).AddMinutes(this.FlattenMinuteEastern),
                    isGth: false));
            }
            this.tradingWindows.Sort((first, second) => first.OpenEastern.CompareTo(second.OpenEastern));
            for (int index = 1; index < this.tradingWindows.Count; index++)
            {
                if (this.tradingWindows[index].OpenEastern <= this.tradingWindows[index - 1].FlattenEastern)
                {
                    this.DisableForTradingDay("configured session windows overlap");
                    return;
                }
            }
        }

        private TradingWindow MakeWindow(string name, DateTime open, DateTime signalEnd, DateTime flatten, bool isGth)
        {
            return new TradingWindow
            {
                Name = name,
                OpenEastern = open,
                RangeEndEastern = open.AddMinutes(this.OpeningRangeMinutes),
                SignalEndEastern = signalEnd,
                FlattenEastern = flatten,
                RiskFraction = isGth ? this.GthRiskFraction : 1D,
                MinOrAtr = isGth ? this.GthMinimumOpeningRangeAtr : this.MinimumOpeningRangeAtr,
                MaxOrAtr = isGth ? this.GthMaximumOpeningRangeAtr : this.MaximumOpeningRangeAtr,
                MinOrTicks = isGth ? this.GthMinimumOpeningRangeTicks : this.MinimumOpeningRangeTicks,
                MaxOrTicks = isGth ? this.GthMaximumOpeningRangeTicks : this.MaximumOpeningRangeTicks,
                MaxSpreadTicks = isGth ? this.GthMaximumSpreadTicks : this.MaximumSpreadTicks,
                SlippageTicksPerSide = isGth ? this.GthAssumedSlippageTicksPerSide : this.AssumedSlippageTicksPerSide,
                MinRelativeVolume = isGth ? this.GthMinimumBreakoutRelativeVolume : this.MinimumBreakoutRelativeVolume
            };
        }

        private void HandleWindowTransitions(DateTime eastern)
        {
            if (this.activeWindow != null && eastern >= this.activeWindow.FlattenEastern)
            {
                if (this.activePosition != null || this.waitingForEntryFill)
                    this.FlattenWindowPosition("window flatten: " + this.activeWindow.Name);
                this.activeWindow = null;
                this.state = SetupState.Disabled;
            }
            if (this.activeWindow == null && !this.tradingDayDisabled)
            {
                foreach (var window in this.tradingWindows)
                {
                    if (window.Contains(eastern))
                    {
                        this.StartWindow(window, eastern);
                        break;
                    }
                }
            }
        }

        private void StartWindow(TradingWindow window, DateTime eastern)
        {
            this.activeWindow = window;
            this.openingRangeHigh = double.MinValue;
            this.openingRangeLow = double.MaxValue;
            this.sessionVwapNumerator = 0D;
            this.sessionVwapDenominator = 0D;
            this.volumeEma = 0D;
            this.vwapHistory.Clear();
            this.tradesThisWindow = 0;
            this.rangeLocked = false;
            this.retestExpiryEastern = DateTime.MinValue;
            this.ClearWallEntryState();
            // Joining after the opening range began means the recorded range would be
            // incomplete; skip this window rather than trade a partial range.
            if (eastern > window.OpenEastern.AddMinutes(1))
            {
                this.state = SetupState.Disabled;
                this.Log("Window " + window.Name + " skipped: strategy joined after the opening range began.");
                return;
            }
            this.state = SetupState.BuildingRange;
            this.Log("Window " + window.Name + " started: OR "
                + window.OpenEastern.ToString("HH:mm", CultureInfo.InvariantCulture) + "-"
                + window.RangeEndEastern.ToString("HH:mm", CultureInfo.InvariantCulture)
                + ", flatten " + window.FlattenEastern.ToString("HH:mm", CultureInfo.InvariantCulture));
        }

        // ------------------------------------------------------------------
        // Market data
        // ------------------------------------------------------------------

        private void CurrentSymbolOnNewQuote(Symbol symbol, Quote quote)
        {
            // Subscribe to ordinary best bid/ask updates without requesting depth.
            // Symbol caches the quote and its timestamp; validate it at order time.
        }

        private void CurrentSymbolOnNewLast(Symbol symbol, Last last)
        {
            lock (this.sync)
            {
                this.lastTickWallClockUtc = DateTime.UtcNow;
                DateTime eastern = this.ToEastern(this.GetMarketUtc());
                DateTime tradeDate = this.TradeDateOf(eastern);
                if (this.currentTradingDateEastern != tradeDate)
                    this.ResetTradingDay(tradeDate);

                this.UpdateRiskState(eastern);
                if (eastern.TimeOfDay >= HardFlattenGuardEastern && eastern.TimeOfDay < TradeDateRollover
                    && (this.activePosition != null || this.waitingForEntryFill))
                {
                    this.FlattenAndDisable("hard flatten guard before the prop close-out deadline");
                    return;
                }
                this.HandleWindowTransitions(eastern);

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

                if (this.activeWindow != null && this.activeWindow.Contains(eastern))
                {
                    double size = Math.Max(0D, last.Size);
                    this.sessionVwapNumerator += last.Price * size;
                    this.sessionVwapDenominator += size;
                    if (eastern < this.activeWindow.RangeEndEastern)
                    {
                        this.openingRangeHigh = Math.Max(this.openingRangeHigh, last.Price);
                        this.openingRangeLow = Math.Min(this.openingRangeLow, last.Price);
                    }
                }
                if (this.UseLevel2)
                    this.UpdateTradeDelta(last);
            }
        }

        private void OnMinuteBarClosed(MinuteBar bar, double nextBarPrice, DateTime marketTimeEastern)
        {
            TradingWindow window = this.activeWindow;
            if (window == null)
                return;
            if (bar.StartEastern < window.OpenEastern || bar.StartEastern >= window.FlattenEastern)
                return;

            double vwap = this.CurrentSessionVwap;
            this.vwapHistory.Add(vwap);
            double relativeVolume = this.volumeEma > 0D ? bar.Volume / this.volumeEma : 0D;

            if (this.activePosition != null)
                this.ManageOpenPosition(bar, vwap, marketTimeEastern);

            this.ReconcileWallEntryCancel(marketTimeEastern);
            if (this.wallEntryPending)
                this.ManagePendingWallEntry(bar, marketTimeEastern);

            if (!this.rangeLocked && bar.StartEastern.AddMinutes(1) >= window.RangeEndEastern)
                this.LockOpeningRange(window);

            if (!this.tradingDayDisabled
                && this.state != SetupState.Disabled
                && this.activePosition == null
                && !this.waitingForEntryFill
                && this.rangeLocked
                && bar.StartEastern >= window.RangeEndEastern)
                this.EvaluateSetup(window, bar, nextBarPrice, relativeVolume, vwap, marketTimeEastern);

            this.volumeEma = this.volumeEma <= 0D ? bar.Volume : 0.12D * bar.Volume + 0.88D * this.volumeEma;
        }

        private void LockOpeningRange(TradingWindow window)
        {
            this.rangeLocked = true;
            double width = this.OpeningRangeWidth;
            double ticks = width / this.CurrentSymbol.TickSize;
            double ratio = width / this.dailyAtr;
            if (!(width > 0D)
                || ticks < window.MinOrTicks
                || ticks > window.MaxOrTicks
                || ratio < window.MinOrAtr
                || ratio > window.MaxOrAtr)
            {
                this.DisableWindow(string.Format(
                    CultureInfo.InvariantCulture,
                    "{0} opening range rejected: width={1:F2}, ticks={2:F1}, OR/ATR={3:F3}", window.Name, width, ticks, ratio));
                return;
            }
            this.state = SetupState.Armed;
            this.Log(string.Format(
                CultureInfo.InvariantCulture,
                "{0} opening range locked: low={1:F2}, high={2:F2}, OR/ATR={3:F3}", window.Name, this.openingRangeLow, this.openingRangeHigh, ratio));
        }

        private void EvaluateSetup(TradingWindow window, MinuteBar bar, double nextBarPrice, double relativeVolume, double vwap, DateTime marketTimeEastern)
        {
            if (bar.StartEastern > window.SignalEndEastern || this.tradesThisWindow >= this.MaximumTradesPerWindow)
            {
                this.DisableWindow(window.Name + ": signal window or trade count exhausted");
                return;
            }
            double width = this.OpeningRangeWidth;
            double buffer = Math.Max(
                this.MinimumBreakoutBufferTicks * this.CurrentSymbol.TickSize,
                this.BreakoutBufferFraction * width);
            double tolerance = this.RetestToleranceFraction * width;

            if (this.state == SetupState.Armed)
            {
                if (relativeVolume < window.MinRelativeVolume)
                    return;
                if (bar.Close >= this.openingRangeHigh + buffer
                    && this.DirectionAllowed(Side.Buy)
                    && this.TrendConfirmed(Side.Buy, bar.Close, vwap))
                {
                    this.state = SetupState.AwaitLongRetest;
                    this.retestExpiryEastern = bar.StartEastern.AddMinutes(this.RetestExpiryMinutes);
                    this.Log(window.Name + ": long breakout observed; waiting for a retest.");
                }
                else if (bar.Close <= this.openingRangeLow - buffer
                    && this.DirectionAllowed(Side.Sell)
                    && this.TrendConfirmed(Side.Sell, bar.Close, vwap))
                {
                    this.state = SetupState.AwaitShortRetest;
                    this.retestExpiryEastern = bar.StartEastern.AddMinutes(this.RetestExpiryMinutes);
                    this.Log(window.Name + ": short breakout observed; waiting for a retest.");
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
                if (retest && this.TrendConfirmed(Side.Buy, bar.Close, vwap) && this.OrderFlowConfirmed(Side.Buy, window))
                    this.TryEnter(window, Side.Buy, nextBarPrice, bar.Low, marketTimeEastern);
            }
            else if (this.state == SetupState.AwaitShortRetest)
            {
                if (bar.Close > this.openingRangeLow + this.FailureCloseFraction * width)
                {
                    this.state = SetupState.Armed;
                    return;
                }
                bool retest = bar.High >= this.openingRangeLow - tolerance && bar.Close <= this.openingRangeLow;
                if (retest && this.TrendConfirmed(Side.Sell, bar.Close, vwap) && this.OrderFlowConfirmed(Side.Sell, window))
                    this.TryEnter(window, Side.Sell, nextBarPrice, bar.High, marketTimeEastern);
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

        private bool OrderFlowConfirmed(Side side, TradingWindow window)
        {
            if (!this.UseLevel2)
                return true;
            if (this.latestOrderFlow == null || this.orderFlowHistory.Count < 5)
                return false;
            if ((DateTime.UtcNow - this.latestOrderFlow.ReceivedUtc).TotalMilliseconds > this.Level2FreshnessMilliseconds)
                return false;
            if (this.latestOrderFlow.SpreadTicks > window.MaxSpreadTicks)
                return false;
            double sign = side == Side.Buy ? 1D : -1D;
            return sign * this.latestOrderFlow.Composite >= this.MinimumL2Composite
                && sign * this.latestOrderFlow.SignedPersistence >= this.MinimumL2Persistence;
        }

        // ------------------------------------------------------------------
        // Round-number ("even") levels: …00 / 20 / 40 / 50 / 60 / 80
        // ------------------------------------------------------------------

        private double NearestRoundLevel(double price)
        {
            double century = Math.Floor(price / 100D) * 100D;
            double best = double.NaN;
            double bestDistance = double.MaxValue;
            for (double baseLevel = century - 100D; baseLevel <= century + 100D; baseLevel += 100D)
            {
                foreach (double offset in RoundLevelOffsets)
                {
                    double level = baseLevel + offset;
                    double distance = Math.Abs(level - price);
                    if (distance < bestDistance || (distance == bestDistance && level < best))
                    {
                        bestDistance = distance;
                        best = level;
                    }
                }
            }
            return best;
        }

        private double ShaveTargetForRoundLevel(double entry, double target, double direction)
        {
            if (!this.UseRoundNumberExits)
                return target;
            double tick = this.CurrentSymbol.TickSize;
            double level = this.NearestRoundLevel(target);
            if (Math.Abs(target - level) > this.RoundNumberTargetWindowTicks * tick)
                return target;
            double shaved = level - direction * this.RoundNumberFrontTicks * tick;
            shaved = direction > 0D ? Math.Min(target, shaved) : Math.Max(target, shaved);
            if (direction * (shaved - entry) < tick)
                return target;
            return shaved;
        }

        private double PadStopForRoundLevel(double entry, double stop, double direction)
        {
            if (!this.UseRoundNumberExits)
                return stop;
            double tick = this.CurrentSymbol.TickSize;
            double level = this.NearestRoundLevel(stop);
            if (Math.Abs(stop - level) > this.RoundNumberStopTriggerTicks * tick)
                return stop;
            double padded = level - direction * this.RoundNumberStopPadTicks * tick;
            if (direction * (padded - stop) >= 0D)
                return stop;
            if (direction * (entry - padded) / tick > this.MaximumStopTicks)
                return stop;
            return padded;
        }

        // ------------------------------------------------------------------
        // Entries
        // ------------------------------------------------------------------

        private void TryEnter(TradingWindow window, Side side, double entryReference, double retestExtreme, DateTime marketTimeEastern)
        {
            if (!this.PreTradeRiskChecks() || !this.Level1ExecutionAllowed(window))
                return;
            if (this.TryPlaceWallLimitEntry(window, side, entryReference, retestExtreme, marketTimeEastern))
                return;
            this.SendMarketEntry(window, side, entryReference, retestExtreme, marketTimeEastern);
        }

        private bool TryPlaceWallLimitEntry(TradingWindow window, Side side, double entryReference, double retestExtreme, DateTime marketTimeEastern)
        {
            if (!this.UseLevel2 || !this.UseWallEntries || this.limitOrderTypeId == null)
                return false;
            OrderFlowSnapshot snapshot = this.latestOrderFlow;
            if (snapshot == null
                || (DateTime.UtcNow - snapshot.ReceivedUtc).TotalMilliseconds > this.Level2FreshnessMilliseconds)
                return false;
            double? wallPrice = side == Side.Buy ? snapshot.BidWallPrice : snapshot.AskWallPrice;
            double? wallRatio = side == Side.Buy ? snapshot.BidWallRatio : snapshot.AskWallRatio;
            if (!wallPrice.HasValue || !wallRatio.HasValue || wallRatio.Value < this.MinimumWallRatio)
                return false;

            double tick = this.CurrentSymbol.TickSize;
            double direction = side == Side.Buy ? 1D : -1D;
            double limitPrice = wallPrice.Value + direction * this.WallOffsetTicks * tick;
            double chaseTicks = direction * (entryReference - limitPrice) / tick;
            if (chaseTicks < 1D || chaseTicks > this.MaximumWallChaseTicks)
                return false;

            // Stop anchors behind the wall: if the wall breaks, the reason for the fill is gone.
            double stopReference = wallPrice.Value - direction * this.WallStopPadTicks * tick;
            int stopTicks = Math.Max(this.MinimumStopTicks, (int)Math.Ceiling(direction * (limitPrice - stopReference) / tick));
            double stopPrice = limitPrice - direction * stopTicks * tick;
            stopPrice = this.PadStopForRoundLevel(limitPrice, stopPrice, direction);
            stopTicks = Math.Max(stopTicks, (int)Math.Ceiling(direction * (limitPrice - stopPrice) / tick));
            if (stopTicks > this.MaximumStopTicks)
                return false;

            int quantity = this.SizeForStop(window, stopTicks);
            if (quantity < 1)
                return false;

            int targetTicks = (int)Math.Ceiling(stopTicks * this.RewardRiskMultiple);
            double targetPrice = limitPrice + direction * targetTicks * tick;
            targetPrice = this.ShaveTargetForRoundLevel(limitPrice, targetPrice, direction);
            double targetOffset = Math.Abs(targetPrice - limitPrice);

            var request = new PlaceOrderRequestParameters
            {
                Account = this.CurrentAccount,
                Symbol = this.CurrentSymbol,
                Side = side,
                Quantity = quantity,
                Price = limitPrice,
                TimeInForce = TimeInForce.Day,
                OrderTypeId = this.limitOrderTypeId,
                StopLoss = SlTpHolder.CreateSL(stopTicks * tick, PriceMeasurement.Offset),
                TakeProfit = SlTpHolder.CreateTP(targetOffset, PriceMeasurement.Offset)
            };

            this.waitingForEntryFill = true;
            this.entryFillSeen = false;
            this.expectedEntrySide = side;
            this.expectedEntryQuantity = quantity;
            this.decisionPrice = limitPrice;
            this.orderSentUtc = DateTime.UtcNow;
            var result = Core.Instance.PlaceOrder(request);
            if (result.Status == TradingOperationResultStatus.Failure)
            {
                this.waitingForEntryFill = false;
                this.DisableForTradingDay("wall limit entry rejected: " + result.Message);
                return true;
            }
            this.wallEntryPending = true;
            this.wallEntryDeadlineEastern = marketTimeEastern.AddMinutes(this.WallEntryTimeoutMinutes);
            this.wallEntryRetestExtreme = retestExtreme;
            this.wallEntryOrderId = result.OrderId;
            this.tradesThisWindow++;
            this.entryTimeEastern = marketTimeEastern;
            this.state = SetupState.Disabled;
            this.Log(string.Format(
                CultureInfo.InvariantCulture,
                "{0}: wall limit entry queued: side={1}, qty={2}, wall={3:F2} (ratio {4:F1}), limit={5:F2}, stopTicks={6}, targetTicks={7}",
                window.Name, side, quantity, wallPrice.Value, wallRatio.Value, limitPrice, stopTicks, targetTicks), StrategyLoggingLevel.Trading);
            return true;
        }

        private void SendMarketEntry(TradingWindow window, Side side, double entryReference, double retestExtreme, DateTime marketTimeEastern)
        {
            double tick = this.CurrentSymbol.TickSize;
            double direction = side == Side.Buy ? 1D : -1D;
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
            double stopPrice = entryReference - direction * stopTicks * tick;
            stopPrice = this.PadStopForRoundLevel(entryReference, stopPrice, direction);
            stopTicks = Math.Max(stopTicks, (int)Math.Ceiling(direction * (entryReference - stopPrice) / tick));
            if (stopTicks > this.MaximumStopTicks)
            {
                this.Log(window.Name + ": retest skipped because its structural stop exceeds the configured maximum.");
                this.state = SetupState.Armed;
                return;
            }

            int quantity = this.SizeForStop(window, stopTicks);
            if (quantity < 1)
            {
                this.Log(window.Name + ": retest skipped because one contract exceeds the all-in, daily, or prop-floor risk budget.");
                this.state = SetupState.Armed;
                return;
            }

            int targetTicks = (int)Math.Ceiling(stopTicks * this.RewardRiskMultiple);
            double targetPrice = entryReference + direction * targetTicks * tick;
            targetPrice = this.ShaveTargetForRoundLevel(entryReference, targetPrice, direction);
            double targetOffset = Math.Abs(targetPrice - entryReference);

            var request = new PlaceOrderRequestParameters
            {
                Account = this.CurrentAccount,
                Symbol = this.CurrentSymbol,
                Side = side,
                Quantity = quantity,
                TimeInForce = TimeInForce.Day,
                OrderTypeId = this.marketOrderTypeId,
                StopLoss = SlTpHolder.CreateSL(stopTicks * tick, PriceMeasurement.Offset),
                TakeProfit = SlTpHolder.CreateTP(targetOffset, PriceMeasurement.Offset)
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
                this.DisableForTradingDay("entry order rejected: " + result.Message);
                return;
            }
            this.tradesThisWindow++;
            this.entryTimeEastern = marketTimeEastern;
            this.state = SetupState.Disabled;
            this.Log(string.Format(
                CultureInfo.InvariantCulture,
                "{0}: entry sent: side={1}, qty={2}, stopTicks={3}, targetOffset={4:F2}",
                window.Name, side, quantity, stopTicks, targetOffset), StrategyLoggingLevel.Trading);
        }

        private int SizeForStop(TradingWindow window, int stopTicks)
        {
            double riskPerContract = stopTicks * this.TickValue
                + 2D * this.CommissionPerSide
                + 2D * window.SlippageTicksPerSide * this.TickValue;
            double availableRisk = this.MaximumRiskPerTrade * window.RiskFraction * this.externalRiskMultiplier;
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
                return 0;
            return Math.Min(this.MaximumQuantity, (int)Math.Floor(availableRisk / riskPerContract));
        }

        // ------------------------------------------------------------------
        // Pending wall-limit management (timeout + invalidation)
        // ------------------------------------------------------------------

        private void ManagePendingWallEntry(MinuteBar bar, DateTime marketTimeEastern)
        {
            if (!this.wallEntryPending || this.activePosition != null)
                return;
            double width = this.OpeningRangeWidth;
            bool thesisDead = this.expectedEntrySide == Side.Buy
                ? bar.Close < this.openingRangeHigh - this.FailureCloseFraction * width
                : bar.Close > this.openingRangeLow + this.FailureCloseFraction * width;
            if (thesisDead)
            {
                this.RequestWallEntryCancel("retest thesis failed before the wall limit filled", armFallback: false, marketTimeEastern);
                return;
            }
            if (marketTimeEastern < this.wallEntryDeadlineEastern)
                return;
            this.RequestWallEntryCancel("wall limit timeout", armFallback: this.WallTimeoutFallbackToMarket, marketTimeEastern);
        }

        private void RequestWallEntryCancel(string reason, bool armFallback, DateTime marketTimeEastern)
        {
            if (!this.wallEntryPending)
                return;
            // The limit may fill while the cancel is in flight, so the entry stays
            // "in flight" (waitingForEntryFill remains true) until the platform confirms
            // there is no working entry order. Only then may a fallback market order go out.
            this.wallEntryPending = false;
            this.wallEntryCancelling = true;
            this.wallFallbackArmed = armFallback;
            this.wallFallbackDeadlineEastern = marketTimeEastern.AddMinutes(2);
            string orderId = this.wallEntryOrderId;
            this.Log("Cancelling pending wall entry: " + reason);
            foreach (var order in Core.Instance.Orders.Where(this.IsSelectedOrder).ToArray())
            {
                if (orderId == null || order.Id == orderId)
                {
                    var result = order.Cancel();
                    if (result.Status == TradingOperationResultStatus.Failure)
                        this.Log("Wall entry cancellation failed: " + result.Message, StrategyLoggingLevel.Error);
                }
            }
        }

        private void ReconcileWallEntryCancel(DateTime marketTimeEastern)
        {
            if (!this.wallEntryCancelling)
                return;
            if (this.activePosition != null)
            {
                // The limit filled before the cancel took effect; the position handler owns it.
                this.wallEntryCancelling = false;
                this.wallFallbackArmed = false;
                return;
            }
            if (Core.Instance.Orders.Any(this.IsSelectedOrder))
            {
                if (marketTimeEastern > this.wallFallbackDeadlineEastern)
                {
                    this.wallFallbackArmed = false;
                    this.FlattenAndDisable("wall entry cancellation was not confirmed in time");
                }
                return;
            }

            this.wallEntryCancelling = false;
            this.waitingForEntryFill = false;
            this.wallEntryOrderId = null;
            bool fallback = this.wallFallbackArmed;
            this.wallFallbackArmed = false;
            TradingWindow window = this.activeWindow;
            if (!fallback || window == null || this.tradingDayDisabled
                || marketTimeEastern > this.wallFallbackDeadlineEastern
                || marketTimeEastern >= window.SignalEndEastern)
                return;
            Side side = this.expectedEntrySide;
            bool stillValid = side == Side.Buy
                ? this.CurrentSymbol.Last >= this.openingRangeHigh
                : this.CurrentSymbol.Last <= this.openingRangeLow;
            if (!stillValid)
                return;
            // The wall-limit attempt consumed the window's trade budget; the fallback
            // replaces that same attempt, so the counter is restored before re-entry.
            this.tradesThisWindow = Math.Max(0, this.tradesThisWindow - 1);
            if (!this.PreTradeRiskChecks() || !this.Level1ExecutionAllowed(window))
                return;
            this.Log(window.Name + ": wall limit timed out; falling back to a market entry.", StrategyLoggingLevel.Trading);
            this.SendMarketEntry(window, side, this.CurrentSymbol.Last, this.wallEntryRetestExtreme, marketTimeEastern);
        }

        private void ClearWallEntryState()
        {
            this.wallEntryPending = false;
            this.wallEntryCancelling = false;
            this.wallFallbackArmed = false;
            this.wallEntryOrderId = null;
            this.wallEntryDeadlineEastern = DateTime.MinValue;
            this.wallFallbackDeadlineEastern = DateTime.MinValue;
        }

        // ------------------------------------------------------------------
        // Open-position management
        // ------------------------------------------------------------------

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
                this.FlattenWindowPosition("failed opening-range retest");
                return;
            }
            if (this.entryTimeEastern != DateTime.MinValue
                && marketTimeEastern - this.entryTimeEastern >= TimeSpan.FromMinutes(this.TimeStopMinutes))
                this.FlattenWindowPosition("time stop");
        }

        // ------------------------------------------------------------------
        // Level 2 processing (order-flow composite + wall detection)
        // ------------------------------------------------------------------

        private void CurrentSymbolOnNewLevel2(Symbol symbol, Level2Quote update, DOMQuote snapshot)
        {
            lock (this.sync)
            {
                if (!this.UseLevel2)
                    return;
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
                                LevelsCount = Math.Max(5, this.WallDepthLevels),
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

                    var flow = new OrderFlowSnapshot
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
                    this.DetectWalls(depth.Bids, depth.Asks, flow);
                    this.latestOrderFlow = flow;
                    this.WriteLevel2Row(flow, tradeDelta);
                }
                catch (Exception exception)
                {
                    if (this.activePosition != null)
                        this.FlattenAndDisable("Level 2 processing error: " + exception.Message);
                    else
                        this.DisableForTradingDay("Level 2 processing error: " + exception.Message);
                }
            }
        }

        private void DetectWalls(Level2Item[] bids, Level2Item[] asks, OrderFlowSnapshot flow)
        {
            // A wall is the largest displayed level whose size is a configurable multiple of the
            // median displayed level size on the same side. The adaptive median keeps the test
            // meaningful in thin GTH books and avoids a fitted absolute contract count. A wall
            // must additionally persist across consecutive throttled snapshots (~200 ms apart)
            // before it is trusted, which filters most spoof-and-cancel behavior but cannot
            // eliminate it; walls therefore only ever improve the entry price.
            double bidPrice, bidRatio, askPrice, askRatio;
            bool hasBidWall = this.FindWall(bids, out bidPrice, out bidRatio);
            bool hasAskWall = this.FindWall(asks, out askPrice, out askRatio);

            if (hasBidWall && bidPrice == this.lastBidWallPrice)
                this.bidWallStreak++;
            else
                this.bidWallStreak = hasBidWall ? 1 : 0;
            this.lastBidWallPrice = hasBidWall ? bidPrice : 0D;

            if (hasAskWall && askPrice == this.lastAskWallPrice)
                this.askWallStreak++;
            else
                this.askWallStreak = hasAskWall ? 1 : 0;
            this.lastAskWallPrice = hasAskWall ? askPrice : 0D;

            if (hasBidWall && this.bidWallStreak >= this.WallPersistenceSnapshots)
            {
                flow.BidWallPrice = bidPrice;
                flow.BidWallRatio = bidRatio;
            }
            if (hasAskWall && this.askWallStreak >= this.WallPersistenceSnapshots)
            {
                flow.AskWallPrice = askPrice;
                flow.AskWallRatio = askRatio;
            }
        }

        private bool FindWall(Level2Item[] side, out double price, out double ratio)
        {
            price = 0D;
            ratio = 0D;
            if (side == null || side.Length < 3)
                return false;
            int levels = Math.Min(side.Length, Math.Max(5, this.WallDepthLevels));
            var sizes = new List<double>(levels);
            int bestIndex = -1;
            double bestSize = 0D;
            for (int index = 0; index < levels; index++)
            {
                double size = Math.Max(0D, side[index].Size);
                sizes.Add(size);
                if (size > bestSize)
                {
                    bestSize = size;
                    bestIndex = index;
                }
            }
            if (bestIndex < 0 || !(bestSize > 0D))
                return false;
            sizes.Sort();
            int middle = sizes.Count / 2;
            double median = sizes.Count % 2 == 1 ? sizes[middle] : 0.5D * (sizes[middle - 1] + sizes[middle]);
            if (!(median > 0D))
                return false;
            double candidateRatio = bestSize / median;
            if (candidateRatio < this.MinimumWallRatio)
                return false;
            price = side[bestIndex].Price;
            ratio = candidateRatio;
            return true;
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

        // ------------------------------------------------------------------
        // Order / position events
        // ------------------------------------------------------------------

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
                bool wallEntryInFlight = this.wallEntryPending || this.wallEntryCancelling;
                bool partialWallFill = wallEntryInFlight
                    && position.Side == this.expectedEntrySide
                    && position.Quantity > 0D
                    && position.Quantity <= this.expectedEntryQuantity + 0.000001D;
                if (partialWallFill)
                {
                    if (Math.Abs(position.Quantity - this.expectedEntryQuantity) > 0.000001D)
                    {
                        this.Log(string.Format(
                            CultureInfo.InvariantCulture,
                            "Wall limit partially filled: {0} of {1}; cancelling the remainder.",
                            position.Quantity, this.expectedEntryQuantity));
                        this.CancelUnfilledEntryRemainder();
                    }
                    this.ClearWallEntryState();
                    return;
                }
                this.ClearWallEntryState();
                if (Math.Abs(position.Quantity - this.expectedEntryQuantity) > 0.000001D || position.Side != this.expectedEntrySide)
                    this.FlattenAndDisable("position quantity/side mismatch");
            }
        }

        private void CancelUnfilledEntryRemainder()
        {
            string orderId = this.wallEntryOrderId;
            if (orderId == null)
                return;
            foreach (var order in Core.Instance.Orders.Where(this.IsSelectedOrder).ToArray())
            {
                if (order.Id == orderId)
                {
                    var result = order.Cancel();
                    if (result.Status == TradingOperationResultStatus.Failure)
                        this.Log("Remainder cancellation failed: " + result.Message, StrategyLoggingLevel.Error);
                }
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
                this.ClearWallEntryState();
                this.CancelSelectedOrders();
                if (this.tradesThisWindow >= this.MaximumTradesPerWindow)
                    this.DisableWindow("maximum trades for this window reached");
                else if (!this.tradingDayDisabled && this.activeWindow != null)
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
                    this.DisableForTradingDay("order refused");
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

        // ------------------------------------------------------------------
        // Risk state
        // ------------------------------------------------------------------

        private void UpdateRiskState(DateTime marketTimeEastern)
        {
            if (this.currentTradingDateEastern == DateTime.MinValue)
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
            if (this.tradingDayDisabled || this.flattenRequested || this.activePosition != null || this.waitingForEntryFill)
                return false;
            if (this.HasUnexpectedAccountActivity())
            {
                this.FlattenAndDisable("unexpected position or working order in the selected account");
                return false;
            }
            if (this.tradesThisWindow >= this.MaximumTradesPerWindow)
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

        private bool Level1ExecutionAllowed(TradingWindow window)
        {
            double bid = this.CurrentSymbol.Bid;
            double ask = this.CurrentSymbol.Ask;
            DateTime quoteUtc = this.CurrentSymbol.QuoteDateTime;
            if (quoteUtc.Kind == DateTimeKind.Unspecified)
                quoteUtc = DateTime.SpecifyKind(quoteUtc, DateTimeKind.Utc);
            DateTime now = this.EnableWallClockWatchdog ? DateTime.UtcNow : this.GetMarketUtc();
            double ageMs = (now - quoteUtc.ToUniversalTime()).TotalMilliseconds;
            bool valid = bid > 0D && ask > bid
                && !double.IsInfinity(bid) && !double.IsInfinity(ask)
                && (ask - bid) / this.CurrentSymbol.TickSize <= window.MaxSpreadTicks
                && ageMs >= 0D && ageMs <= this.MaximumQuoteAgeMilliseconds;
            if (!valid)
                this.Log(window.Name + ": entry skipped; L1 quote missing, stale, crossed, or spread too wide.");
            return valid;
        }

        private void ResetTradingDay(DateTime tradeDateEastern)
        {
            if (this.currentTradingDateEastern != DateTime.MinValue
                && this.EnforcePropThreshold
                && this.RequireDailyRestartForPropThreshold)
            {
                this.currentTradingDateEastern = tradeDateEastern;
                this.BuildTradingWindows(tradeDateEastern);
                this.DisableForTradingDay("new trading date requires a restart and refreshed prop liquidation threshold");
                return;
            }
            if (Core.Instance.Positions.Any(this.IsSelectedAccountPosition)
                || Core.Instance.Orders.Any(this.IsSelectedAccountOrder))
            {
                this.FlattenAndDisable("account exposure carried into a new trading date");
                return;
            }
            double refreshedAtr = this.currentTradingDateEastern == DateTime.MinValue && this.dailyAtr > 0D
                ? this.dailyAtr
                : (this.DailyAtrOverride > 0D ? this.DailyAtrOverride : this.LoadMedianDailyTrueRange());
            if (!(refreshedAtr > 0D))
            {
                this.DisableForTradingDay("unable to refresh a positive daily ATR");
                return;
            }
            this.dailyAtr = refreshedAtr;
            this.currentTradingDateEastern = tradeDateEastern;
            this.BuildTradingWindows(tradeDateEastern);
            this.activeWindow = null;
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
            this.lastBidWallPrice = 0D;
            this.bidWallStreak = 0;
            this.lastAskWallPrice = 0D;
            this.askWallStreak = 0;
            this.ClearWallEntryState();
            this.tradesThisWindow = 0;
            this.excessiveSlippageEvents = 0;
            this.rangeLocked = false;
            this.waitingForEntryFill = false;
            this.entryFillSeen = false;
            this.flattenRequested = false;
            this.tradingDayDisabled = false;
            this.state = SetupState.Disabled;
            this.sessionStartingBalance = this.CurrentAccount.Balance;
            this.externalDirection = RegimeDirection.Both;
            this.externalRiskMultiplier = 1D;
            this.LoadExternalRegime(tradeDateEastern);
            this.Log("Trading day reset for " + tradeDateEastern.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        }

        private void LoadExternalRegime(DateTime tradeDateEastern)
        {
            if (string.IsNullOrWhiteSpace(this.ExternalRegimeCsvPath))
            {
                if (this.RequireExternalRegime)
                    this.DisableForTradingDay("required external regime path is empty");
                return;
            }
            try
            {
                string expectedDate = tradeDateEastern.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
                string selected = File.ReadLines(this.ExternalRegimeCsvPath)
                    .Where(line => !string.IsNullOrWhiteSpace(line) && !line.StartsWith("session_date", StringComparison.OrdinalIgnoreCase))
                    .LastOrDefault(line => line.Split(',')[0].Trim() == expectedDate);
                if (selected == null)
                    throw new InvalidDataException("no row for the current trading date");
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
                    this.DisableForTradingDay("external regime unavailable: " + exception.Message);
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
                if (this.currentTradingDateEastern == DateTime.MinValue || this.tradingDayDisabled)
                    return;
                DateTime eastern = this.ToEastern(DateTime.UtcNow);
                if (this.activeWindow == null || !this.activeWindow.Contains(eastern))
                    return;
                this.ReconcileWallEntryCancel(eastern);
                if (this.wallEntryPending && eastern >= this.wallEntryDeadlineEastern.AddMinutes(1))
                {
                    // Backstop for quiet tapes: the minute-close handler normally manages the
                    // timeout, but a pending limit must never linger without prints. No market
                    // fallback here: an illiquid tape is not a place to pay the spread.
                    this.RequestWallEntryCancel("wall limit timeout (watchdog)", armFallback: false, eastern);
                }
                if (this.lastTickWallClockUtc != DateTime.MinValue && (DateTime.UtcNow - this.lastTickWallClockUtc).TotalSeconds > 5D)
                {
                    if (this.activePosition != null || this.waitingForEntryFill)
                        this.FlattenAndDisable("last-trade feed stale for more than five seconds");
                    return;
                }
                if (this.UseLevel2
                    && this.lastLevel2WallClockUtc != DateTime.MinValue
                    && (DateTime.UtcNow - this.lastLevel2WallClockUtc).TotalMilliseconds > Math.Max(2000, 2 * this.Level2FreshnessMilliseconds)
                    && (this.activePosition != null || this.waitingForEntryFill))
                    this.FlattenAndDisable("Level 2 feed stale");
            }
        }

        // ------------------------------------------------------------------
        // Shutdown helpers
        // ------------------------------------------------------------------

        private void FlattenWindowPosition(string reason)
        {
            // Window-scoped flatten: close exposure and stop trading this window, but keep
            // later windows of the same trading day alive.
            this.Log("Window flatten: " + reason);
            this.ClearWallEntryState();
            this.waitingForEntryFill = false;
            this.state = SetupState.Disabled;
            foreach (var position in Core.Instance.Positions.Where(this.IsSelectedPosition).ToArray())
            {
                var result = position.Close();
                if (result.Status == TradingOperationResultStatus.Failure)
                    this.Log("Window close failed: " + result.Message, StrategyLoggingLevel.Error);
            }
            this.CancelSelectedOrders();
        }

        private void FlattenAndDisable(string reason)
        {
            if (this.flattenRequested)
                return;
            this.flattenRequested = true;
            this.tradingDayDisabled = true;
            this.state = SetupState.Disabled;
            this.ClearWallEntryState();
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

        private void DisableWindow(string reason)
        {
            this.state = SetupState.Disabled;
            this.Log("Window disabled: " + reason);
        }

        private void DisableForTradingDay(string reason)
        {
            this.tradingDayDisabled = true;
            this.state = SetupState.Disabled;
            this.Log("Trading day disabled: " + reason, StrategyLoggingLevel.Error);
        }

        // ------------------------------------------------------------------
        // Small helpers
        // ------------------------------------------------------------------

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

        // ------------------------------------------------------------------
        // Level 2 recording
        // ------------------------------------------------------------------

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
                    this.level2Writer.WriteLine("received_utc,best_bid,best_ask,best_bid_size,best_ask_size,depth_imbalance,ofi_norm,trade_delta_norm,microprice_ticks,composite,signed_persistence,spread_ticks,bid_wall_price,bid_wall_ratio,ask_wall_price,ask_wall_ratio");
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
                    snapshot.SpreadTicks.ToString("R", CultureInfo.InvariantCulture),
                    snapshot.BidWallPrice.HasValue ? snapshot.BidWallPrice.Value.ToString("R", CultureInfo.InvariantCulture) : string.Empty,
                    snapshot.BidWallRatio.HasValue ? snapshot.BidWallRatio.Value.ToString("R", CultureInfo.InvariantCulture) : string.Empty,
                    snapshot.AskWallPrice.HasValue ? snapshot.AskWallPrice.Value.ToString("R", CultureInfo.InvariantCulture) : string.Empty,
                    snapshot.AskWallRatio.HasValue ? snapshot.AskWallRatio.Value.ToString("R", CultureInfo.InvariantCulture) : string.Empty
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

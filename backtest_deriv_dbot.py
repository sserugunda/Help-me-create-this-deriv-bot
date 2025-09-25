import argparse
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd


def compute_wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """Compute RSI using Wilder's smoothing (classic RSI)."""
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)

    # Wilder's RMA via EWM alpha = 1/period
    avg_gain = gains.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(method="bfill").fillna(50.0)


def ensure_datetime_index(df: pd.DataFrame, ts_col: str) -> pd.DataFrame:
    if np.issubdtype(df[ts_col].dtype, np.number):
        sample = df[ts_col].iloc[0]
        if sample > 10_000_000_000:  # ms
            df["_dt"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
        else:  # seconds
            df["_dt"] = pd.to_datetime(df[ts_col], unit="s", utc=True)
    else:
        df["_dt"] = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
    df = df.dropna(subset=["_dt"]).copy()
    df = df.sort_values("_dt").set_index("_dt")
    return df


def resample_to_2min(df_1m: pd.DataFrame) -> pd.DataFrame:
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in df_1m.columns:
        agg["volume"] = "sum"
    df_2m = df_1m.resample("2T", label="right", closed="right").agg(agg)
    return df_2m.dropna(subset=["open", "high", "low", "close"])


def align_latest_prior(series: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """Align a lower-frequency series to a target index using last known value (asof)."""
    s = series.sort_index().to_frame("val")
    s["idx"] = s.index
    t = pd.DataFrame({"idx": target_index})
    merged = pd.merge_asof(
        t.sort_values("idx"),
        s.reset_index().rename(columns={"index": "series_idx"}),
        left_on="idx",
        right_on="idx",
        direction="backward",
        allow_exact_matches=True,
    )
    out = pd.Series(merged["val"].values, index=merged["idx"].values)
    out.index = pd.to_datetime(out.index)
    return out.reindex(target_index)


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str  # "CALL" or "PUT"
    entry_open: float
    exit_close: float
    stake: float
    payout_rate: float
    pnl: float
    win: bool


@dataclass
class BacktestResult:
    trades: List[Trade]
    starting_balance: float
    final_balance: float
    total_profit: float
    num_trades: int
    wins: int
    losses: int
    win_rate: float


def backtest_strategy(
    df_1m: pd.DataFrame,
    payout_rate: float = 0.95,
    starting_balance: float = 10_000.0,
    target_profit: float = 1_000.0,
    max_trades: int = 200,
) -> BacktestResult:
    """
    Strategy from XML (DBot):
      - Timeframe: 1-minute candles, expiry 1 minute.
      - RSI filters: RSI(2) on 1m and 2m must both be >51 for CALL, <49 for PUT.
      - Candle filter: Use previous 1m candle direction (close>open for CALL, open>close for PUT).
      - Important: RSI values are taken from 2 candles back (FROM_END=2), so use t-2.
      - Entry at open[t], settle at close[t]. Ties are loss (strict Rise/Fall semantics).
      - Stake is 1% of current balance each trade. Stop when trades reach max_trades or profit >= target.
    """
    df_1m = df_1m.copy()

    # Compute RSI on 1-minute closes with period 2
    rsi_1m = compute_wilder_rsi(df_1m["close"], period=2)

    # Build 2-minute bars and RSI(2), align to 1m timeline by last known value
    df_2m = resample_to_2min(df_1m)
    rsi_2m = compute_wilder_rsi(df_2m["close"], period=2)
    rsi_2m_on_1m = align_latest_prior(rsi_2m, df_1m.index)

    balance = starting_balance
    cumulative_profit = 0.0
    trades: List[Trade] = []
    wins = 0
    losses = 0
    num_trades = 0

    # Start from i=2 to access t-2 (RSI) and t-1 (prev candle)
    for i in range(2, len(df_1m)):
        if num_trades >= max_trades or cumulative_profit >= target_profit:
            break

        t_prev = df_1m.index[i - 1]
        t_curr = df_1m.index[i]
        t_rsi = df_1m.index[i - 2]

        prev_open = float(df_1m.loc[t_prev, "open"])
        prev_close = float(df_1m.loc[t_prev, "close"])
        curr_open = float(df_1m.loc[t_curr, "open"])
        curr_close = float(df_1m.loc[t_curr, "close"])

        rsi1_use = float(rsi_1m.loc[t_rsi])
        rsi2_use = float(rsi_2m_on_1m.loc[t_rsi])

        take_call = (rsi1_use > 51.0) and (rsi2_use > 51.0) and (prev_close > prev_open)
        take_put = (rsi1_use < 49.0) and (rsi2_use < 49.0) and (prev_open > prev_close)

        if not (take_call or take_put):
            continue

        stake = round(balance * 0.01, 2)
        if stake <= 0:
            continue

        direction = "CALL" if take_call else "PUT"

        # Settlement on current candle; ties count as loss
        if direction == "CALL":
            win = curr_close > curr_open
        else:
            win = curr_close < curr_open

        pnl = stake * payout_rate if win else -stake
        balance += pnl
        cumulative_profit += pnl
        num_trades += 1
        if win:
            wins += 1
        else:
            losses += 1

        trades.append(
            Trade(
                entry_time=t_curr,
                exit_time=t_curr,
                direction=direction,
                entry_open=curr_open,
                exit_close=curr_close,
                stake=stake,
                payout_rate=payout_rate,
                pnl=pnl,
                win=win,
            )
        )

    win_rate = (wins / num_trades) * 100.0 if num_trades > 0 else 0.0
    return BacktestResult(
        trades=trades,
        starting_balance=starting_balance,
        final_balance=balance,
        total_profit=cumulative_profit,
        num_trades=num_trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
    )


def load_ohlcv_csv(
    path: str,
    ts_col: str = "timestamp",
    open_col: str = "open",
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    volume_col: Optional[str] = None,
) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = ensure_datetime_index(df, ts_col)
    rename_map = {open_col: "open", high_col: "high", low_col: "low", close_col: "close"}
    if volume_col and volume_col in df.columns:
        rename_map[volume_col] = "volume"
    df = df.rename(columns=rename_map)
    keep_cols = ["open", "high", "low", "close"]
    if "volume" in df.columns:
        keep_cols.append("volume")
    return df[keep_cols]


def result_to_dataframe(result: BacktestResult) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "entry_time": tr.entry_time,
            "exit_time": tr.exit_time,
            "direction": tr.direction,
            "entry_open": tr.entry_open,
            "exit_close": tr.exit_close,
            "stake": tr.stake,
            "payout_rate": tr.payout_rate,
            "pnl": tr.pnl,
            "win": tr.win,
        }
        for tr in result.trades
    ])


def main():
    parser = argparse.ArgumentParser(description="Backtest DBot RSI 1m/2m strategy on 1-minute OHLCV CSV.")
    parser.add_argument("--csv", required=True, help="Path to 1-minute OHLCV CSV for 1HZ10V.")
    parser.add_argument("--timestamp-col", default="timestamp", help="Timestamp column name.")
    parser.add_argument("--open-col", default="open", help="Open column name.")
    parser.add_argument("--high-col", default="high", help="High column name.")
    parser.add_argument("--low-col", default="low", help="Low column name.")
    parser.add_argument("--close-col", default="close", help="Close column name.")
    parser.add_argument("--volume-col", default=None, help="Volume column name (optional).")
    parser.add_argument("--payout", type=float, default=0.95, help="Binary option payout rate on wins (e.g., 0.95).")
    parser.add_argument("--start-balance", type=float, default=10_000.0, help="Starting balance.")
    parser.add_argument("--target-profit", type=float, default=1_000.0, help="Target profit to stop.")
    parser.add_argument("--max-trades", type=int, default=200, help="Max trades to execute.")
    parser.add_argument("--trades-csv", default=None, help="Optional path to export trades CSV.")
    args = parser.parse_args()

    df_1m = load_ohlcv_csv(
        path=args.csv,
        ts_col=args.timestamp_col,
        open_col=args.open_col,
        high_col=args.high_col,
        low_col=args.low_col,
        close_col=args.close_col,
        volume_col=args.volume_col,
    )

    result = backtest_strategy(
        df_1m=df_1m,
        payout_rate=args.payout,
        starting_balance=args.start_balance,
        target_profit=args.target_profit,
        max_trades=args.max_trades,
    )

    print("Backtest summary")
    print("----------------")
    print(f"Trades: {result.num_trades}")
    print(f"Wins: {result.wins}  Losses: {result.losses}  Win rate: {result.win_rate:.2f}%")
    print(f"Starting balance: {result.starting_balance:,.2f}")
    print(f"Final balance:    {result.final_balance:,.2f}")
    print(f"Total profit:     {result.total_profit:,.2f}")

    if args.trades_csv:
        result_to_dataframe(result).to_csv(args.trades_csv, index=False)
        print(f"Trades exported to {args.trades_csv}")


if __name__ == "__main__":
    main()


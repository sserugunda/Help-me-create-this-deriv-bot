import io
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st


# ----------------------------
# Helpers: Indicators
# ----------------------------
def compute_rsi_wilder(close: pd.Series, period: int) -> pd.Series:

    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    # Wilder smoothing via EMA with alpha=1/period (good approximation)
    roll_up = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = roll_up / (roll_down.replace(0, np.nan))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(method="bfill")


def add_tr_and_body_ratio(df: pd.DataFrame) -> pd.DataFrame:

    df = df.copy()
    df["prev_close"] = df["close"].shift(1)
    tr1 = (df["high"] - df["low"]).abs()
    tr2 = (df["high"] - df["prev_close"]).abs()
    tr3 = (df["low"] - df["prev_close"]).abs()
    df["tr_max"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    body = (df["close"] - df["open"]).abs()
    df["body_over_tr"] = body / df["tr_max"].replace(0, np.nan)
    df["body_over_tr"] = df["body_over_tr"].fillna(0.0)
    return df


def add_atr_mult(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:

    df = df.copy()
    # ATR here is simple SMA of TR over `period` bars per the XML logic (sum(list)/period)
    atr = df["tr_max"].rolling(window=period, min_periods=period).mean()
    df["atr_mult"] = df["tr_max"] / atr
    df["atr_mult"] = df["atr_mult"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return df


# ----------------------------
# Backtest Engine
# ----------------------------
@dataclass
class Trade:
    direction: str  # "CALL" or "PUT"
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stake: float
    profit: float
    balance_after: float


def run_backtest(
    ticks: pd.DataFrame,
    candles_1m: pd.DataFrame,
    candles_2m: pd.DataFrame,
    initial_balance: float,
    stake_pct: float,
    payout: float,
    target_profit: float,
    max_trades: int,
) -> Tuple[List[Trade], float]:

    # Indexing and sorting
    ticks = ticks.sort_values("timestamp").reset_index(drop=True)
    candles_1m = candles_1m.sort_values("timestamp").set_index("timestamp")
    candles_2m = candles_2m.sort_values("timestamp").set_index("timestamp")

    # Enrich 1m candles
    c1 = add_tr_and_body_ratio(candles_1m)
    c1["rsi1m"] = compute_rsi_wilder(c1["close"], period=2)
    c1 = add_atr_mult(c1, period=20)
    # Use previous closed candle (index 2 from end in XML) for signals
    c1["m1_low_prev"] = c1["low"].shift(1)
    c1["m1_high_prev"] = c1["high"].shift(1)
    c1["body_over_tr_prev"] = c1["body_over_tr"].shift(1)
    c1["atr_mult_prev"] = c1["atr_mult"].shift(1)
    c1["rsi1m_prev"] = c1["rsi1m"].shift(1)

    # Enrich 2m candles
    c2 = candles_2m.copy()
    c2["rsi2m"] = compute_rsi_wilder(c2["close"], period=2)
    c2["rsi2m_prev"] = c2["rsi2m"].shift(1)

    # Resample from 1m to 1H and 1D for bias checks
    h1 = (
        candles_1m[["open", "high", "low", "close"]]
        .resample("1H", label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    d1 = (
        candles_1m[["open", "high", "low", "close"]]
        .resample("1D", label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    # Merge-asof tick -> last closed candle(s)
    tdf = ticks.copy()
    tdf = tdf.sort_values("timestamp").reset_index(drop=True)

    # 1m context at tick time: last closed 1m candle
    tdf = pd.merge_asof(
        tdf,
        c1[[
            "open",
            "high",
            "low",
            "close",
            "rsi1m",
            "rsi1m_prev",
            "body_over_tr",
            "body_over_tr_prev",
            "atr_mult",
            "atr_mult_prev",
            "m1_low_prev",
            "m1_high_prev",
        ]].reset_index().rename(columns={
            "open": "m1_open",
            "high": "m1_high",
            "low": "m1_low",
            "close": "m1_close",
            "timestamp": "m1_timestamp",
        }),
        left_on="timestamp",
        right_on="timestamp",
        direction="backward",
    )

    # 2m context
    tdf = pd.merge_asof(
        tdf,
        c2[["rsi2m", "rsi2m_prev"]].reset_index().rename(columns={"timestamp": "m2_timestamp"}),
        left_on="timestamp",
        right_on="m2_timestamp",
        direction="backward",
    )

    # Hourly bias (previous closed H1)
    tdf = pd.merge_asof(
        tdf,
        h1[["open", "close"]].reset_index().rename(
            columns={"open": "h1_open", "close": "h1_close", "timestamp": "h1_timestamp"}
        ),
        left_on="timestamp",
        right_on="timestamp",
        direction="backward",
    )

    # Daily bias (previous closed D1)
    tdf = pd.merge_asof(
        tdf,
        d1[["open", "close"]].reset_index().rename(
            columns={"open": "d1_open", "close": "d1_close", "timestamp": "d1_timestamp"}
        ),
        left_on="timestamp",
        right_on="timestamp",
        direction="backward",
    )

    # Prepare numpy arrays for speed
    times = tdf["timestamp"].to_numpy()
    prices = tdf["price"].to_numpy()
    rsi1m_prev = tdf["rsi1m_prev"].to_numpy()
    rsi2m_prev = tdf["rsi2m_prev"].to_numpy()
    low1m_prev = tdf["m1_low_prev"].to_numpy()
    high1m_prev = tdf["m1_high_prev"].to_numpy()
    body_over_tr_prev = tdf["body_over_tr_prev"].to_numpy()
    atr_mult_prev = tdf["atr_mult_prev"].to_numpy()
    h1_open = tdf["h1_open"].to_numpy()
    h1_close = tdf["h1_close"].to_numpy()
    d1_open = tdf["d1_open"].to_numpy()
    d1_close = tdf["d1_close"].to_numpy()

    # Trading state
    balance = float(initial_balance)
    equity = balance
    trades: List[Trade] = []
    open_trade_idx: Optional[int] = None
    open_trade_dir: Optional[str] = None
    open_trade_price: Optional[float] = None
    open_trade_stake: Optional[float] = None

    total_profit = 0.0
    total_trades = 0

    n = len(tdf)
    i = 0
    while i < n:
        # Close trade if expiry is reached
        if open_trade_idx is not None and i >= open_trade_idx + 60:
            exit_price = float(prices[i])
            entry_price = float(open_trade_price)
            direction = str(open_trade_dir)
            stake = float(open_trade_stake)
            result_profit = 0.0

            if direction == "CALL":
                if exit_price > entry_price:
                    result_profit = stake * payout
                elif exit_price < entry_price:
                    result_profit = -stake
                else:
                    result_profit = 0.0
            else:  # PUT
                if exit_price < entry_price:
                    result_profit = stake * payout
                elif exit_price > entry_price:
                    result_profit = -stake
                else:
                    result_profit = 0.0

            balance += result_profit
            total_profit += result_profit
            total_trades += 1

            trades.append(
                Trade(
                    direction=direction,
                    entry_time=pd.Timestamp(times[open_trade_idx]),
                    exit_time=pd.Timestamp(times[i]),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stake=stake,
                    profit=result_profit,
                    balance_after=balance,
                )
            )

            # Reset for next opportunity
            open_trade_idx = None
            open_trade_dir = None
            open_trade_price = None
            open_trade_stake = None

            # Limits
            if total_trades >= max_trades or total_profit >= target_profit:
                break

        # If no open trade, evaluate entry conditions
        if open_trade_idx is None:
            # Guard NaNs
            cond_inputs_ok = (
                not np.isnan(rsi1m_prev[i])
                and not np.isnan(rsi2m_prev[i])
                and not np.isnan(low1m_prev[i])
                and not np.isnan(high1m_prev[i])
                and not np.isnan(body_over_tr_prev[i])
                and not np.isnan(atr_mult_prev[i])
                and not np.isnan(h1_open[i])
                and not np.isnan(h1_close[i])
                and not np.isnan(d1_open[i])
                and not np.isnan(d1_close[i])
            )

            if cond_inputs_ok:
                threshold = 0.5 / atr_mult_prev[i] if atr_mult_prev[i] != 0 else np.inf
                # CALL conditions
                call_signal = (
                    (rsi2m_prev[i] > 51)
                    and (rsi1m_prev[i] > 51)
                    and (prices[i] < low1m_prev[i])
                    and (h1_open[i] > h1_close[i])
                    and (body_over_tr_prev[i] > threshold)
                    and (d1_close[i] > d1_open[i])
                )
                # PUT conditions
                put_signal = (
                    (rsi2m_prev[i] < 49)
                    and (rsi1m_prev[i] < 49)
                    and (prices[i] > high1m_prev[i])
                    and (h1_close[i] > h1_open[i])
                    and (body_over_tr_prev[i] > threshold)
                    and (d1_open[i] > d1_close[i])
                )

                if call_signal or put_signal:
                    direction = "CALL" if call_signal else "PUT"
                    stake = max(0.0, balance * stake_pct)
                    if stake > 0:
                        open_trade_idx = i
                        open_trade_dir = direction
                        open_trade_price = float(prices[i])
                        open_trade_stake = float(stake)

        i += 1

    return trades, balance


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="XML Strategy Backtester", layout="wide")
st.title("Backtest: RSI + Tick + ATR Strategy (Upload Ticks, 1m, 2m)")

st.markdown(
    "Upload three CSV files with the following schemas:\n\n"
    "- Ticks: columns = `timestamp`, `price`\n"
    "- 1-minute candles: columns = `timestamp`, `open`, `high`, `low`, `close`\n"
    "- 2-minute candles: columns = `timestamp`, `open`, `high`, `low`, `close`\n\n"
    "Timestamps should be in ISO-8601 or epoch milliseconds/seconds."
)

col_u1, col_u2, col_u3 = st.columns(3)
with col_u1:
    up_ticks = st.file_uploader("Upload Ticks CSV", type=["csv"], key="ticks")
with col_u2:
    up_1m = st.file_uploader("Upload 1-Min Candles CSV", type=["csv"], key="m1")
with col_u3:
    up_2m = st.file_uploader("Upload 2-Min Candles CSV", type=["csv"], key="m2")

st.divider()

col_p1, col_p2, col_p3, col_p4 = st.columns(4)
with col_p1:
    initial_balance = st.number_input("Initial Balance", min_value=0.0, value=1000.0, step=50.0)
with col_p2:
    stake_pct = st.slider("Stake % of Balance", min_value=1, max_value=50, value=10, step=1) / 100.0
with col_p3:
    payout = st.slider("Win Payout (Return % of Stake)", min_value=50, max_value=100, value=90, step=1) / 100.0
with col_p4:
    target_profit = st.number_input("Target Profit (Stop after reaching)", min_value=0.0, value=100.0, step=10.0)

col_l1, col_l2 = st.columns(2)
with col_l1:
    max_trades = st.number_input("Max Trades", min_value=1, value=200, step=10)
with col_l2:
    run_btn = st.button("Run Backtest", type="primary", use_container_width=True)


def _parse_timestamp(series: pd.Series) -> pd.Series:

    # Try to detect epoch seconds/milliseconds
    s = series.copy()
    if np.issubdtype(s.dtype, np.number):
        # Heuristic: if values are large, treat as ms
        median_val = float(np.nanmedian(s.to_numpy()))
        if median_val > 1e12:  # ms
            return pd.to_datetime(s, unit="ms", utc=True).dt.tz_convert(None)
        elif median_val > 1e9:  # s
            return pd.to_datetime(s, unit="s", utc=True).dt.tz_convert(None)
        else:
            # Fallback to ns
            return pd.to_datetime(s, unit="ns", utc=True).dt.tz_convert(None)
    else:
        return pd.to_datetime(s, utc=True, errors="coerce").dt.tz_convert(None)


def _load_ticks(file: io.BytesIO) -> pd.DataFrame:

    df = pd.read_csv(file)
    if "timestamp" not in df.columns or "price" not in df.columns:
        raise ValueError("Ticks CSV must contain 'timestamp' and 'price' columns.")
    df["timestamp"] = _parse_timestamp(df["timestamp"]).astype("datetime64[ns]")
    df = df.dropna(subset=["timestamp", "price"]).sort_values("timestamp").reset_index(drop=True)
    return df[["timestamp", "price"]]


def _load_candles(file: io.BytesIO, label: str) -> pd.DataFrame:

    df = pd.read_csv(file)
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{label} candles CSV missing columns: {sorted(missing)}")
    df["timestamp"] = _parse_timestamp(df["timestamp"]).astype("datetime64[ns]")
    df = (
        df.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    )
    return df[["timestamp", "open", "high", "low", "close"]]


if run_btn:
    if not (up_ticks and up_1m and up_2m):
        st.error("Please upload all three files: ticks, 1m, and 2m.")
        st.stop()

    try:
        ticks_df = _load_ticks(up_ticks)
        m1_df = _load_candles(up_1m, "1m")
        m2_df = _load_candles(up_2m, "2m")
    except Exception as e:
        st.exception(e)
        st.stop()

    with st.spinner("Running backtest..."):
        trades, final_balance = run_backtest(
            ticks=ticks_df,
            candles_1m=m1_df,
            candles_2m=m2_df,
            initial_balance=initial_balance,
            stake_pct=stake_pct,
            payout=payout,
            target_profit=target_profit,
            max_trades=int(max_trades),
        )

    # Results
    trades_df = pd.DataFrame([t.__dict__ for t in trades]) if trades else pd.DataFrame(
        columns=[
            "entry_time",
            "exit_time",
            "direction",
            "entry_price",
            "exit_price",
            "stake",
            "profit",
            "balance_after",
        ]
    )
    net_profit = float(trades_df["profit"].sum()) if not trades_df.empty else 0.0
    wins = int((trades_df["profit"] > 0).sum()) if not trades_df.empty else 0
    losses = int((trades_df["profit"] < 0).sum()) if not trades_df.empty else 0
    pushes = int((trades_df["profit"] == 0).sum()) if not trades_df.empty else 0
    win_rate = (wins / max(1, len(trades_df))) * 100.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Trades", f"{len(trades_df)}")
    c2.metric("Win Rate", f"{win_rate:.2f}%")
    c3.metric("Net Profit", f"{net_profit:.2f}")
    c4.metric("Final Balance", f"{final_balance:.2f}")

    st.subheader("Trades")
    st.dataframe(trades_df, use_container_width=True, height=360)

    if not trades_df.empty:
        csv = trades_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download Trades CSV",
            data=csv,
            file_name="backtest_trades.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.subheader("Equity Curve")
    eq_vals = [initial_balance]
    if not trades_df.empty:
        eq_vals.extend(trades_df["balance_after"].tolist())
    eq = pd.Series(eq_vals, index=list(range(len(eq_vals))))
    st.line_chart(eq.rename("Balance"))


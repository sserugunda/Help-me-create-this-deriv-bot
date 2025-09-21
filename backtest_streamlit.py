import io
from typing import Optional, Tuple

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st


def guess_price_column(columns: list[str]) -> Optional[str]:
    candidates = [
        "bid",
        "Bid",
        "BID",
        "last",
        "Last",
        "PRICE",
        "price",
        "Price",
        "close",
        "Close",
        "quote",
        "Quote",
    ]
    for name in candidates:
        if name in columns:
            return name
    # Fallback: any numeric-looking column
    for name in columns:
        if name.lower() not in {"time", "timestamp", "datetime", "epoch"}:
            return name
    return None


def guess_time_column(columns: list[str]) -> Optional[str]:
    candidates = ["timestamp", "Timestamp", "time", "Time", "datetime", "Datetime", "epoch", "Epoch"]
    for name in candidates:
        if name in columns:
            return name
    return None


def ensure_datetime(series: pd.Series) -> pd.Series:
    name_lower = series.name.lower()
    if name_lower == "epoch" or "epoch" in name_lower:
        return pd.to_datetime(series.astype(float), unit="s", utc=True)
    # Try auto parse
    try:
        return pd.to_datetime(series, utc=True, errors="coerce")
    except Exception:
        # Fallback to index order if unparsable
        return pd.to_datetime(np.arange(len(series)), unit="s", utc=True)


def compute_indicators(
    price: pd.Series,
    fast_period: int,
    slow_period: int,
    atr_period: int,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = price.ewm(span=fast_period, adjust=False).mean()
    ema_slow = price.ewm(span=slow_period, adjust=False).mean()
    true_range = price.diff().abs()
    # Wilder-style ATR approximation via EMA with alpha=1/period
    atr = true_range.ewm(alpha=1 / max(1, atr_period), adjust=False).mean()
    return ema_fast, ema_slow, atr


def compute_heads(price: pd.Series, lookback_len: int = 21) -> Tuple[pd.Series, pd.Series]:
    # BHead/RHead replicate: max/min over previous N ticks, excluding current
    bhead = price.shift(1).rolling(window=lookback_len, min_periods=lookback_len).max()
    rhead = price.shift(1).rolling(window=lookback_len, min_periods=lookback_len).min()
    return bhead, rhead


def backtest(
    df: pd.DataFrame,
    price_col: str,
    time_col: str,
    fast_period: int = 5,
    slow_period: int = 20,
    atr_period: int = 14,
    atr_min: float = 0.03,
    break_factor: float = 0.2,
    eps: float = 0.0,
    cooldown: int = 30,
    duration: int = 10,
    lookback_len: int = 21,
) -> Tuple[pd.DataFrame, dict]:
    df = df.copy()
    df[price_col] = pd.to_numeric(df[price_col], errors="coerce")
    df = df.dropna(subset=[price_col])
    df = df.sort_values(time_col).reset_index(drop=True)

    price = df[price_col]
    ema_fast, ema_slow, atr = compute_indicators(price, fast_period, slow_period, atr_period)
    bhead, rhead = compute_heads(price, lookback_len)

    df["ema_fast"] = ema_fast
    df["ema_slow"] = ema_slow
    df["atr"] = atr
    df["BHead"] = bhead
    df["RHead"] = rhead

    trade_records = []
    last_trade_idx = -10_000

    for i in range(len(df)):
        # Ensure enough history and cooldown
        if i - last_trade_idx < cooldown:
            continue
        if i + duration >= len(df):
            break
        row = df.iloc[i]
        prev_row = df.iloc[i - 1] if i > 0 else None

        # Skip if indicators not ready
        if (
            pd.isna(row["ema_fast"]) or pd.isna(row["ema_slow"]) or pd.isna(row["atr"]) or pd.isna(row["BHead"]) or pd.isna(row["RHead"])
        ):
            continue

        price_i = row[price_col]
        ema_fast_i = row["ema_fast"]
        ema_slow_i = row["ema_slow"]
        atr_i = row["atr"]
        bhead_i = row["BHead"]
        rhead_i = row["RHead"]
        ema_fast_prev = prev_row["ema_fast"] if prev_row is not None else np.nan

        # Trend confirmations
        up_trend = (ema_fast_i > ema_slow_i) and (not pd.isna(ema_fast_prev) and ema_fast_i > ema_fast_prev) and (price_i >= ema_slow_i)
        down_trend = (ema_fast_i < ema_slow_i) and (not pd.isna(ema_fast_prev) and ema_fast_i < ema_fast_prev) and (price_i <= ema_slow_i)
        atr_ok = atr_i >= atr_min

        # Breakout with ATR buffer and eps
        call_breakout = (price_i > bhead_i) and ((price_i - bhead_i) >= break_factor * atr_i) and (price_i >= bhead_i + eps)
        put_breakout = (price_i < rhead_i) and ((rhead_i - price_i) >= break_factor * atr_i) and (price_i <= rhead_i - eps)

        took_trade = False
        if up_trend and atr_ok and call_breakout:
            entry_price = price_i
            exit_price = df.iloc[i + duration][price_col]
            result = 1 if exit_price > entry_price else 0  # equal counts as loss
            trade_records.append(
                {
                    "time": df.iloc[i][time_col],
                    "index": i,
                    "side": "CALL",
                    "entry": float(entry_price),
                    "exit_index": i + duration,
                    "exit_time": df.iloc[i + duration][time_col],
                    "exit": float(exit_price),
                    "win": result,
                }
            )
            last_trade_idx = i
            took_trade = True

        if (not took_trade) and down_trend and atr_ok and put_breakout:
            entry_price = price_i
            exit_price = df.iloc[i + duration][price_col]
            result = 1 if exit_price < entry_price else 0
            trade_records.append(
                {
                    "time": df.iloc[i][time_col],
                    "index": i,
                    "side": "PUT",
                    "entry": float(entry_price),
                    "exit_index": i + duration,
                    "exit_time": df.iloc[i + duration][time_col],
                    "exit": float(exit_price),
                    "win": result,
                }
            )
            last_trade_idx = i

    trades = pd.DataFrame(trade_records)
    total = int(trades.shape[0]) if not trades.empty else 0
    wins = int(trades["win"].sum()) if total > 0 else 0
    winrate = (wins / total * 100.0) if total > 0 else 0.0

    metrics = {"trades": total, "wins": wins, "winrate_pct": winrate}
    return trades, metrics


def render_chart(df: pd.DataFrame, price_col: str, time_col: str, trades: pd.DataFrame):
    base = alt.Chart(df).encode(x=alt.X(f"{time_col}:T", title="Time"))
    price_line = base.mark_line(color="#4e79a7").encode(y=alt.Y(f"{price_col}:Q", title="Price"))
    ema_fast_line = base.mark_line(color="#f28e2c").encode(y="ema_fast:Q")
    ema_slow_line = base.mark_line(color="#e15759").encode(y="ema_slow:Q")

    layers = [price_line, ema_fast_line, ema_slow_line]

    if not trades.empty:
        trades_plot = trades.copy()
        trades_plot["y"] = trades_plot["entry"]
        call_points = (
            alt.Chart(trades_plot[trades_plot["side"] == "CALL"]).mark_point(shape="triangle-up", size=80, color="#2ca02c")
            .encode(x=alt.X("time:T"), y=alt.Y("y:Q"), tooltip=["time:T", "side:N", "entry:Q", "exit:Q", "win:N"])
        )
        put_points = (
            alt.Chart(trades_plot[trades_plot["side"] == "PUT"]).mark_point(shape="triangle-down", size=80, color="#d62728")
            .encode(x=alt.X("time:T"), y=alt.Y("y:Q"), tooltip=["time:T", "side:N", "entry:Q", "exit:Q", "win:N"])
        )
        layers.extend([call_points, put_points])

    chart = alt.layer(*layers).resolve_scale(y="independent").properties(height=500)
    st.altair_chart(chart, use_container_width=True)


def main():
    st.set_page_config(page_title="EMA/ATR Breakout Backtester (Ticks)", layout="wide")
    st.title("EMA/ATR Breakout Backtester (Ticks)")
    st.caption("Last price is Bid price. Upload a CSV of tick data.")

    st.sidebar.header("Parameters")
    fast_period = st.sidebar.number_input("EMA Fast", min_value=2, max_value=200, value=5)
    slow_period = st.sidebar.number_input("EMA Slow", min_value=3, max_value=400, value=20)
    atr_period = st.sidebar.number_input("ATR Period", min_value=2, max_value=400, value=14)
    atr_min = st.sidebar.number_input("Min ATR (absolute)", min_value=0.0, value=0.03, step=0.01, format="%.5f")
    break_factor = st.sidebar.number_input("Breakout buffer × ATR", min_value=0.0, value=0.2, step=0.05)
    eps = st.sidebar.number_input("Extra breakout buffer (abs)", min_value=0.0, value=0.0, step=0.01, format="%.5f")
    cooldown = st.sidebar.number_input("Cooldown (ticks)", min_value=0, max_value=10_000, value=30)
    duration = st.sidebar.number_input("Contract duration (ticks)", min_value=1, max_value=10_000, value=10)
    lookback_len = st.sidebar.number_input("High/Low lookback (ticks, prev)", min_value=5, max_value=500, value=21)

    uploaded = st.file_uploader("Upload ticks CSV", type=["csv"])  # prompts for file
    if not uploaded:
        st.info("Upload a CSV with at least time and bid/price columns.")
        st.write("Suggested columns: timestamp/time/datetime/epoch and bid/price/last")
        return

    # Read CSV
    try:
        df = pd.read_csv(uploaded)
    except Exception:
        # Try semi-colon
        uploaded.seek(0)
        df = pd.read_csv(uploaded, sep=";")

    if df.empty:
        st.error("The uploaded CSV is empty.")
        return

    # Column selection
    price_guess = guess_price_column(list(df.columns)) or st.sidebar.selectbox("Select price (bid)", list(df.columns))
    time_guess = guess_time_column(list(df.columns)) or st.sidebar.selectbox("Select time column", list(df.columns))

    price_col = st.sidebar.selectbox("Price (Bid)", list(df.columns), index=list(df.columns).index(price_guess) if price_guess in df.columns else 0)
    time_col = st.sidebar.selectbox("Time column", list(df.columns), index=list(df.columns).index(time_guess) if time_guess in df.columns else 0)

    # Ensure datetime time column
    df[time_col] = ensure_datetime(df[time_col])

    # Run backtest
    with st.spinner("Running backtest..."):
        trades, metrics = backtest(
            df,
            price_col=price_col,
            time_col=time_col,
            fast_period=fast_period,
            slow_period=slow_period,
            atr_period=atr_period,
            atr_min=atr_min,
            break_factor=break_factor,
            eps=eps,
            cooldown=cooldown,
            duration=duration,
            lookback_len=lookback_len,
        )

    # Recompute indicators for plotting (after sort & clean)
    df_clean = df.dropna(subset=[price_col]).sort_values(time_col).reset_index(drop=True).copy()
    ema_fast, ema_slow, atr = compute_indicators(df_clean[price_col], fast_period, slow_period, atr_period)
    df_clean["ema_fast"], df_clean["ema_slow"] = ema_fast, ema_slow

    # Display metrics
    st.subheader("Results")
    c1, c2, c3 = st.columns(3)
    c1.metric("Trades", f"{metrics['trades']}")
    c2.metric("Wins", f"{metrics['wins']}")
    c3.metric("Win rate", f"{metrics['winrate_pct']:.2f}%")

    if not trades.empty:
        st.dataframe(trades, use_container_width=True)
    else:
        st.info("No trades were generated with the current parameters.")

    st.subheader("Chart")
    render_chart(df_clean, price_col, time_col, trades)


if __name__ == "__main__":
    main()


"""
投資マニュアル準拠の銘柄分析（claude.ai Analysis tool 用・self-contained 版）

PC版（~/.claude/skills/investment-signals/scripts/）からポート、subprocess依存を除去し
volume_profile / earnings_check の機能を内包。

Usage（claude.ai Analysis tool 上）:
    !pip install yfinance pandas matplotlib   # 初回のみ
    from analyze import full_analysis
    result = full_analysis(["7011", "9984"], with_chart=True)
    print(result)

または直接実行:
    python analyze.py 7011 9984 --with-chart
"""
import argparse
import io
import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# 出力先（claude.ai では /tmp など適当な場所に書く）
DEFAULT_VP_OUT = Path("./charts")

MA_PERIODS = [5, 10, 20, 50, 100]


# ============================================================
# データ取得
# ============================================================

def fetch(ticker_jp: str, interval: str, period: str) -> pd.DataFrame:
    """日本株はサフィックス .T を自動付与。指数（^N225 等）はそのまま。"""
    if ticker_jp.startswith("^") or "." in ticker_jp:
        symbol = ticker_jp
    else:
        symbol = f"{ticker_jp}.T"
    df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"データ取得失敗: {symbol} ({interval})")
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

    # 日足のみ: 最新バーが NaN なら kabutan から補完（yfinance 遅延対策）
    if interval == "1d" and not ticker_jp.startswith("^") and "." not in ticker_jp:
        df = _inject_kabutan_close_if_missing(df, ticker_jp)
    return df


# ============================================================
# kabutan 補完（yfinance 遅延対策）
# ============================================================

def fetch_kabutan_latest_ohlc(code: str) -> dict | None:
    """株探の個別銘柄ページから最新営業日の OHLCV を抽出。失敗時 None。"""
    import urllib.request
    import re as _re
    url = f"https://kabutan.jp/stock/?code={code}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0 Safari/537.36"
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    text = _re.sub(r"<script[^>]*>.*?</script>", "", body, flags=_re.DOTALL)
    text = _re.sub(r"<style[^>]*>.*?</style>", "", text, flags=_re.DOTALL)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = _re.sub(r"\s+", " ", text)

    m = _re.search(
        r"(\d{2})月(\d{2})日\s*始値\s*([\d,]+)\s*\([^)]*\)\s*"
        r"高値\s*([\d,]+)\s*\([^)]*\)\s*"
        r"安値\s*([\d,]+)\s*\([^)]*\)\s*"
        r"終値\s*([\d,]+)\s*\([^)]*\)\s*"
        r"出来高\s*([\d,]+)",
        text,
    )
    if not m:
        return None

    def num(s: str) -> float:
        return float(s.replace(",", ""))

    return {
        "month": int(m.group(1)),
        "day": int(m.group(2)),
        "open": num(m.group(3)),
        "high": num(m.group(4)),
        "low": num(m.group(5)),
        "close": num(m.group(6)),
        "volume": int(m.group(7).replace(",", "")),
    }


def _inject_kabutan_close_if_missing(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """最新バー（または直近営業日）が NaN/欠損なら kabutan から OHLCV を取得して上書き/追加"""
    if df.empty:
        return df

    last_close = df["Close"].iloc[-1]
    last_idx = df.index[-1]
    last_date = last_idx.date() if hasattr(last_idx, "date") else None
    today = date.today()

    if pd.notna(last_close):
        if last_date and (today - last_date).days <= 1:
            return df

    kabu = fetch_kabutan_latest_ohlc(ticker)
    if not kabu:
        return df

    now = datetime.now()
    year = now.year
    k_date = date(year, kabu["month"], kabu["day"])
    if k_date > today:
        k_date = date(year - 1, kabu["month"], kabu["day"])

    if last_date and k_date < last_date:
        return df

    new_idx = pd.Timestamp(k_date).tz_localize(df.index.tz) if df.index.tz else pd.Timestamp(k_date)
    new_row = pd.DataFrame(
        {
            "Open": [kabu["open"]],
            "High": [kabu["high"]],
            "Low": [kabu["low"]],
            "Close": [kabu["close"]],
            "Volume": [kabu["volume"]],
        },
        index=[new_idx],
    )

    if new_idx in df.index:
        df.loc[new_idx, ["Open", "High", "Low", "Close", "Volume"]] = [
            kabu["open"], kabu["high"], kabu["low"], kabu["close"], kabu["volume"]
        ]
    else:
        df = pd.concat([df, new_row])

    return df


# ============================================================
# 指標計算
# ============================================================

def add_ma(df: pd.DataFrame) -> pd.DataFrame:
    for p in MA_PERIODS:
        df[f"MA{p}"] = df["Close"].rolling(p).mean()
    return df


def slope(series: pd.Series, lookback: int = 5) -> float:
    s = series.dropna().iloc[-lookback:]
    if len(s) < 2:
        return 0.0
    return float((s.iloc[-1] - s.iloc[0]) / s.iloc[0] / (len(s) - 1) * 100)


def direction(sl: float, eps: float = 0.05) -> str:
    if sl > eps:
        return "up"
    if sl < -eps:
        return "down"
    return "flat"


def count_trend_period(df: pd.DataFrame) -> int:
    df = df.copy()
    df["MA5_diff"] = df["MA5"].diff()
    df["up"] = df["MA5_diff"] > 0
    count = 0
    for up in reversed(df["up"].tail(40).tolist()):
        if up:
            count += 1
        else:
            break
    return count


# ============================================================
# 地合い・時間軸評価
# ============================================================

def evaluate_market(n225_daily: pd.DataFrame) -> dict:
    n225_daily = add_ma(n225_daily)
    s5 = slope(n225_daily["MA5"])
    s10 = slope(n225_daily["MA10"])
    s20 = slope(n225_daily["MA20"])
    score = sum(
        1 if direction(s) == "up" else (-1 if direction(s) == "down" else 0)
        for s in [s5, s10, s20]
    )
    if score == 3:
        label = "イージー（勝率高）"
    elif score >= 1:
        label = "イージー寄りノーマル"
    elif score == 0:
        label = "ノーマル"
    elif score >= -2:
        label = "ノーマル寄りハード"
    else:
        label = "ハード（勝率低）"

    recent = n225_daily["Close"].tail(120)
    peak = recent.max()
    last = recent.iloc[-1]
    drawdown = (last - peak) / peak * 100
    if drawdown <= -30:
        crash = "リーマン級暴落"
    elif drawdown <= -20:
        crash = "暴落"
    elif drawdown <= -10:
        crash = "下落"
    else:
        crash = "通常"

    return {
        "label": label,
        "score": score,
        "slope_5MA_pct_per_bar": round(s5, 3),
        "slope_10MA_pct_per_bar": round(s10, 3),
        "slope_20MA_pct_per_bar": round(s20, 3),
        "drawdown_from_120d_high_pct": round(drawdown, 2),
        "crash_level": crash,
    }


def evaluate_timeframe(df: pd.DataFrame, label: str) -> dict:
    df = add_ma(df)
    slopes = {p: slope(df[f"MA{p}"]) for p in MA_PERIODS}
    dirs = {p: direction(slopes[p]) for p in MA_PERIODS}
    last_close = float(df["Close"].iloc[-1])
    last_open = float(df["Open"].iloc[-1])
    is_bull = last_close > last_open
    over_ma5 = bool(last_close > df["MA5"].iloc[-1]) if pd.notna(df["MA5"].iloc[-1]) else False
    over_ma10 = bool(last_close > df["MA10"].iloc[-1]) if pd.notna(df["MA10"].iloc[-1]) else False
    over_ma20 = bool(last_close > df["MA20"].iloc[-1]) if pd.notna(df["MA20"].iloc[-1]) else False
    last_bar = df.index[-1]
    return {
        "timeframe": label,
        "last_bar_datetime": last_bar.isoformat() if hasattr(last_bar, "isoformat") else str(last_bar),
        "last_close": round(last_close, 2),
        "candle": "陽線" if is_bull else "陰線",
        "ma_slope_pct_per_bar": {f"MA{p}": round(v, 3) for p, v in slopes.items()},
        "ma_direction": {f"MA{p}": dirs[p] for p in MA_PERIODS},
        "above_ma": {"MA5": over_ma5, "MA10": over_ma10, "MA20": over_ma20},
    }


# ============================================================
# 買い・売り判定
# ============================================================

def evaluate_buy_sell(daily, weekly, monthly):
    d = add_ma(daily.copy())
    w = add_ma(weekly.copy())
    m = add_ma(monthly.copy())

    # NaN を含むバーを末尾から除外（場中の暫定バー対策）
    d = d.dropna(subset=["Close", "MA5", "MA10", "MA20"]).copy()
    if len(d) < 10:
        return {
            "trend_period_bars": 0, "buy_score": 0,
            "buy_score_max_theoretical": 14, "buy_score_min_theoretical": -7,
            "buy_reasons": [], "penalty_reasons": [],
            "hard_avoid_flags": ["データ不足"], "soft_warning_flags": [],
            "avoid_flags": ["データ不足"], "sell_state": "判定不能",
            "sell_reasons": [], "surge_pct_last5bars": 0,
            "stop_loss_candidates": {}, "last_close": 0, "diagnostics": {},
        }

    trend_period = count_trend_period(d)
    last = d.iloc[-1]
    prev = d.iloc[-2]
    is_bull_today = last["Close"] > last["Open"]
    crossed_up_ma5_today = prev["Close"] < prev["MA5"] and last["Close"] > last["MA5"] and is_bull_today
    crossed_down_ma5_today = prev["Close"] > prev["MA5"] and last["Close"] < last["MA5"] and not is_bull_today
    near_ma5 = abs(last["Close"] - last["MA5"]) / last["Close"] < 0.015
    bear_two_in_row = (
        d.iloc[-1]["Close"] < d.iloc[-1]["Open"]
        and d.iloc[-2]["Close"] < d.iloc[-2]["Open"]
    )

    # ============================================================
    # 直近5日スコープの状態分析（v2）
    # ============================================================
    recent_5 = d.tail(5)
    days_below_ma5_in_last_5 = int((recent_5["Close"] < recent_5["MA5"]).sum())
    bears_in_last_5 = int((recent_5["Close"] < recent_5["Open"]).sum())
    above_ma5_now = bool(last["Close"] > last["MA5"])
    below_ma5_now = bool(last["Close"] < last["MA5"])

    ma5_bearish_breakdown_recent = False
    breakdown_days_ago = None
    n = len(d)
    lookback = min(5, n - 1)
    for ago in range(0, lookback):
        if n - (ago + 2) < 0:
            break
        curr_row = d.iloc[-(ago + 1)]
        prev_row = d.iloc[-(ago + 2)]
        crossed_down = (
            prev_row["Close"] > prev_row["MA5"]
            and curr_row["Close"] < curr_row["MA5"]
            and curr_row["Close"] < curr_row["Open"]
        )
        if crossed_down:
            ma5_bearish_breakdown_recent = True
            breakdown_days_ago = ago
            break

    surge_pct = float((d["Close"].iloc[-1] / d["Close"].iloc[-6] - 1) * 100) if len(d) >= 6 else 0
    is_surge = surge_pct >= 10

    d_s5, d_s10, d_s20 = slope(d["MA5"]), slope(d["MA10"]), slope(d["MA20"])
    w_s5, w_s10, w_s20 = slope(w["MA5"]), slope(w["MA10"]), slope(w["MA20"])
    m_s5, m_s10, m_s20 = slope(m["MA5"]), slope(m["MA10"]), slope(m["MA20"])

    buy_score = 0
    buy_breakdown = []
    if crossed_up_ma5_today and trend_period <= 8:
        buy_score += 3
        buy_breakdown.append((3, f"日足5MAを陽線突破 + トレンド周期{trend_period}本（≤8本）"))
    elif crossed_up_ma5_today:
        buy_score += 1
        buy_breakdown.append((1, f"日足5MA陽線突破（ただしトレンド周期{trend_period}本）"))
    if near_ma5 and trend_period <= 8 and direction(d_s5) == "up" and above_ma5_now:
        buy_score += 2
        buy_breakdown.append((2, f"5MAに近接 + トレンド周期{trend_period}本（押し目候補・終値5MA上）"))
    # 日足5MA右肩上がり：終値が5MA上の場合のみ加点
    if direction(d_s5) == "up" and above_ma5_now:
        buy_score += 1
        buy_breakdown.append((1, "日足5MA右肩上がり + 終値5MA上"))
    if direction(d_s20) == "up":
        buy_score += 1
        buy_breakdown.append((1, "日足20MA右肩上がり"))
    if direction(w_s5) == "up":
        buy_score += 2
        buy_breakdown.append((2, "週足5MA右肩上がり"))
    if direction(w_s10) in ("up", "flat"):
        buy_score += 1
        buy_breakdown.append((1, "週足10MA右肩上がり or 横ばい"))
    if direction(w_s20) in ("up", "flat"):
        buy_score += 1
        buy_breakdown.append((1, "週足20MA右肩上がり or 横ばい"))
    if direction(m_s5) == "up":
        buy_score += 1
        buy_breakdown.append((1, "月足5MA右肩上がり"))
    if direction(m_s10) == "up":
        buy_score += 1
        buy_breakdown.append((1, "月足10MA右肩上がり"))
    if direction(m_s20) == "up":
        buy_score += 1
        buy_breakdown.append((1, "月足20MA右肩上がり"))

    hard_avoid_flags = []
    soft_warning_flags = []
    penalty_breakdown = []
    if trend_period >= 10 and near_ma5:
        buy_score -= 3
        penalty_breakdown.append((-3, f"トレンド周期{trend_period}本（≥10本）で5MA近接 = 伸び切り懸念"))
        hard_avoid_flags.append(f"トレンド周期{trend_period}本（≥10本）で5MA近接 = 伸び切り懸念")
    if direction(d_s20) == "down":
        buy_score -= 2
        penalty_breakdown.append((-2, "日足20MA下向き = 負けパターン"))
        hard_avoid_flags.append("日足20MA下向き = 負けパターン1（マニュアル §8）")
    if direction(w_s20) == "down":
        buy_score -= 2
        penalty_breakdown.append((-2, "週足20MA下向き = 大型相場悪い、勝負見送り推奨"))
        hard_avoid_flags.append("週足20MA下向き = 負けパターン3（マニュアル §8）")
    if is_surge and not near_ma5:
        soft_warning_flags.append(f"直近5本で+{surge_pct:.1f}% 急騰中 = ジリジリ上昇中の飛びつき注意・押し目待ち推奨（マニュアル §8 負けパターン2）")
    # v2: 5MA は上向きだが終値が5MA下に滞在中 = 短期トレンド失速
    if direction(d_s5) == "up" and below_ma5_now and days_below_ma5_in_last_5 >= 2:
        buy_score -= 2
        penalty_breakdown.append((-2, f"日足5MA上向きだが終値が5MA下に直近5日中{days_below_ma5_in_last_5}日 = 短期トレンド失速"))
        hard_avoid_flags.append(f"日足5MAは上向きだが終値が5MA下に滞在（直近5日中{days_below_ma5_in_last_5}日） = 短期トレンド失速・転換中の可能性")

    # 売り判定（マニュアル §9 準拠・v2）
    sell_state = "ホールド"
    sell_reasons = []
    # v2: 直近5日内に5MA陰線下抜け + 現在も5MA下 → 3次
    if ma5_bearish_breakdown_recent and below_ma5_now:
        sell_state = "3次：売却シグナル"
        when = "本日" if breakdown_days_ago == 0 else f"{breakdown_days_ago}日前"
        sell_reasons.append(
            f"{when}に5MA陰線下抜け、現在も5MA下"
            f"（直近5日: {days_below_ma5_in_last_5}日5MA下/陰線{bears_in_last_5}本）"
            f"（マニュアル §9 3次）"
        )
    # v2: 価格が5MA下に複数日 + 陰線多 → 3次相当
    elif below_ma5_now and days_below_ma5_in_last_5 >= 3 and bears_in_last_5 >= 3:
        sell_state = "3次：売却シグナル"
        sell_reasons.append(
            f"終値が5MA下に直近5日中{days_below_ma5_in_last_5}日、陰線{bears_in_last_5}本 = トレンド転換確定"
        )
    elif crossed_down_ma5_today and direction(d_s5) in ("down", "flat"):
        sell_state = "3次：売却シグナル"
        sell_reasons.append("本日5MA陰線下抜け + 5MA下落/横ばい")
    elif bear_two_in_row and direction(d_s5) == "flat":
        sell_state = "2次：警戒（売却候補）"
        sell_reasons.append("陰線2本連続 + 5MA横ばい")
    # v2: 価格が5MA下に複数日（陰線本数の条件は緩い）
    elif below_ma5_now and days_below_ma5_in_last_5 >= 3:
        sell_state = "2次：警戒（売却候補）"
        sell_reasons.append(
            f"終値が5MA下に直近5日中{days_below_ma5_in_last_5}日 = トレンド転換中の可能性"
        )
    elif trend_period >= 10:
        sell_state = "1次：利確検討"
        sell_reasons.append(f"トレンド周期{trend_period}本（≥10本）")
    elif trend_period >= 8 and is_surge:
        sell_state = "1次：早期利確（急騰銘柄）"
        sell_reasons.append(f"トレンド周期{trend_period}本 + +{surge_pct:.1f}%急騰")

    last_close = float(d["Close"].iloc[-1])
    stop_5pct = round(last_close * 0.95, 2)
    recent_low_20d = float(d["Low"].tail(20).min())
    ma20_now = float(d["MA20"].iloc[-1]) if pd.notna(d["MA20"].iloc[-1]) else None

    buy_reasons = [f"{'+' if p > 0 else ''}{p}点: {r}" for p, r in buy_breakdown]
    penalty_reasons = [f"{p}点: {r}" for p, r in penalty_breakdown]

    return {
        "trend_period_bars": trend_period,
        "buy_score": buy_score,
        "buy_score_max_theoretical": 14,
        "buy_score_min_theoretical": -7,
        "buy_reasons": buy_reasons,
        "penalty_reasons": penalty_reasons,
        "hard_avoid_flags": hard_avoid_flags,
        "soft_warning_flags": soft_warning_flags,
        "avoid_flags": hard_avoid_flags + soft_warning_flags,
        "sell_state": sell_state,
        "sell_reasons": sell_reasons,
        "surge_pct_last5bars": round(surge_pct, 2),
        "stop_loss_candidates": {
            "現在値-5%": stop_5pct,
            "直近20日安値": round(recent_low_20d, 2),
            "日足20MA": round(ma20_now, 2) if ma20_now else None,
        },
        "last_close": round(last_close, 2),
        "diagnostics": {
            "below_ma5_now": below_ma5_now,
            "above_ma5_now": above_ma5_now,
            "days_below_ma5_in_last_5": days_below_ma5_in_last_5,
            "bears_in_last_5": bears_in_last_5,
            "ma5_bearish_breakdown_recent": ma5_bearish_breakdown_recent,
            "breakdown_days_ago": breakdown_days_ago,
        },
    }


# ============================================================
# 出来高ゾーン
# ============================================================

def summarize_volume_zones(daily: pd.DataFrame, bins: int = 30) -> dict:
    daily = daily.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    if len(daily) < 5:
        return {"resistance_zones_above": [], "support_zones_below": [], "interpretation": "データ不足"}
    typical = (daily["High"] + daily["Low"] + daily["Close"]) / 3
    lo, hi = float(daily["Low"].min()), float(daily["High"].max())
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    vol = np.zeros(bins)
    for price, v in zip(typical, daily["Volume"]):
        idx = min(int((price - lo) / (hi - lo) * bins), bins - 1)
        vol[idx] += v
    threshold = float(np.quantile(vol, 0.7))
    last_close = float(daily["Close"].iloc[-1])
    zones_above = [round(c, 1) for c, v in zip(centers, vol) if v >= threshold and c > last_close]
    zones_below = [round(c, 1) for c, v in zip(centers, vol) if v >= threshold and c <= last_close]
    if len(zones_above) >= 3 and len(zones_below) <= 1:
        interp = "上値ゾーン濃厚・下値サポート薄い → 上値が重い"
    elif len(zones_below) >= 3 and len(zones_above) <= 1:
        interp = "下値サポート厚い・上値抵抗薄い → 上昇余地あり"
    elif len(zones_above) >= 3 and len(zones_below) >= 3:
        interp = "上下ともゾーンあり → レンジ展開想定"
    else:
        interp = "明確なゾーン無し → ボラ低 or トレンド初期"
    return {
        "resistance_zones_above": zones_above,
        "support_zones_below": zones_below,
        "interpretation": interp,
    }


def draw_volume_profile(ticker: str, daily: pd.DataFrame, bins: int = 30, out_path: Path | None = None):
    """matplotlib でローソク足 + 出来高プロファイル PNG 生成"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["Yu Gothic", "MS Gothic", "Hiragino Sans", "Meiryo", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False

    df = daily.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).tail(60).copy()
    for p in (5, 10, 20, 50):
        df[f"MA{p}"] = df["Close"].rolling(p).mean()

    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    lo, hi = float(df["Low"].min()), float(df["High"].max())
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    vol = np.zeros(bins)
    for price, v in zip(typical, df["Volume"]):
        idx = min(int((price - lo) / (hi - lo) * bins), bins - 1)
        vol[idx] += v
    threshold = float(np.quantile(vol, 0.7))
    last_close = float(df["Close"].iloc[-1])

    fig, (ax_p, ax_v) = plt.subplots(
        1, 2, figsize=(14, 7), gridspec_kw={"width_ratios": [4, 1]}, sharey=True
    )

    for i, (idx, row) in enumerate(df.iterrows()):
        col = "#d62728" if row["Close"] >= row["Open"] else "#1f77b4"
        ax_p.vlines(i, row["Low"], row["High"], color=col, linewidth=0.6)
        ax_p.add_patch(plt.Rectangle(
            (i - 0.3, min(row["Open"], row["Close"])),
            0.6, abs(row["Close"] - row["Open"]) or 0.01,
            color=col, alpha=0.85,
        ))
    for p, c in [(5, "#ff6b00"), (10, "#1f77b4"), (20, "#2ca02c"), (50, "#9467bd")]:
        ax_p.plot(range(len(df)), df[f"MA{p}"], color=c, linewidth=1.0, label=f"MA{p}")

    for c, v in zip(centers, vol):
        if v >= threshold:
            color = "#d62728" if c > last_close else "#2ca02c"
            ax_p.axhline(c, color=color, alpha=0.15, linewidth=8, zorder=0)

    ax_p.axhline(last_close, color="black", linestyle="--", linewidth=0.8, alpha=0.6)
    ax_p.text(len(df) - 1, last_close, f" {last_close:,.0f}", va="center", fontsize=9)
    n = len(df)
    step = max(1, n // 8)
    ax_p.set_xticks(list(range(0, n, step)))
    ax_p.set_xticklabels([df.index[i].strftime("%m/%d") for i in range(0, n, step)], fontsize=8)
    ax_p.set_xlim(-0.5, n - 0.5)
    ax_p.grid(axis="y", alpha=0.3)
    ax_p.set_title(f"{ticker} 日足（{df.index[0].strftime('%Y-%m-%d')} 〜 {df.index[-1].strftime('%Y-%m-%d')}）")
    ax_p.legend(loc="upper left", fontsize=8)
    ax_p.set_ylabel("価格 (JPY)")

    for c, v in zip(centers, vol):
        col = "#d62728" if c > last_close else "#2ca02c"
        alpha = 0.9 if v >= threshold else 0.35
        ax_v.barh(c, v, height=(centers[1] - centers[0]) * 0.9, color=col, alpha=alpha)
    ax_v.set_title("出来高プロファイル")
    ax_v.set_xlabel("出来高")
    ax_v.grid(axis="x", alpha=0.3)
    ax_v.tick_params(axis="y", labelleft=False)

    if out_path is None:
        out_path = DEFAULT_VP_OUT / f"{ticker}_{datetime.now().strftime('%Y-%m-%d')}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


# ============================================================
# 決算日チェック
# ============================================================

def check_earnings(ticker: str, warn_days: int = 7) -> dict:
    symbol = ticker if "." in ticker else f"{ticker}.T"
    t = yf.Ticker(symbol)
    next_date = None
    source = None
    try:
        cal = t.calendar
        if cal and "Earnings Date" in cal:
            future = [d for d in cal["Earnings Date"] if isinstance(d, date) and d >= date.today()]
            if future:
                next_date = min(future)
                source = "calendar"
    except Exception:
        pass
    if next_date is None:
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                today = datetime.now(ed.index.tz) if ed.index.tz else datetime.now()
                future_idx = ed.index[ed.index >= today]
                if len(future_idx) > 0:
                    next_date = future_idx.min().date()
                    source = "earnings_dates"
        except Exception:
            pass
    if next_date is None:
        return {"next_earnings_date": None, "days_until": None, "advice": "決算日不明（手動確認推奨）"}
    days = (next_date - date.today()).days
    if days <= 1:
        advice = "🚨 決算直前。保有売却 / 新規買い禁止"
    elif days <= warn_days:
        advice = f"⚠️ あと{days}日で決算。手仕舞いタイミング検討"
    else:
        advice = f"あと{days}日。通常運用可"
    return {
        "next_earnings_date": next_date.isoformat(),
        "days_until": days,
        "source": source,
        "advice": advice,
    }


# ============================================================
# ファンダメンタル評価（マニュアル §4 業績好調基準）
# ============================================================

def evaluate_fundamentals(ticker: str) -> dict:
    """yfinance info から PER/PBR/ROE/成長率を取得し、ファンダスコアを算出"""
    symbol = ticker if "." in ticker else f"{ticker}.T"
    t = yf.Ticker(symbol)
    try:
        info = t.info
    except Exception:
        info = {}

    market_cap = info.get("marketCap") or 0
    per_trailing = info.get("trailingPE")
    per_forward = info.get("forwardPE")
    pbr = info.get("priceToBook")
    div_yield = info.get("dividendYield")
    roe = info.get("returnOnEquity")
    op_margin = info.get("operatingMargins")
    rev_growth = info.get("revenueGrowth")
    eps_growth = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")

    score = 0
    reasons = []
    penalties = []

    per = per_forward if per_forward and per_forward > 0 else per_trailing
    if per and per > 0:
        if per < 10:
            score += 1
            reasons.append(f"+1点: PER {per:.1f}倍 = 割安")
        elif per < 25:
            score += 1
            reasons.append(f"+1点: PER {per:.1f}倍 = 適正範囲（マニュアル想定）")
        elif per <= 35:
            reasons.append(f"+0点: PER {per:.1f}倍 = やや高めだが許容範囲")
        elif per <= 50:
            score -= 1
            penalties.append(f"-1点: PER {per:.1f}倍 = 割高")
        else:
            score -= 2
            penalties.append(f"-2点: PER {per:.1f}倍 = 過大評価")
    else:
        reasons.append("PER 不明 or 赤字のため算定不可")

    if eps_growth is not None:
        eps_pct = eps_growth * 100
        if eps_pct >= 50:
            score += 2
            reasons.append(f"+2点: EPS成長 +{eps_pct:.0f}% = 絶好調")
        elif eps_pct >= 20:
            score += 1
            reasons.append(f"+1点: EPS成長 +{eps_pct:.0f}% = 好調")
        elif eps_pct >= 0:
            reasons.append(f"+0点: EPS成長 +{eps_pct:.0f}% = 維持")
        else:
            score -= 1
            penalties.append(f"-1点: EPS成長 {eps_pct:.0f}% = 減益（マニュアル §4 削除要件相当）")
    else:
        reasons.append("EPS成長 不明")

    if rev_growth is not None:
        rev_pct = rev_growth * 100
        if rev_pct >= 10:
            score += 1
            reasons.append(f"+1点: 売上成長 +{rev_pct:.1f}% = 好調")
        elif rev_pct >= 0:
            pass
        else:
            score -= 1
            penalties.append(f"-1点: 売上成長 {rev_pct:.1f}% = 減収")

    if roe is not None:
        roe_pct = roe * 100
        if roe_pct >= 15:
            score += 1
            reasons.append(f"+1点: ROE {roe_pct:.1f}% = 高収益")
        elif roe_pct < 5:
            penalties.append(f"  ROE {roe_pct:.1f}% = 低収益（参考）")

    if op_margin is not None:
        om_pct = op_margin * 100
        if om_pct >= 20:
            score += 1
            reasons.append(f"+1点: 営業利益率 {om_pct:.1f}% = 高採算")
        elif om_pct >= 10:
            pass
        elif om_pct < 5:
            penalties.append(f"  営業利益率 {om_pct:.1f}% = 低採算（参考）")

    market_cap_oku = round(market_cap / 1e8) if market_cap else 0
    if market_cap >= 500e9:
        score += 1
        reasons.append(f"+1点: 時価総額 {market_cap_oku}億 = §4 大型株要件クリア")
    elif market_cap > 0:
        reasons.append(f"  時価総額 {market_cap_oku}億 = §4 基準未満（テクニカル信頼度低下）")

    if per and per > 0:
        if per < 15:
            valuation_label = "割安寄り"
        elif per <= 25:
            valuation_label = "適正"
        elif per <= 35:
            valuation_label = "やや割高"
        else:
            valuation_label = "割高"
    else:
        valuation_label = "判定不可（赤字 or データなし）"

    return {
        "market_cap_oku": market_cap_oku,
        "per_trailing": round(per_trailing, 2) if per_trailing else None,
        "per_forward": round(per_forward, 2) if per_forward else None,
        "pbr": round(pbr, 2) if pbr else None,
        "dividend_yield_pct": round(div_yield, 2) if div_yield else None,
        "roe_pct": round(roe * 100, 2) if roe is not None else None,
        "operating_margin_pct": round(op_margin * 100, 2) if op_margin is not None else None,
        "revenue_growth_pct": round(rev_growth * 100, 2) if rev_growth is not None else None,
        "eps_growth_pct": round(eps_growth * 100, 2) if eps_growth is not None else None,
        "fundamental_score": score,
        "fundamental_score_max_theoretical": 8,
        "fundamental_score_min_theoretical": -5,
        "fundamental_reasons": reasons,
        "fundamental_penalty_reasons": penalties,
        "valuation_label": valuation_label,
    }


# ============================================================
# verdict
# ============================================================

def verdict(buy_score, sell_state, market_label, hard_avoid_flags, soft_warning_flags=None,
            earnings_days=None, volume_interp="", fundamental_score=None, valuation_label=""):
    soft_warning_flags = soft_warning_flags or []
    if earnings_days is not None and earnings_days <= 1:
        return "🚨 決算直前 → 新規買いNG・保有売却推奨"
    if sell_state.startswith("3次"):
        return "🚨 売却シグナル（保有中なら売却検討）"
    if sell_state.startswith("2次"):
        return "⚠️ 売却候補（保有中ならポジション縮小検討）"
    if hard_avoid_flags:
        return "⛔ 買い見送り推奨（負けパターン該当）"
    if "ハード" in market_label and buy_score < 5:
        return "⛔ 地合いハード × スコア弱い → 買い見送り"

    fund_notes = []
    if fundamental_score is not None:
        if fundamental_score >= 4:
            fund_notes.append(f"ファンダ◎(F:{fundamental_score})")
        elif fundamental_score <= -2:
            fund_notes.append(f"⚠️ファンダ弱(F:{fundamental_score})")
    if valuation_label in ("割高",):
        fund_notes.append("PER割高")
    elif valuation_label == "判定不可（赤字 or データなし）":
        fund_notes.append("PER算定不可(赤字可能性)")

    if buy_score >= 7:
        base = "🟢 買いサイン強い"
        notes = []
        if "上値が重い" in volume_interp:
            notes.append("出来高ゾーン上値重い→慎重に")
        if soft_warning_flags:
            notes.append("急騰中→押し目待ち推奨")
        if earnings_days is not None and earnings_days <= 7:
            notes.append(f"あと{earnings_days}日で決算→短期勝負")
        notes.extend(fund_notes)
        if notes:
            base += "（" + " / ".join(notes) + "）"
        return base
    if buy_score >= 4:
        notes = []
        if soft_warning_flags:
            notes.append("押し目待ち推奨")
        notes.extend(fund_notes)
        suffix = ("（" + " / ".join(notes) + "）") if notes else ""
        return f"🟡 買いサイン中程度（条件確認しつつ検討）{suffix}"
    if sell_state.startswith("1次"):
        return "🟡 利確検討（保有中なら部分利確）"
    return "⚪ 様子見"


# ============================================================
# メイン
# ============================================================

def analyze_ticker(ticker: str, with_chart: bool = False) -> dict:
    fetched_at = datetime.now()
    daily = fetch(ticker, "1d", "1y")
    weekly = fetch(ticker, "1wk", "5y")
    monthly = fetch(ticker, "1mo", "max")

    tf_daily = evaluate_timeframe(daily, "日足")
    tf_weekly = evaluate_timeframe(weekly, "週足")
    tf_monthly = evaluate_timeframe(monthly, "月足")
    bs = evaluate_buy_sell(daily, weekly, monthly)
    vz = summarize_volume_zones(daily.tail(60))
    ec = check_earnings(ticker)
    fundamentals = evaluate_fundamentals(ticker)

    last_bar_ts = daily.index[-1]
    last_bar_date = last_bar_ts.date() if hasattr(last_bar_ts, "date") else None
    today = date.today()
    if last_bar_date == today:
        jst_hour = fetched_at.hour
        if 9 <= jst_hour < 15:
            status = "本日場中（最新バーは未確定・暫定値）"
        elif 15 <= jst_hour < 24:
            status = "本日場後（確定値・約15-20分遅延あり）"
        else:
            status = "本日深夜（前日確定値の可能性あり）"
        days_old = 0
    elif last_bar_date is not None:
        days_old = (today - last_bar_date).days
        if days_old == 1:
            status = "前営業日終値（確定値）"
        elif days_old <= 3:
            status = f"{days_old}日前の終値（週末/祝日の可能性）"
        else:
            status = f"⚠️ {days_old}日前のデータ（取得遅延の可能性、要確認）"
    else:
        days_old = None
        status = "不明"

    result = {
        "ticker": ticker,
        "data_freshness": {
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "last_daily_bar_date": last_bar_date.isoformat() if last_bar_date else None,
            "days_old": days_old,
            "status": status,
            "source": "yfinance (Yahoo Finance、15-20分遅延)",
        },
        "timeframes": {"daily": tf_daily, "weekly": tf_weekly, "monthly": tf_monthly},
        "signals": bs,
        "volume_zones": vz,
        "earnings": ec,
        "fundamentals": fundamentals,
    }
    if with_chart:
        try:
            result["chart_image"] = draw_volume_profile(ticker, daily)
        except Exception as e:
            result["chart_image_error"] = str(e)
    return result


def full_analysis(tickers: list[str], with_chart: bool = False, market_only: bool = False) -> dict:
    output = {
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        "market": {},
        "tickers": [],
    }
    n225 = fetch("^N225", "1d", "1y")
    output["market"]["nikkei225"] = evaluate_market(n225)
    spx = fetch("^GSPC", "1d", "1y")
    output["market"]["sp500"] = evaluate_market(spx)
    if not market_only:
        for t in tickers:
            try:
                r = analyze_ticker(t, with_chart=with_chart)
                r["verdict"] = verdict(
                    r["signals"]["buy_score"],
                    r["signals"]["sell_state"],
                    output["market"]["nikkei225"]["label"],
                    r["signals"]["hard_avoid_flags"],
                    soft_warning_flags=r["signals"]["soft_warning_flags"],
                    earnings_days=r["earnings"].get("days_until"),
                    volume_interp=r["volume_zones"]["interpretation"],
                    fundamental_score=r["fundamentals"].get("fundamental_score"),
                    valuation_label=r["fundamentals"].get("valuation_label", ""),
                )
                output["tickers"].append(r)
            except Exception as e:
                output["tickers"].append({"ticker": t, "error": str(e)})
    return output


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--market-only", action="store_true")
    ap.add_argument("--with-chart", action="store_true")
    args = ap.parse_args()
    result = full_analysis(args.tickers, with_chart=args.with_chart, market_only=args.market_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

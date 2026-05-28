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
    return df[["Open", "High", "Low", "Close", "Volume"]].copy()


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

    trend_period = count_trend_period(d)
    last = d.iloc[-1]
    prev = d.iloc[-2]
    is_bull_today = last["Close"] > last["Open"]
    crossed_up_ma5 = prev["Close"] < prev["MA5"] and last["Close"] > last["MA5"] and is_bull_today
    crossed_down_ma5 = prev["Close"] > prev["MA5"] and last["Close"] < last["MA5"] and not is_bull_today
    near_ma5 = abs(last["Close"] - last["MA5"]) / last["Close"] < 0.015
    bear_two_in_row = (
        d.iloc[-1]["Close"] < d.iloc[-1]["Open"]
        and d.iloc[-2]["Close"] < d.iloc[-2]["Open"]
    )
    surge_pct = float((d["Close"].iloc[-1] / d["Close"].iloc[-6] - 1) * 100) if len(d) >= 6 else 0
    is_surge = surge_pct >= 10

    d_s5, d_s10, d_s20 = slope(d["MA5"]), slope(d["MA10"]), slope(d["MA20"])
    w_s5, w_s10, w_s20 = slope(w["MA5"]), slope(w["MA10"]), slope(w["MA20"])
    m_s5, m_s10, m_s20 = slope(m["MA5"]), slope(m["MA10"]), slope(m["MA20"])

    buy_score = 0
    buy_breakdown = []
    if crossed_up_ma5 and trend_period <= 8:
        buy_score += 3
        buy_breakdown.append((3, f"日足5MAを陽線突破 + トレンド周期{trend_period}本（≤8本）"))
    elif crossed_up_ma5:
        buy_score += 1
        buy_breakdown.append((1, f"日足5MA陽線突破（ただしトレンド周期{trend_period}本）"))
    if near_ma5 and trend_period <= 8 and direction(d_s5) == "up":
        buy_score += 2
        buy_breakdown.append((2, f"5MAに近接 + トレンド周期{trend_period}本（押し目候補）"))
    if direction(d_s5) == "up":
        buy_score += 1
        buy_breakdown.append((1, "日足5MA右肩上がり"))
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

    sell_state = "ホールド"
    sell_reasons = []
    if crossed_down_ma5 and direction(d_s5) in ("down", "flat"):
        sell_state = "3次：売却シグナル"
        sell_reasons.append("5MA下落 + 陰線で5MA下抜け")
    elif bear_two_in_row and direction(d_s5) == "flat":
        sell_state = "2次：警戒（売却候補）"
        sell_reasons.append("陰線2本連続 + 5MA横ばい")
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
    }


# ============================================================
# 出来高ゾーン
# ============================================================

def summarize_volume_zones(daily: pd.DataFrame, bins: int = 30) -> dict:
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

    df = daily.tail(60).copy()
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
# verdict
# ============================================================

def verdict(buy_score, sell_state, market_label, hard_avoid_flags, soft_warning_flags=None, earnings_days=None, volume_interp=""):
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
    if buy_score >= 7:
        base = "🟢 買いサイン強い"
        notes = []
        if "上値が重い" in volume_interp:
            notes.append("出来高ゾーン上値重い→慎重に")
        if soft_warning_flags:
            notes.append("急騰中→押し目待ち推奨")
        if earnings_days is not None and earnings_days <= 7:
            notes.append(f"あと{earnings_days}日で決算→短期勝負")
        if notes:
            base += "（" + " / ".join(notes) + "）"
        return base
    if buy_score >= 4:
        suffix = "（押し目待ち推奨）" if soft_warning_flags else ""
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

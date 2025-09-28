import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from indicators import rsi, ema
from colorama import Fore, init
import pytz

# ========= Init =========
init(autoreset=True)
DHAKA_TZ = pytz.timezone("Asia/Dhaka")
print(Fore.YELLOW + "🚀 Starting DailyTrade3bot (News+1h Confirm+Confidence+PnL+Heartbeat)\n")

# ========= ENV =========
def env(key, default=None, cast=str):
    v = os.getenv(key, default)
    return cast(v) if v is not None else None

TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = env("TELEGRAM_CHAT_ID")

SYMBOLS = [s.strip() for s in env("BINANCE_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT").split(",") if s.strip()]
INTERVAL            = env("BINANCE_INTERVAL", "15m")   # signal TF
HTF_INTERVAL        = env("HTF_INTERVAL", "1h")        # higher TF confirm
CHECK_INTERVAL      = env("CHECK_INTERVAL_SECONDS", 60, int)

RSI_LOWER = env("RSI_LOWER", 30, int)
RSI_UPPER = env("RSI_UPPER", 70, int)
EMA_FAST  = env("EMA_FAST", 50, int)
EMA_SLOW  = env("EMA_SLOW", 200, int)

VOLUME_MULTIPLIER = float(env("VOLUME_MULTIPLIER", "1.5"))

CAPITAL_USDT  = float(env("CAPITAL_USDT", "1000"))   # PnL base
BASE_LEVERAGE = int(env("LEVERAGE", "2"))            # for the first PnL line

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
FF_JSON        = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"  # unofficial public JSON

# ========= State (in-memory) =========
# Active trades per symbol (one at a time, to keep simple)
active_trade = {
    # "ETHUSDT": {"side":"LONG","entry":..., "sl":..., "tp":..., "open_candle_close_time": pd.Timestamp}
}
stats = {"wins": 0, "losses": 0, "pnl_2x": 0.0, "pnl_10x": 0.0}

# News pause state
news_paused = False
news_resume_after = None
last_news_title = None

# ========= Helpers =========
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(Fore.RED + "Telegram not configured (TELEGRAM_* env missing).")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(Fore.RED + f"Telegram send error: {e}")

def fetch_klines(symbol: str, interval: str, limit: int = 400) -> pd.DataFrame:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(BINANCE_KLINES, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_asset_volume","number_of_trades",
            "taker_buy_base_asset_volume","taker_buy_quote_asset_volume","ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"]  = pd.to_datetime(df["open_time"],  unit="ms", utc=True).dt.tz_convert(DHAKA_TZ)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True).dt.tz_convert(DHAKA_TZ)
    return df

def trading_session(now: datetime) -> str:
    h = now.hour
    if 4 <= h < 12:   return "🌏 Asia Session"
    if 12 <= h < 20:  return "🇬🇧 London Session"
    return "🗽 New York Session"

def heartbeat():
    now = datetime.now(DHAKA_TZ).strftime("%Y-%m-%d %I:%M %p (Dhaka)")
    send_telegram(f"🤖 Bot is Running by Professor007...\n🕒 {now}")

# ========= News Filter (pause + resume alert) =========
def check_news_filter():
    global news_paused, news_resume_after, last_news_title
    try:
        r = requests.get(FF_JSON, timeout=10)
        r.raise_for_status()
        events = r.json()
    except Exception as e:
        print(Fore.RED + f"News fetch error: {e}")
        return

    now = datetime.now(DHAKA_TZ)

    # If we were paused, check resume time
    if news_paused and news_resume_after and now >= news_resume_after:
        news_paused = False
        send_telegram(
            "✅ News Over – Bot Resumed\n"
            "📊 Trading signals are now active again!\n"
            f"🕒 {now.strftime('%Y-%m-%d %I:%M %p (Dhaka)')}"
        )
        news_resume_after = None
        last_news_title = None
        return

    # Check for upcoming/current high-impact USD events within ±30 minutes
    for ev in events:
        if ev.get("impact") != "High" or ev.get("country") != "USD":
            continue
        ds, ts = ev.get("date"), ev.get("time")
        title  = ev.get("title") or "High Impact News"
        if not ds or not ts:
            continue
        try:
            ev_time = datetime.strptime(f"{ds} {ts}", "%Y-%m-%d %H:%M")
            ev_time = DHAKA_TZ.localize(ev_time)
        except Exception:
            continue

        delta_min = (ev_time - now).total_seconds() / 60.0
        if abs(delta_min) <= 30:
            # pause if not already paused for this event
            if not news_paused or last_news_title != title:
                news_paused = True
                last_news_title = title
                news_resume_after = ev_time + timedelta(minutes=30)  # resume 30m after event time
                send_telegram(
                    "🚨 *NEWS ALERT* 🚨\n"
                    f"Event: {title} (USD)\n"
                    f"🕒 Time: {ev_time.strftime('%I:%M %p (Dhaka)')}\n"
                    "❌ Trading paused during high-impact news!"
                )
            return

# ========= Confidence =========
def confidence_score(rsi_val, vol_spike, trend_up, trend_down, htf_align) -> str:
    score = 50
    if vol_spike: score += 10
    if trend_up and rsi_val > 50: score += 10
    if trend_down and rsi_val < 50: score += 10
    if rsi_val < 35 or rsi_val > 65: score += 8
    if htf_align: score += 15
    score = max(0, min(95, score))
    if score >= 75: return f"🟢 HIGH ({score}%) 🔥"
    if score >= 60: return f"🟡 MEDIUM ({score}%)"
    return f"🔴 LOW ({score}%) ⚠️"

# ========= Signal (15m) + 1h Confirm =========
def build_signal(symbol: str):
    ltf = fetch_klines(symbol, INTERVAL, limit=400)  # 15m
    ltf["rsi"] = rsi(ltf["close"], 14)
    ltf["ema_fast"] = ema(ltf["close"], EMA_FAST)
    ltf["ema_slow"] = ema(ltf["close"], EMA_SLOW)
    ltf["vol_sma"] = ltf["volume"].rolling(50).mean()
    ltf["vol_spike"] = ltf["volume"] > (VOLUME_MULTIPLIER * ltf["vol_sma"])

    last, prev = ltf.iloc[-1], ltf.iloc[-2]
    trend_up   = last["ema_fast"] > last["ema_slow"]
    trend_down = last["ema_fast"] < last["ema_slow"]

    # triggers
    candidates = []
    if trend_up and last["rsi"] > 40 and prev["rsi"] <= 40 and last["vol_spike"]:
        candidates.append("LONG")
    if trend_down and last["rsi"] < 60 and prev["rsi"] >= 60 and last["vol_spike"]:
        candidates.append("SHORT")
    if not candidates:
        return None

    side = "LONG" if trend_up else "SHORT" if trend_down else candidates[0]

    # HTF confirm (1h)
    htf = fetch_klines(symbol, HTF_INTERVAL, limit=200)
    htf["ema_fast"] = ema(htf["close"], EMA_FAST)
    htf["ema_slow"] = ema(htf["close"], EMA_SLOW)
    hlast = htf.iloc[-1]
    htf_up, htf_down = hlast["ema_fast"] > hlast["ema_slow"], hlast["ema_fast"] < hlast["ema_slow"]
    htf_align = (side == "LONG" and htf_up) or (side == "SHORT" and htf_down)
    if not htf_align:
        return None  # strict alignment required

    # ATR-like sizing (15m)
    ltf["tr"] = (ltf["high"] - ltf["low"]).rolling(14).mean()
    atr = float(ltf["tr"].iloc[-1]) if pd.notna(ltf["tr"].iloc[-1]) else float(ltf["high"].iloc[-1]-ltf["low"].iloc[-1])
    price = float(last["close"])
    if side == "LONG":
        entry = price
        sl    = round(price - 0.75 * atr, 4)
        tp    = round(price + 1.5 * atr, 4)
    else:
        entry = price
        sl    = round(price + 0.75 * atr, 4)
        tp    = round(price - 1.5 * atr, 4)

    conf    = confidence_score(float(last["rsi"]), bool(last["vol_spike"]), trend_up, trend_down, htf_align)
    session = trading_session(datetime.now(DHAKA_TZ))

    msg = (
        f"[{symbol} {INTERVAL} Signal] "
        f"{'🚀 LONG' if side=='LONG' else '🔻 SHORT'}\n"
        f"💹 Entry: {entry}\n"
        f"🛑 SL: {sl}\n"
        f"🎯 TP: {tp}\n\n"
        f"📊 Indicators (15m):\n"
        f"📉 RSI: {round(float(last['rsi']),2)}\n"
        f"📈 EMA{EMA_FAST}: {round(float(last['ema_fast']),2)} | EMA{EMA_SLOW}: {round(float(last['ema_slow']),2)}\n"
        f"🔥 VolSpike: {bool(last['vol_spike'])}\n\n"
        f"🧭 HTF Confirm (1h): ✅ Aligned\n"
        f"🕒 Time: {datetime.now(DHAKA_TZ).strftime('%Y-%m-%d %I:%M %p (Dhaka)')}\n"
        f"⚡ Confidence: {conf}\n"
        f"📍 Session: {session}"
    )

    # Save as active trade (one per symbol)
    active_trade[symbol] = {
        "side": side, "entry": entry, "sl": sl, "tp": tp,
        "open_candle_close_time": last["close_time"]
    }
    return msg

# ========= Result Evaluator (SL/TP hit order-aware) =========
def evaluate_results(symbol: str, df: pd.DataFrame):
    """
    After a signal, scan candles *after* the signal candle.
    Use each candle's high/low to detect which level (TP/SL) touched first.
    """
    if symbol not in active_trade:
        return

    tr = active_trade[symbol]
    side, entry, sl, tp, opened_at = tr["side"], tr["entry"], tr["sl"], tr["tp"], tr["open_candle_close_time"]

    # consider candles strictly after the signal's open_candle_close_time
    df_after = df[df["close_time"] > opened_at]
    if df_after.empty:
        return  # nothing to evaluate yet

    hit = None
    for _, row in df_after.iterrows():
        hi = float(row["high"]); lo = float(row["low"])
        if side == "LONG":
            # order matters within same candle: if price could reach both, assume worst-case first
            if lo <= sl: hit = ("LOSS", sl); break
            if hi >= tp: hit = ("WIN",  tp); break
        else:
            if hi >= sl: hit = ("LOSS", sl); break
            if lo <= tp: hit = ("WIN",  tp); break

    if not hit:
        return

    # Compute PnL on hit
    outcome, exit_price = hit
    move     = abs(exit_price - entry)
    pct_move = move / entry

    pnl_2x  = (pct_move * CAPITAL_USDT * BASE_LEVERAGE) * (1 if outcome=="WIN" else -1)
    pnl_10x = (pct_move * CAPITAL_USDT * 10)           * (1 if outcome=="WIN" else -1)

    if outcome == "WIN":
        stats["wins"] += 1
        tag = "✅ WIN 🟢"
    else:
        stats["losses"] += 1
        tag = "❌ LOSS 🔴"

    stats["pnl_2x"]  += pnl_2x
    stats["pnl_10x"] += pnl_10x
    winrate = stats["wins"] / max(1, stats["wins"] + stats["losses"]) * 100.0

    msg = (
        f"[{symbol} {INTERVAL} Result] {tag}\n"
        f"💹 Entry: {entry} | 🎯 Exit: {exit_price}\n"
        f"📈 Price Move: {round(move,4)} ({round(pct_move*100,2)}%)\n\n"
        f"💰 Capital: {CAPITAL_USDT} USDT\n"
        f"⚡ With {BASE_LEVERAGE}x → {round(pnl_2x,2)} USDT\n"
        f"🚀 With 10x → {round(pnl_10x,2)} USDT\n\n"
        f"📊 Overall Stats:\n"
        f"🏆 Wins: {stats['wins']}   ❌ Losses: {stats['losses']}\n"
        f"💵 Net PnL ({BASE_LEVERAGE}x): {round(stats['pnl_2x'],2)} USDT\n"
        f"💵 Net PnL (10x): {round(stats['pnl_10x'],2)} USDT\n"
        f"📈 Win Rate: {round(winrate,2)}%"
    )
    send_telegram(msg)

    # Clear active trade for this symbol
    del active_trade[symbol]

# ========= MAIN LOOP =========
def main():
    last_heartbeat = 0
    last_signal_time = {s: 0 for s in SYMBOLS}  # throttle per symbol

    while True:
        try:
            now = time.time()

            # Heartbeat every 30 minutes
            if now - last_heartbeat >= 1800:
                heartbeat()
                last_heartbeat = now

            # News condition
            check_news_filter()
            if news_paused:
                time.sleep(CHECK_INTERVAL)
                continue

            for sym in SYMBOLS:
                try:
                    # Always fetch fresh LTF for evaluation
                    df15 = fetch_klines(sym, "15m", 400)

                    # If there's an open trade, evaluate first
                    evaluate_results(sym, df15)

                    # Try new signal (throttle: not more than once per CHECK_INTERVAL)
                    if now - last_signal_time.get(sym, 0) >= CHECK_INTERVAL:
                        # 1h confirm inside build_signal()
                        sig_text = build_signal(sym)
                        if sig_text:
                            send_telegram(sig_text)
                            last_signal_time[sym] = now

                    time.sleep(0.25)
                except Exception as e:
                    print(Fore.RED + f"{sym} error: {e}")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(Fore.RED + f"Loop error: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

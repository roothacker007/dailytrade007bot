import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from indicators import rsi, ema
from colorama import Fore, init

# ============== INIT ==============
init(autoreset=True)

def env(key, default=None, cast=str):
    v = os.getenv(key, default)
    return cast(v) if v is not None else None

# ---------- ENV / CONFIG ----------
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = env("TELEGRAM_CHAT_ID")

SYMBOLS  = [s.strip() for s in env("BINANCE_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT").split(",") if s.strip()]
INTERVAL = env("BINANCE_INTERVAL", "15m")
CONFIRM_TF = env("CONFIRM_TF", "1h")
CHECK_INTERVAL = env("CHECK_INTERVAL_SECONDS", 60, int)

RSI_LOWER = env("RSI_LOWER", 30, int)
RSI_UPPER = env("RSI_UPPER", 70, int)
EMA_FAST  = env("EMA_FAST", 50, int)
EMA_SLOW  = env("EMA_SLOW", 200, int)
VOLUME_MULTIPLIER = float(env("VOLUME_MULTIPLIER", "1.5"))

CAPITAL = float(env("CAPITAL_USDT", "1000"))
LEVS_TO_SHOW = [2, 10]

HEARTBEAT_MINUTES = env("HEARTBEAT_MINUTES", 30, int)

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

try:
    import pytz
    DHAKA_TZ = pytz.timezone("Asia/Dhaka")
    def now_dhaka():
        return datetime.now(DHAKA_TZ)
except Exception:
    def now_dhaka():
        return datetime.utcnow() + timedelta(hours=6)

def session_name(dt):
    h = dt.hour
    if 12 <= h < 17: return "🇬🇧 London Session"
    elif 17 <= h or h < 2: return "🗽 New York Session"
    elif 4 <= h < 12: return "🇯🇵 Asia Session"
    else: return "🇦🇺 Sydney Session"

# ============== TELEGRAM ==============
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(Fore.RED + "Telegram not configured.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(Fore.RED + f"Telegram send error: {e}")

# ============== DATA FETCH ==============
def fetch_klines(symbol: str, interval: str, limit: int = 300):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_KLINES, params=params, timeout=12)
    resp.raise_for_status()
    raw = resp.json()
    cols = ["open_time","open","high","low","close","volume","close_time","ignore1","ignore2","ignore3","ignore4","ignore5"]
    df = pd.DataFrame(raw, columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"]  = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df

# ============== SIGNAL ENGINE ==============
open_trades = {}
totals = {"wins":0,"loss":0,"pnl_2x":0.0,"pnl_10x":0.0}

def make_signal(symbol, df15, df1h):
    df15["rsi"] = rsi(df15["close"], 14)
    df15["ema_fast"] = ema(df15["close"], EMA_FAST)
    df15["ema_slow"] = ema(df15["close"], EMA_SLOW)
    df15["vol_sma"] = df15["volume"].rolling(50).mean()
    df15["vol_spike"] = df15["volume"] > (VOLUME_MULTIPLIER * df15["vol_sma"])
    last, prev = df15.iloc[-1], df15.iloc[-2]

    trend_up, trend_down = last["ema_fast"] > last["ema_slow"], last["ema_fast"] < last["ema_slow"]
    signals = []
    if trend_up and last["rsi"] > 40 and prev["rsi"] <= 40 and last["vol_spike"]:
        signals.append("LONG")
    if trend_up and last["rsi"] < RSI_LOWER and last["vol_spike"]:
        signals.append("LONG")
    if trend_down and last["rsi"] < 60 and prev["rsi"] >= 60 and last["vol_spike"]:
        signals.append("SHORT")
    if trend_down and last["rsi"] > RSI_UPPER and last["vol_spike"]:
        signals.append("SHORT")
    if not signals: return None
    side = signals[0]

    price = float(last["close"])
    atr = float((df15["high"] - df15["low"]).rolling(14).mean().iloc[-1])
    entry, sl, tp = price, (price - 0.75*atr if side=="LONG" else price + 0.75*atr), (price + 1.5*atr if side=="LONG" else price - 1.5*atr)

    df1h["ema_fast"] = ema(df1h["close"], EMA_FAST)
    df1h["ema_slow"] = ema(df1h["close"], EMA_SLOW)
    df1h["rsi"] = rsi(df1h["close"], 14)
    h_last = df1h.iloc[-1]
    aligned = (side=="LONG" and h_last["ema_fast"]>h_last["ema_slow"] and h_last["rsi"]>45) or (side=="SHORT" and h_last["ema_fast"]<h_last["ema_slow"] and h_last["rsi"]<55)

    conf = "🟢 HIGH (78%)" if aligned else "🟡 MED (60%)"
    ts, sess = now_dhaka(), session_name(now_dhaka())
    msg = (
        f"[{symbol} {INTERVAL} Signal] {'🚀 LONG' if side=='LONG' else '🔻 SHORT'}\n"
        f"💹 Entry: {entry:.2f}\n🛑 SL: {sl:.2f}\n🎯 TP: {tp:.2f}\n\n"
        f"📊 Indicators (15m):\n📉 RSI: {round(last['rsi'],2)}\n"
        f"📈 EMA50: {round(last['ema_fast'],2)} | EMA200: {round(last['ema_slow'],2)}\n"
        f"🔥 VolSpike: {bool(last['vol_spike'])}\n\n"
        f"🧭 HTF Confirm (1h): {'✅ Aligned' if aligned else '⚠️ Not Aligned'}\n"
        f"🕒 Time: {ts.strftime('%Y-%m-%d %I:%M %p (Dhaka)')}\n"
        f"⚡ Confidence: {conf} 🔥\n"
        f"📍 Session: {sess}"
    )
    open_trades[symbol] = {"side":side,"entry":entry,"sl":sl,"tp":tp}
    return msg

def resolve_trade(symbol, price):
    if symbol not in open_trades: return None
    t = open_trades[symbol]
    side, entry, sl, tp = t["side"], t["entry"], t["sl"], t["tp"]
    win, exit_price = None, None
    if side=="LONG":
        if price>=tp: win, exit_price=True, tp
        elif price<=sl: win, exit_price=False, sl
    else:
        if price<=tp: win, exit_price=True, tp
        elif price>=sl: win, exit_price=False, sl
    if win is None: return None
    move_pct = (exit_price-entry)/entry * (1 if side=="LONG" else -1)
    pnl2, pnl10 = round(CAPITAL*2*move_pct,2), round(CAPITAL*10*move_pct,2)
    if win: totals["wins"]+=1
    else: totals["loss"]+=1
    totals["pnl_2x"]+=pnl2; totals["pnl_10x"]+=pnl10
    winrate = round(totals["wins"]/(totals["wins"]+totals["loss"])*100,1) if totals["wins"]+totals["loss"]>0 else 0
    msg = (
        f"[{symbol} {INTERVAL} Result] {'✅ WIN 🟢' if win else '❌ LOSS 🔴'}\n"
        f"💹 Entry: {entry:.2f} | 🎯 Exit: {exit_price:.2f}\n"
        f"📈 Price Move: {round(abs(exit_price-entry),2)} ({round(move_pct*100,2)}%)\n\n"
        f"💰 Capital: {CAPITAL} USDT\n"
        f"⚡ With 2x → {pnl2} USDT\n🚀 With 10x → {pnl10} USDT\n\n"
        f"📊 Overall Stats:\n🏆 Wins: {totals['wins']}   ❌ Losses: {totals['loss']}\n"
        f"💵 Net PnL (2x): {round(totals['pnl_2x'],2)} USDT\n"
        f"💵 Net PnL (10x): {round(totals['pnl_10x'],2)} USDT\n"
        f"📈 Win Rate: {winrate}%"
    )
    del open_trades[symbol]
    return msg

# ============== HEARTBEAT ==============
last_heartbeat = 0
def maybe_heartbeat():
    global last_heartbeat
    if time.time()-last_heartbeat >= HEARTBEAT_MINUTES*60:
        last_heartbeat=time.time()
        send_telegram(f"🤖 Bot is Running by Professor007 \n🕒 {now_dhaka().strftime('%Y-%m-%d %I:%M %p (Dhaka)')}")

# ============== MAIN LOOP ==============
def main():
    while True:
        maybe_heartbeat()
        for sym in SYMBOLS:
            try:
                df15, df1h = fetch_klines(sym, INTERVAL), fetch_klines(sym, CONFIRM_TF)
                msg = make_signal(sym, df15, df1h)
                if msg: send_telegram(msg)
                res = resolve_trade(sym, float(df15.iloc[-1]["close"]))
                if res: send_telegram(res)
            except Exception as e:
                print(Fore.RED+f"{sym} error: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__": main()

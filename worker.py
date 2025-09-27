import os
import time
import requests
import pandas as pd
from datetime import datetime
from indicators import rsi, ema
from colorama import Fore, init
import pytz  # for timezone

# Enable colored output
init(autoreset=True)

print(Fore.YELLOW + "🚀 Starting Currency Tracker by Professor007...\n")

# ================== ENV LOADER ==================
def env(key, default=None, cast=str):
    v = os.getenv(key, default)
    return cast(v) if v is not None else None

# ================== TELEGRAM CONFIG ==================
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")

SYMBOLS = [s.strip() for s in env(
    "BINANCE_SYMBOLS",
    "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT"
).split(",") if s.strip()]
INTERVAL = env("BINANCE_INTERVAL", "15m")
CHECK_INTERVAL = env("CHECK_INTERVAL_SECONDS", 60, int)

RSI_LOWER = env("RSI_LOWER", 30, int)
RSI_UPPER = env("RSI_UPPER", 70, int)
EMA_FAST = env("EMA_FAST", 50, int)
EMA_SLOW = env("EMA_SLOW", 200, int)
VOLUME_MULTIPLIER = float(env("VOLUME_MULTIPLIER", "1.5"))

# Heartbeat Interval (seconds) – default 900s = 15min
HEARTBEAT_INTERVAL = env("HEARTBEAT_INTERVAL", 900, int)

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

# Dhaka timezone
DHAKA_TZ = pytz.timezone("Asia/Dhaka")

# ================== DATA FETCH ==================
def fetch_klines(symbol: str, interval: str, limit: int = 400):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_KLINES, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    cols = [
        "open_time","open","high","low","close","volume",
        "close_time","quote_asset_volume","number_of_trades",
        "taker_buy_base_asset_volume","taker_buy_quote_asset_volume","ignore"
    ]
    df = pd.DataFrame(data, columns=cols)
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(DHAKA_TZ)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True).dt.tz_convert(DHAKA_TZ)

    # Debug print to confirm real market data
    last = df.iloc[-1]
    print(
        Fore.CYAN
        + f"[DEBUG] {symbol} last candle @ {last['close_time']:%Y-%m-%d %I:%M %p} "
          f"Open={last['open']} Close={last['close']} Vol={last['volume']}"
    )
    return df

# ================== TELEGRAM SENDER ==================
def send_telegram(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(Fore.RED + "Telegram not configured.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(Fore.RED + "Telegram send error:", e)

# ================== SIGNAL GENERATOR ==================
def make_signal(symbol: str, df: pd.DataFrame):
    df = df.copy()
    df["rsi"] = rsi(df["close"], 14)
    df["ema_fast"] = ema(df["close"], EMA_FAST)
    df["ema_slow"] = ema(df["close"], EMA_SLOW)
    df["vol_sma"] = df["volume"].rolling(50).mean()
    df["vol_spike"] = df["volume"] > (VOLUME_MULTIPLIER * df["vol_sma"])

    last = df.iloc[-1]
    prev = df.iloc[-2]

    trend_up = last["ema_fast"] > last["ema_slow"]
    trend_down = last["ema_fast"] < last["ema_slow"]

    signals = []
    if trend_up and last["rsi"] > 40 and prev["rsi"] <= 40 and last["vol_spike"]:
        signals.append(("LONG", "RSI cross up 40 w/ trend up & volume spike"))
    if trend_up and last["rsi"] < RSI_LOWER and last["vol_spike"]:
        signals.append(("LONG", "Oversold in uptrend + volume spike"))
    if trend_down and last["rsi"] < 60 and prev["rsi"] >= 60 and last["vol_spike"]:
        signals.append(("SHORT", "RSI cross down 60 w/ trend down & volume spike"))
    if trend_down and last["rsi"] > RSI_UPPER and last["vol_spike"]:
        signals.append(("SHORT", "Overbought in downtrend + volume spike"))

    if not signals:
        return None

    picked = None
    if trend_up:
        picked = next((s for s in signals if s[0] == "LONG"), signals[0])
    elif trend_down:
        picked = next((s for s in signals if s[0] == "SHORT"), signals[0])
    else:
        picked = signals[0]

    df["tr"] = (df["high"] - df["low"]).rolling(14).mean()
    atr = float(df["tr"].iloc[-1]) if pd.notna(df["tr"].iloc[-1]) else float(df["high"].iloc[-1]-df["low"].iloc[-1])
    price = float(last["close"])
    side = picked[0]

    if side == "LONG":
        entry = price
        sl = round(price - 0.75 * atr, 4)
        tp = round(price + 1.5 * atr, 4)
    else:
        entry = price
        sl = round(price + 0.75 * atr, 4)
        tp = round(price - 1.5 * atr, 4)

    msg = (
        f"[{symbol} {INTERVAL} Signal] {('LONG 🚀' if side=='LONG' else 'SHORT 🔻')}\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        f"RSI: {round(float(last['rsi']),2)} | "
        f"EMA{EMA_FAST}: {round(float(last['ema_fast']),2)} | "
        f"EMA{EMA_SLOW}: {round(float(last['ema_slow']),2)}\n"
        f"VolSpike: {bool(last['vol_spike'])} | "
        f"Time: {datetime.now(DHAKA_TZ).strftime('%Y-%m-%d %I:%M %p (Dhaka)')}"
    )
    return msg

# ================== MAIN LOOP ==================
def main():
    print(Fore.YELLOW + f"Starting multi-symbol worker for {SYMBOLS} @ {INTERVAL} ...")
    last_sent = {}
    last_heartbeat = 0  # Track last heartbeat time

    while True:
        try:
            for sym in SYMBOLS:
                try:
                    df = fetch_klines(sym, INTERVAL, limit=400)
                    sig = make_signal(sym, df)
                    now = int(time.time())

                    # === SEND TRADING SIGNAL ===
                    if sig and now - last_sent.get(sym, 0) >= CHECK_INTERVAL // 2:
                        print(Fore.YELLOW + sig + "\n")
                        send_telegram(sig)
                        last_sent[sym] = now

                    # Small pause to avoid Binance rate-limit
                    time.sleep(0.3)

                except Exception as e:
                    print(Fore.RED + f"{sym} error: {e}")

            # === HEARTBEAT: send every HEARTBEAT_INTERVAL ===
            now = int(time.time())
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                hb_msg = f"✅ BOT is Running by Professor007\n⏰ {datetime.now(DHAKA_TZ).strftime('%Y-%m-%d %I:%M %p (Dhaka)')}"
                send_telegram(hb_msg)
                print(Fore.GREEN + "[HEARTBEAT] " + hb_msg + "\n")
                last_heartbeat = now

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(Fore.RED + "Loop error:", e)
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

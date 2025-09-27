# Binance Multi-Market Signal Bot (RSI + EMA + Volume)

Watches multiple symbols on Binance (default: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT) on your chosen interval
(default 15m). Sends **Entry / SL / TP** signals to Telegram when filters align:
- Trend (EMA50 vs EMA200)
- RSI threshold / cross
- Volume spike vs 50-candle average

## Quick Start
1) Create a Telegram bot with @BotFather → copy the **bot token**.
2) Get your chat id: send `/start` to your bot, then visit:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` → copy `chat.id`.
3) Copy `.env.example` → `.env` and fill in your values.
4) Install and run:
```bash
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)    # on Windows: set KEY=VALUE
python worker.py
```

## 24/7 Cloud (Railway/Render)
- Upload these files to a new service.
- Add the same environment variables from `.env` in the dashboard.
- Start command: `python worker.py`

## Config
- `BINANCE_SYMBOLS` comma-separated list (no spaces).
- `BINANCE_INTERVAL` e.g. 15m, 1h, 4h.
- `CHECK_INTERVAL_SECONDS` how often to check (>= 60 for 15m).
- Tune `RSI_LOWER/UPPER`, `EMA_FAST/SLOW`, and `VOLUME_MULTIPLIER`.

**Note**: Uses Binance public klines endpoint (no API key required). Signals are for analysis only.

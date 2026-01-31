"""
telegram_paper_trading_bot.py

Bot de Paper Trading com Notificações Telegram
Contabilização oficial desde 01/01/2026
"""

import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
import json
from pathlib import Path
import requests
import os
from dotenv import load_dotenv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io

# =============================
# CONFIGURAÇÃO FIXA DO PERÍODO
# =============================
YEAR_START = datetime(2026, 1, 1)

load_dotenv()

# =============================
# CONFIGURAÇÃO
# =============================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'
MA_PERIOD = 8
BODY_MIN_PERCENT = 45
RR_RATIO = 2.2
COOLDOWN_BARS = 12
RISK_PER_TRADE = 0.02
LEVERAGE = 2.5

INITIAL_BALANCE = 10000

SESSION_START_HOUR = 8
SESSION_END_HOUR = 17
TIMING_WINDOW_PERCENT = 33.33

DAILY_REPORT_HOUR = 18

DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)

TRADES_FILE = DATA_DIR / 'telegram_trades.json'
STATE_FILE = DATA_DIR / 'telegram_state.json'
EQUITY_FILE = DATA_DIR / 'equity_curve.json'

# =============================
# TELEGRAM
# =============================
class TelegramNotifier:

    def __init__(self, token, chat_id):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def send_message(self, text, parse_mode="HTML"):
        try:
            requests.post(
                f"{self.base_url}/sendMessage",
                data={'chat_id': self.chat_id, 'text': text, 'parse_mode': parse_mode},
                timeout=10
            )
        except Exception as e:
            print(e)

    def send_photo(self, photo_bytes, caption=""):
        try:
            requests.post(
                f"{self.base_url}/sendPhoto",
                files={'photo': photo_bytes},
                data={'chat_id': self.chat_id, 'caption': caption, 'parse_mode': 'HTML'},
                timeout=30
            )
        except Exception as e:
            print(e)

    def format_trade_entry(self, s):
        return f"""
{'🟢' if s['side']=='LONG' else '🔴'} <b>NOVA POSIÇÃO {s['side']}</b>

Entry: ${s['entry']:,.2f}
Stop: ${s['stop']:,.2f}
Target: ${s['target']:,.2f}
R:R {RR_RATIO}

Risk: ${s['risk_usd']:,.2f}
Size: {s['size']:.4f} BTC
Leverage: {LEVERAGE}x

{s['timestamp']}
""".strip()

    def format_trade_exit(self, t):
        emoji = "🎯" if t['outcome'] == 'TARGET' else "🛑"
        pnl = "💚" if t['pnl_usd'] > 0 else "❤️"

        return f"""
{emoji} <b>{t['outcome']}</b>

Side: {t['side']}
Entry: ${t['entry']:,.2f}
Exit: ${t['exit']:,.2f}
Duração: {t['duration_hours']}h

{pnl} PnL: ${t['pnl_usd']:+,.2f}
Balance: ${t['balance_after']:,.2f}

{t['exit_time']}
""".strip()

    def format_daily_report(self, s):
        return f"""
📊 <b>RELATÓRIO 2026</b>
📅 Desde: 01/01/2026

Balance: ${s['current_balance']:,.2f}
Return: {s['total_return_pct']:+.2f}%

Trades: {s['total_trades']}
Winrate: {s['win_rate']:.1f}%
Lucro: ${s['total_profit_usd']:+,.2f}

Hoje:
Trades: {s['trades_today']}
PnL: ${s['pnl_today_usd']:+,.2f}

Status: {s['position_status']}
Dias rodando: {s['days_running']}
""".strip()

# =============================
# BOT PRINCIPAL
# =============================
class TelegramPaperTradingBot:

    def __init__(self):
        self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

        self.paper_balance = INITIAL_BALANCE
        self.initial_balance = INITIAL_BALANCE
        self.position = None
        self.last_trade_bar = -999
        self.all_trades = []
        self.equity_curve = []

        self._load_state()
        self.telegram.send_message("🚀 Bot online | Estatísticas desde 01/01/2026")

    def _year_start(self):
        return YEAR_START

    def _load_state(self):
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                s = json.load(f)
                self.paper_balance = s.get('balance', INITIAL_BALANCE)

        if TRADES_FILE.exists():
            with open(TRADES_FILE) as f:
                self.all_trades = json.load(f)

        if EQUITY_FILE.exists():
            with open(EQUITY_FILE) as f:
                self.equity_curve = json.load(f)

    def _save_state(self):
        json.dump({'balance': self.paper_balance}, open(STATE_FILE, 'w'))
        json.dump(self.all_trades, open(TRADES_FILE, 'w'))
        json.dump(self.equity_curve, open(EQUITY_FILE, 'w'))

    def _calculate_stats(self):
        year_start = self._year_start()

        trades = [
            t for t in self.all_trades
            if datetime.strptime(t['exit_time'], '%d/%m/%Y %H:%M') >= year_start
        ]

        year_balance = self.initial_balance
        for e in self.equity_curve:
            if datetime.fromisoformat(e['timestamp']) < year_start:
                year_balance = e['equity']
            else:
                break

        wins = [t for t in trades if t['pnl_usd'] > 0]

        today = datetime.now().strftime('%d/%m/%Y')
        today_trades = [t for t in trades if t['exit_time'].startswith(today)]

        return {
            'current_balance': self.paper_balance,
            'total_trades': len(trades),
            'win_rate': (len(wins)/len(trades)*100) if trades else 0,
            'total_profit_usd': self.paper_balance - year_balance,
            'total_return_pct': ((self.paper_balance / year_balance) - 1) * 100,
            'trades_today': len(today_trades),
            'pnl_today_usd': sum(t['pnl_usd'] for t in today_trades),
            'position_status': self.position['side'] if self.position else 'FLAT',
            'days_running': (datetime.now() - year_start).days + 1
        }

    def run(self):
        while True:
            try:
                self.telegram.send_message(
                    self.telegram.format_daily_report(self._calculate_stats())
                )
                time.sleep(3600)
            except Exception as e:
                print(e)
                time.sleep(60)

# =============================
# EXECUÇÃO
# =============================
if __name__ == "__main__":
    bot = TelegramPaperTradingBot()
    bot.run()

"""
telegram_paper_trading_bot.py

Bot de Paper Trading com Notificações Telegram
"""

import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import json
from pathlib import Path
import requests
import os
from dotenv import load_dotenv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import holidays
import pytz

load_dotenv()

print("="*80)
print("DEBUG - VERIFICANDO VARIÁVEIS")
print("="*80)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

print(f"Token presente: {'SIM' if TELEGRAM_BOT_TOKEN else 'NAO'}")
if TELEGRAM_BOT_TOKEN:
    print(f"Token (20 chars): {TELEGRAM_BOT_TOKEN[:20]}...")
print(f"Chat ID presente: {'SIM' if TELEGRAM_CHAT_ID else 'NAO'}")
if TELEGRAM_CHAT_ID:
    print(f"Chat ID: {TELEGRAM_CHAT_ID}")
print("="*80)

SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'
MA_PERIOD = 8
BODY_MIN_PERCENT = 45
RR_RATIO = 2.1
COOLDOWN_HOURS = 12
RISK_PER_TRADE = 0.02
LEVERAGE = 2.5
TAKER_FEE = 0.0004
SLIPPAGE = 0.0002
INITIAL_BALANCE = 10000
START_DATE = datetime(2026, 1, 1)
NY_TZ = pytz.timezone('America/New_York')
SESSION_START_HOUR = 8
SESSION_END_HOUR = 11
REPORT_HOUR_NY = 11
REPORT_MINUTE_NY = 10
TICK_SIZE = 0.1

DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)
TRADES_FILE = DATA_DIR / 'telegram_trades.json'
STATE_FILE = DATA_DIR / 'telegram_state.json'
EQUITY_FILE = DATA_DIR / 'equity_curve.json'

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        print(f"Telegram inicializado - Chat: {chat_id}")
    
    def send_message(self, text: str, parse_mode: str = "HTML"):
        print(f"\nEnviando mensagem... ({len(text)} chars)")
        try:
            url = f"{self.base_url}/sendMessage"
            data = {'chat_id': self.chat_id, 'text': text, 'parse_mode': parse_mode}
            response = requests.post(url, data=data, timeout=10)
            result = response.json()
            print(f"Status: {response.status_code} - OK: {result.get('ok')}")
            if not result.get('ok'):
                print(f"Erro: {result.get('description')}")
            return result
        except Exception as e:
            print(f"ERRO: {e}")
            return None
    
    def send_photo(self, photo_bytes: bytes, caption: str = ""):
        print(f"\nEnviando foto... ({len(photo_bytes)} bytes)")
        try:
            url = f"{self.base_url}/sendPhoto"
            files = {'photo': photo_bytes}
            data = {'chat_id': self.chat_id, 'caption': caption, 'parse_mode': 'HTML'}
            response = requests.post(url, files=files, data=data, timeout=30)
            result = response.json()
            print(f"Status: {response.status_code} - OK: {result.get('ok')}")
            return result
        except Exception as e:
            print(f"ERRO: {e}")
            return None

class PaperTradingBot:
    def __init__(self):
        print("="*80)
        print("BOT INICIANDO")
        print("="*80)
        
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN nao configurado!")
        if not TELEGRAM_CHAT_ID:
            raise ValueError("TELEGRAM_CHAT_ID nao configurado!")
        
        self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        self.exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
        self.paper_balance = INITIAL_BALANCE
        self.initial_balance = INITIAL_BALANCE
        self.position = None
        self.all_trades = []
        self.equity_curve = []
        self.start_date = START_DATE
        self.last_daily_report = None
        self.last_trade_time = None
        self.us_holidays = holidays.US(years=range(2026, 2030))
        
        self._load_state()
        self._send_startup_message()
        
        print(f"Balance: ${self.paper_balance:,.2f}")
        print(f"Trades: {len(self.all_trades)}")
        print("="*80)
    
    def _load_state(self):
        if STATE_FILE.exists():
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                self.paper_balance = state.get('balance', INITIAL_BALANCE)
                self.initial_balance = state.get('initial_balance', INITIAL_BALANCE)
                self.start_date = datetime.fromisoformat(state.get('start_date', START_DATE.isoformat()))
                self.last_daily_report = state.get('last_daily_report')
                if state.get('last_trade_time'):
                    self.last_trade_time = datetime.fromisoformat(state['last_trade_time'])
                print("Estado carregado")
        
        if TRADES_FILE.exists():
            with open(TRADES_FILE, 'r') as f:
                self.all_trades = json.load(f)
                print(f"{len(self.all_trades)} trades carregados")
        
        if EQUITY_FILE.exists():
            with open(EQUITY_FILE, 'r') as f:
                self.equity_curve = json.load(f)
    
    def _save_state(self):
        state = {
            'balance': self.paper_balance,
            'initial_balance': self.initial_balance,
            'start_date': self.start_date.isoformat(),
            'last_daily_report': self.last_daily_report,
            'last_trade_time': self.last_trade_time.isoformat() if self.last_trade_time else None,
            'last_update': datetime.now().isoformat()
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        with open(TRADES_FILE, 'w') as f:
            json.dump(self.all_trades, f, indent=2)
        with open(EQUITY_FILE, 'w') as f:
            json.dump(self.equity_curve, f, indent=2)
        print("Estado salvo")
    
    def _is_trading_day(self, dt: datetime) -> bool:
        if dt.weekday() >= 5:
            return False
        if dt.date() in self.us_holidays:
            return False
        return True
    
    def _is_trading_hours(self, dt: datetime) -> bool:
        ny_time = dt.astimezone(NY_TZ)
        return SESSION_START_HOUR <= ny_time.hour < SESSION_END_HOUR
    
    def _calculate_body_percent(self, row) -> float:
        range_size = row['high'] - row['low']
        if range_size == 0:
            return 0
        body_size = abs(row['close'] - row['open'])
        return (body_size / range_size) * 100
    
    def _detect_ma_turn(self, df: pd.DataFrame, index: int) -> str:
        if index < 2:
            return None
        ma_prev2 = df.loc[index - 2, 'sma']
        ma_prev1 = df.loc[index - 1, 'sma']
        ma_curr = df.loc[index, 'sma']
        if ma_prev1 < ma_prev2 and ma_curr > ma_prev1:
            return 'UP'
        if ma_prev1 > ma_prev2 and ma_curr < ma_prev1:
            return 'DOWN'
        return None
    
    def _in_cooldown(self, current_time: datetime) -> bool:
        if self.last_trade_time is None:
            return False
        hours_since_last = (current_time - self.last_trade_time).total_seconds() / 3600
        return hours_since_last < COOLDOWN_HOURS
    
    def _calculate_position_size(self, entry: float, stop: float) -> float:
        risk_usd = self.paper_balance * RISK_PER_TRADE
        risk_per_btc = abs(entry - stop)
        if risk_per_btc == 0:
            return 0
        position_size = risk_usd / risk_per_btc
        return position_size * LEVERAGE
    
    def _execute_trade(self, side: str, entry: float, stop: float, signal_time: datetime):
        """Executa entrada"""
        if side == 'LONG':
            entry_executed = entry * (1 + SLIPPAGE)
        else:
            entry_executed = entry * (1 - SLIPPAGE)
        
        risk_distance = abs(entry_executed - stop)
        if side == 'LONG':
            target = entry_executed + (risk_distance * RR_RATIO)
        else:
            target = entry_executed - (risk_distance * RR_RATIO)
        
        size = self._calculate_position_size(entry_executed, stop)
        position_value = size * entry_executed / LEVERAGE
        entry_fee = position_value * TAKER_FEE
        self.paper_balance -= entry_fee
        
        self.position = {
            'side': side,
            'entry': entry_executed,
            'stop': stop,
            'target': target,
            'size': size,
            'entry_time': signal_time.isoformat(),
            'entry_fee': entry_fee
        }
        
        print(f"\n{'LONG' if side == 'LONG' else 'SHORT'} @ ${entry_executed:,.2f}")
        print(f"Stop: ${stop:,.2f} | Target: ${target:,.2f}")
        print(f"Size: {size:.4f} BTC")
        
        # Notifica Telegram
        msg = f"""
🟢 <b>NOVA POSICAO {side}</b>

• Entry: ${entry_executed:,.2f}
• Stop: ${stop:,.2f}
• Target: ${target:,.2f}
• Size: {size:.4f} BTC
• Risk: {RISK_PER_TRADE*100}%

⏰ {signal_time.strftime('%d/%m/%Y %H:%M')}
        """
        self.telegram.send_message(msg.strip())
    
    def _close_position(self, exit_price: float, outcome: str, exit_time: datetime):
        """Fecha posicao"""
        if not self.position:
            return
        
        if self.position['side'] == 'LONG':
            exit_executed = exit_price * (1 - SLIPPAGE)
            pnl_gross = (exit_executed - self.position['entry']) * self.position['size']
        else:
            exit_executed = exit_price * (1 + SLIPPAGE)
            pnl_gross = (self.position['entry'] - exit_executed) * self.position['size']
        
        position_value = self.position['size'] * exit_executed / LEVERAGE
        exit_fee = position_value * TAKER_FEE
        pnl_net = pnl_gross - self.position['entry_fee'] - exit_fee
        
        self.paper_balance += pnl_net
        
        entry_dt = datetime.fromisoformat(self.position['entry_time'])
        duration_hours = (exit_time - entry_dt).total_seconds() / 3600
        
        trade = {
            'side': self.position['side'],
            'entry': self.position['entry'],
            'exit': exit_executed,
            'stop': self.position['stop'],
            'target': self.position['target'],
            'size': self.position['size'],
            'outcome': outcome,
            'pnl_usd': pnl_net,
            'pnl_pct': (pnl_net / (self.paper_balance - pnl_net)) * 100,
            'fees_total': self.position['entry_fee'] + exit_fee,
            'balance_after': self.paper_balance,
            'entry_time': self.position['entry_time'],
            'exit_time': exit_time.isoformat(),
            'duration_hours': duration_hours
        }
        
        self.all_trades.append(trade)
        self.last_trade_time = exit_time
        
        self.equity_curve.append({
            'timestamp': exit_time.isoformat(),
            'balance': self.paper_balance,
            'trade_number': len(self.all_trades)
        })
        
        print(f"\n{'TARGET' if outcome == 'TARGET' else 'STOP'} @ ${exit_executed:,.2f}")
        print(f"PnL: ${pnl_net:+,.2f} ({trade['pnl_pct']:+.2f}%)")
        print(f"Balance: ${self.paper_balance:,.2f}")
        
        # Notifica Telegram
        emoji = "🎯" if outcome == 'TARGET' else "🛑"
        pnl_emoji = "💚" if pnl_net > 0 else "❤️"
        msg = f"""
{emoji} <b>{outcome}</b>

• Exit: ${exit_executed:,.2f}
• Duracao: {duration_hours:.1f}h

{pnl_emoji} <b>Resultado:</b>
• PnL: ${pnl_net:+,.2f} ({trade['pnl_pct']:+.2f}%)
• Balance: ${self.paper_balance:,.2f}

⏰ {exit_time.strftime('%d/%m/%Y %H:%M')}
        """
        self.telegram.send_message(msg.strip())
        
        self.position = None
        self._save_state()
    
    def _send_startup_message(self):
        days_running = (datetime.now() - self.start_date).days
        msg = f"""
🚀 <b>BOT INICIADO</b>

📊 <b>Setup:</b>
• MA: SMA {MA_PERIOD}
• Body: > {BODY_MIN_PERCENT}%
• R:R: {RR_RATIO}
• Leverage: {LEVERAGE}x

💰 <b>Capital:</b>
• Balance: ${self.paper_balance:,.2f}
• Inicial: ${self.initial_balance:,.2f}
• Return: {((self.paper_balance/self.initial_balance - 1)*100):+.2f}%

📈 <b>Historico:</b>
• Total trades: {len(self.all_trades)}
• Dias rodando: {days_running}

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}
        """
        print("\nENVIANDO STARTUP MESSAGE")
        self.telegram.send_message(msg.strip())
    
    def run_backtest(self):
        """BACKTEST COMPLETO REAL"""
        print("\n" + "="*80)
        print("BACKTEST INICIANDO")
        print("="*80)
        
        if self.all_trades:
            print(f"Backtest ja executado ({len(self.all_trades)} trades)")
            print("="*80)
            return
        
        print(f"Baixando dados desde {START_DATE.strftime('%Y-%m-%d')}...")
        
        since = int(START_DATE.timestamp() * 1000)
        all_candles = []
        
        while True:
            try:
                candles = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=1000)
                if not candles:
                    break
                
                all_candles.extend(candles)
                print(f"Baixados {len(all_candles)} candles...")
                
                since = candles[-1][0] + 1
                
                if candles[-1][0] >= int(datetime.now().timestamp() * 1000):
                    break
                
                time.sleep(self.exchange.rateLimit / 1000)
            except Exception as e:
                print(f"Erro ao baixar dados: {e}")
                break
        
        if not all_candles:
            print("ERRO: Nenhum candle baixado!")
            return
        
        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['sma'] = df['close'].rolling(MA_PERIOD).mean()
        df['body_pct'] = df.apply(self._calculate_body_percent, axis=1)
        df.reset_index(inplace=True)
        
        print(f"Total de {len(df)} candles processados")
        print("Procurando sinais...")
        
        signals_found = 0
        
        for i in range(MA_PERIOD + 2, len(df)):
            current = df.loc[i]
            current_time = current['timestamp'].to_pydatetime()
            
            if not self._is_trading_day(current_time):
                continue
            
            # Verifica posicao aberta
            if self.position:
                if self.position['side'] == 'LONG':
                    if current['low'] <= self.position['stop']:
                        self._close_position(self.position['stop'], 'STOP', current_time)
                    elif current['high'] >= self.position['target']:
                        self._close_position(self.position['target'], 'TARGET', current_time)
                else:
                    if current['high'] >= self.position['stop']:
                        self._close_position(self.position['stop'], 'STOP', current_time)
                    elif current['low'] <= self.position['target']:
                        self._close_position(self.position['target'], 'TARGET', current_time)
                continue
            
            if not self._is_trading_hours(current_time):
                continue
            
            if self._in_cooldown(current_time):
                continue
            
            ma_turn = self._detect_ma_turn(df, i)
            
            if ma_turn and current['body_pct'] >= BODY_MIN_PERCENT:
                signals_found += 1
                
                if ma_turn == 'UP':
                    trigger = current['high']
                    stop = current['low'] - TICK_SIZE
                    
                    if i + 1 < len(df):
                        next_candle = df.loc[i + 1]
                        if next_candle['high'] >= trigger:
                            self._execute_trade('LONG', trigger, stop, next_candle['timestamp'].to_pydatetime())
                
                elif ma_turn == 'DOWN':
                    trigger = current['low']
                    stop = current['high'] + TICK_SIZE
                    
                    if i + 1 < len(df):
                        next_candle = df.loc[i + 1]
                        if next_candle['low'] <= trigger:
                            self._execute_trade('SHORT', trigger, stop, next_candle['timestamp'].to_pydatetime())
        
        print(f"\nSinais encontrados: {signals_found}")
        print(f"Trades executados: {len(self.all_trades)}")
        print(f"Balance final: ${self.paper_balance:,.2f}")
        print("="*80)
        
        self._save_state()
    
    def check_and_report(self):
        print("\nVERIFICANDO RELATORIO")
        now = datetime.now()
        ny_now = now.astimezone(NY_TZ)
        print(f"Horario NY: {ny_now.strftime('%H:%M')}")
        print(f"Configurado: {REPORT_HOUR_NY:02d}:{REPORT_MINUTE_NY:02d}")

if __name__ == '__main__':
    try:
        print("\nINICIANDO BOT")
        bot = PaperTradingBot()
        bot.run_backtest()
        bot.check_and_report()
        print("\nBOT EXECUTADO COM SUCESSO!")
    except Exception as e:
        print(f"\nERRO: {e}")
        import traceback
        traceback.print_exc()

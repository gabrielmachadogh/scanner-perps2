"""Bot de Paper Trading com Telegram - MEXC"""
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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

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
TICK_SIZE = 0.1

DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)
TRADES_FILE = DATA_DIR / 'telegram_trades.json'
STATE_FILE = DATA_DIR / 'telegram_state.json'

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, text, parse_mode="HTML"):
        try:
            url = f"{self.base_url}/sendMessage"
            data = {'chat_id': self.chat_id, 'text': text, 'parse_mode': parse_mode}
            response = requests.post(url, data=data, timeout=10)
            return response.json()
        except Exception as e:
            print(f"Erro Telegram: {e}")
            return None

class PaperTradingBot:
    def __init__(self):
        print("="*80)
        print("BOT INICIANDO - MEXC")
        print("="*80)
        
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            raise ValueError("Telegram nao configurado!")
        
        self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        
        # MEXC Exchange
        print("Conectando à MEXC...")
        self.exchange = ccxt.mexc({
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}  # Futures na MEXC
        })
        print("✅ MEXC conectada")
        
        self.paper_balance = INITIAL_BALANCE
        self.initial_balance = INITIAL_BALANCE
        self.position = None
        self.all_trades = []
        self.start_date = START_DATE
        self.last_trade_time = None
        self.us_holidays = holidays.US(years=range(2026, 2030))
        
        self._load_state()
        self._send_startup_message()
    
    def _load_state(self):
        if STATE_FILE.exists():
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                self.paper_balance = state.get('balance', INITIAL_BALANCE)
                self.initial_balance = state.get('initial_balance', INITIAL_BALANCE)
                self.start_date = datetime.fromisoformat(state.get('start_date', START_DATE.isoformat()))
                if state.get('last_trade_time'):
                    self.last_trade_time = datetime.fromisoformat(state['last_trade_time'])
                print(f"✅ Estado carregado: {len(state)} items")
        
        if TRADES_FILE.exists():
            with open(TRADES_FILE, 'r') as f:
                self.all_trades = json.load(f)
                print(f"✅ {len(self.all_trades)} trades carregados")
    
    def _save_state(self):
        state = {
            'balance': self.paper_balance,
            'initial_balance': self.initial_balance,
            'start_date': self.start_date.isoformat(),
            'last_trade_time': self.last_trade_time.isoformat() if self.last_trade_time else None,
            'last_update': datetime.now().isoformat()
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        with open(TRADES_FILE, 'w') as f:
            json.dump(self.all_trades, f, indent=2)
        print("✅ Estado salvo")
    
    def _is_trading_day(self, dt):
        if dt.weekday() >= 5:
            return False
        if dt.date() in self.us_holidays:
            return False
        return True
    
    def _is_trading_hours(self, dt):
        ny_time = dt.astimezone(NY_TZ)
        return SESSION_START_HOUR <= ny_time.hour < SESSION_END_HOUR
    
    def _calculate_body_percent(self, row):
        range_size = row['high'] - row['low']
        if range_size == 0:
            return 0
        body_size = abs(row['close'] - row['open'])
        return (body_size / range_size) * 100
    
    def _detect_ma_turn(self, df, index):
        if index < 2:
            return None
        ma_prev2 = df.loc[index - 2, 'sma']
        ma_prev1 = df.loc[index - 1, 'sma']
        ma_curr = df.loc[index, 'sma']
        
        if pd.isna(ma_prev2) or pd.isna(ma_prev1) or pd.isna(ma_curr):
            return None
        
        if ma_prev1 < ma_prev2 and ma_curr > ma_prev1:
            return 'UP'
        if ma_prev1 > ma_prev2 and ma_curr < ma_prev1:
            return 'DOWN'
        return None
    
    def _in_cooldown(self, current_time):
        if self.last_trade_time is None:
            return False
        hours_since_last = (current_time - self.last_trade_time).total_seconds() / 3600
        return hours_since_last < COOLDOWN_HOURS
    
    def _calculate_position_size(self, entry, stop):
        risk_usd = self.paper_balance * RISK_PER_TRADE
        risk_per_btc = abs(entry - stop)
        if risk_per_btc == 0:
            return 0
        return (risk_usd / risk_per_btc) * LEVERAGE
    
    def _execute_trade(self, side, entry, stop, signal_time):
        entry_executed = entry * (1 + SLIPPAGE) if side == 'LONG' else entry * (1 - SLIPPAGE)
        risk_distance = abs(entry_executed - stop)
        target = entry_executed + (risk_distance * RR_RATIO) if side == 'LONG' else entry_executed - (risk_distance * RR_RATIO)
        
        size = self._calculate_position_size(entry_executed, stop)
        entry_fee = (size * entry_executed / LEVERAGE) * TAKER_FEE
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
        
        print(f"\n✅ TRADE: {side} @ ${entry_executed:,.2f}")
        
        msg = f"""🟢 <b>{side}</b>

Entry: ${entry_executed:,.2f}
Stop: ${stop:,.2f}
Target: ${target:,.2f}
Size: {size:.4f} BTC

{signal_time.strftime('%d/%m %H:%M')}"""
        self.telegram.send_message(msg)
    
    def _close_position(self, exit_price, outcome, exit_time):
        if not self.position:
            return
        
        if self.position['side'] == 'LONG':
            exit_executed = exit_price * (1 - SLIPPAGE)
            pnl_gross = (exit_executed - self.position['entry']) * self.position['size']
        else:
            exit_executed = exit_price * (1 + SLIPPAGE)
            pnl_gross = (self.position['entry'] - exit_executed) * self.position['size']
        
        exit_fee = (self.position['size'] * exit_executed / LEVERAGE) * TAKER_FEE
        pnl_net = pnl_gross - self.position['entry_fee'] - exit_fee
        self.paper_balance += pnl_net
        
        entry_dt = datetime.fromisoformat(self.position['entry_time'])
        duration_hours = (exit_time - entry_dt).total_seconds() / 3600
        
        trade = {
            'side': self.position['side'],
            'entry': self.position['entry'],
            'exit': exit_executed,
            'outcome': outcome,
            'pnl_usd': pnl_net,
            'pnl_pct': (pnl_net / (self.paper_balance - pnl_net)) * 100,
            'balance_after': self.paper_balance,
            'entry_time': self.position['entry_time'],
            'exit_time': exit_time.isoformat(),
            'duration_hours': duration_hours
        }
        
        self.all_trades.append(trade)
        self.last_trade_time = exit_time
        
        print(f"✅ FECHADO: {outcome} @ ${exit_executed:,.2f} | PnL: ${pnl_net:+,.2f}")
        
        emoji = "🎯" if outcome == 'TARGET' else "🛑"
        msg = f"""{emoji} <b>{outcome}</b>

Exit: ${exit_executed:,.2f}
PnL: ${pnl_net:+,.2f}
Balance: ${self.paper_balance:,.2f}

{exit_time.strftime('%d/%m %H:%M')}"""
        self.telegram.send_message(msg)
        
        self.position = None
        self._save_state()
    
    def _send_startup_message(self):
        days = (datetime.now() - self.start_date).days
        msg = f"""🚀 <b>BOT INICIADO - MEXC</b>

Balance: ${self.paper_balance:,.2f}
Trades: {len(self.all_trades)}
Dias: {days}

{datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        self.telegram.send_message(msg)
    
    def run_backtest(self):
        print("\n" + "="*80)
        print("BACKTEST INICIANDO - MEXC")
        print("="*80)
        
        # FORÇA RODAR SEMPRE (para debug)
        if self.all_trades:
            print(f"⚠️  Já existem {len(self.all_trades)} trades, mas vou processar mesmo assim...")
            self.all_trades = []  # Limpa para refazer
            self.paper_balance = INITIAL_BALANCE
        
        print(f"📥 Baixando dados da MEXC desde {START_DATE.strftime('%Y-%m-%d')}...")
        
        # MEXC usa diferentes limites
        since = int(START_DATE.timestamp() * 1000)
        all_candles = []
        max_requests = 100  # Limite de segurança
        request_count = 0
        
        while request_count < max_requests:
            try:
                print(f"  Request {request_count + 1}... (total: {len(all_candles)} candles)")
                
                candles = self.exchange.fetch_ohlcv(
                    SYMBOL, 
                    TIMEFRAME, 
                    since=since, 
                    limit=500  # MEXC permite até 500
                )
                
                if not candles:
                    print("  Sem mais dados")
                    break
                
                all_candles.extend(candles)
                since = candles[-1][0] + 1
                request_count += 1
                
                # Para quando chegar no presente
                if candles[-1][0] >= int(datetime.now().timestamp() * 1000):
                    print("  Chegou no presente")
                    break
                
                # Rate limit da MEXC
                time.sleep(0.2)  # 200ms entre requests
                
            except Exception as e:
                print(f"❌ Erro na request {request_count + 1}: {e}")
                break
        
        if not all_candles:
            print("❌ Nenhum candle baixado!")
            msg = "❌ <b>ERRO</b>\n\nNenhum dado baixado da MEXC"
            self.telegram.send_message(msg)
            return
        
        print(f"✅ {len(all_candles)} candles baixados em {request_count} requests")
        
        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['sma'] = df['close'].rolling(MA_PERIOD).mean()
        df['body_pct'] = df.apply(self._calculate_body_percent, axis=1)
        df.reset_index(inplace=True)
        
        print(f"📊 Processando {len(df)} candles...")
        
        # CONTADORES
        total_candles = 0
        trading_days = 0
        trading_hours = 0
        ma_turns = 0
        body_ok = 0
        triggers = 0
        
        for i in range(MA_PERIOD + 2, len(df)):
            current = df.loc[i]
            current_time = current['timestamp'].to_pydatetime()
            total_candles += 1
            
            if not self._is_trading_day(current_time):
                continue
            trading_days += 1
            
            # Gerencia posição
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
            trading_hours += 1
            
            if self._in_cooldown(current_time):
                continue
            
            ma_turn = self._detect_ma_turn(df, i)
            if not ma_turn:
                continue
            ma_turns += 1
            
            if current['body_pct'] < BODY_MIN_PERCENT:
                continue
            body_ok += 1
            
            triggers += 1
            
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
        
        print("\n" + "="*80)
        print("ESTATÍSTICAS")
        print("="*80)
        print(f"Total candles: {total_candles}")
        print(f"Dias úteis: {trading_days}")
        print(f"Horário NY 8-11h: {trading_hours}")
        print(f"Viradas MA: {ma_turns}")
        print(f"Body > {BODY_MIN_PERCENT}%: {body_ok}")
        print(f"Triggers: {triggers}")
        print(f"Trades: {len(self.all_trades)}")
        print(f"Balance: ${self.paper_balance:,.2f}")
        print("="*80)
        
        # Resumo pro Telegram
        pnl_pct = ((self.paper_balance / self.initial_balance) - 1) * 100
        
        msg = f"""📊 <b>BACKTEST COMPLETO - MEXC</b>

Período: {START_DATE.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}

Candles: {total_candles}
Dias úteis: {trading_days}
Horário NY: {trading_hours}
Viradas MA: {ma_turns}
Body > 45%: {body_ok}
Triggers: {triggers}

<b>Trades: {len(self.all_trades)}</b>
Balance: ${self.paper_balance:,.2f}
Return: {pnl_pct:+.2f}%"""
        
        self.telegram.send_message(msg)
        
        self._save_state()

if __name__ == '__main__':
    try:
        bot = PaperTradingBot()
        bot.run_backtest()
        print("\n✅ SUCESSO!")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()

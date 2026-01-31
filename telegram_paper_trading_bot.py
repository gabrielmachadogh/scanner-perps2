"""
Bot de Paper Trading - BTC/USDT - MEXC
RESET COMPLETO: Apaga tudo e refaz backtest desde 01/01/2025
Comeca com $10,000 em 01/01/2025
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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Configuracao da estrategia
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

# BOT ONLINE DESDE 01/01/2025
START_DATE = datetime(2025, 1, 1, 0, 0, 0)

# Horarios
NY_TZ = pytz.timezone('America/New_York')
SESSION_START_HOUR = 8
SESSION_END_HOUR = 11
REPORT_HOUR_NY = 11
REPORT_MINUTE_NY = 10

TICK_SIZE = 0.1

# Arquivos
DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)
TRADES_FILE = DATA_DIR / 'telegram_trades.json'
STATE_FILE = DATA_DIR / 'telegram_state.json'
EQUITY_FILE = DATA_DIR / 'equity_curve.json'

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
    
    def send_photo(self, photo_bytes, caption=""):
        try:
            url = f"{self.base_url}/sendPhoto"
            files = {'photo': photo_bytes}
            data = {'chat_id': self.chat_id, 'caption': caption, 'parse_mode': 'HTML'}
            response = requests.post(url, files=files, data=data, timeout=30)
            return response.json()
        except Exception as e:
            print(f"Erro Telegram foto: {e}")
            return None

class PaperTradingBot:
    def __init__(self):
        print("="*80)
        print("BOT PAPER TRADING - RESET COMPLETO")
        print(f"Apagando dados antigos e recomeçando desde {START_DATE.strftime('%d/%m/%Y')}")
        print("="*80)
        
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            raise ValueError("Telegram nao configurado!")
        
        self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        
        print("Conectando a MEXC...")
        self.exchange = ccxt.mexc({
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        print("✅ MEXC conectada")
        
        # RESET COMPLETO - Apaga tudo
        print("\n🔄 DELETANDO DADOS ANTIGOS...")
        if TRADES_FILE.exists():
            TRADES_FILE.unlink()
            print("✅ trades.json deletado")
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            print("✅ state.json deletado")
        if EQUITY_FILE.exists():
            EQUITY_FILE.unlink()
            print("✅ equity.json deletado")
        
        # Inicia do ZERO
        self.paper_balance = INITIAL_BALANCE
        self.initial_balance = INITIAL_BALANCE
        self.position = None
        self.all_trades = []
        self.equity_curve = []
        self.start_date = START_DATE
        self.last_trade_time = None
        self.last_daily_report = None
        self.us_holidays = holidays.US(years=range(2025, 2030))
        
        print(f"\n✅ Bot resetado - Balance: ${INITIAL_BALANCE:,.2f}")
        
        self._send_startup_message()
    
    def _save_state(self):
        """Salva estado atual"""
        state = {
            'balance': self.paper_balance,
            'initial_balance': self.initial_balance,
            'start_date': START_DATE.isoformat(),
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
        if side == 'LONG':
            target = entry_executed + (risk_distance * RR_RATIO)
        else:
            target = entry_executed - (risk_distance * RR_RATIO)
        
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
        
        print(f"\n✅ {side} @ ${entry_executed:,.2f} | Stop: ${stop:,.2f}")
        
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
            'stop': self.position['stop'],
            'target': self.position['target'],
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
        
        self.equity_curve.append({
            'timestamp': exit_time.isoformat(),
            'balance': self.paper_balance,
            'trade_number': len(self.all_trades)
        })
        
        print(f"✅ {outcome} @ ${exit_executed:,.2f} | PnL: ${pnl_net:+,.2f}")
        
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
        msg = f"""🚀 <b>BOT RESETADO - NOVO BACKTEST</b>

📅 Inicio: {START_DATE.strftime('%d/%m/%Y')}
💰 Capital Inicial: ${INITIAL_BALANCE:,.2f}

Processando todos os trades desde 01/01/2025...

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        self.telegram.send_message(msg)
    
    def _create_equity_chart(self):
        if not self.equity_curve:
            return None
        
        try:
            df_equity = pd.DataFrame(self.equity_curve)
            df_equity['timestamp'] = pd.to_datetime(df_equity['timestamp'])
            
            start_point = pd.DataFrame([{
                'timestamp': START_DATE,
                'balance': INITIAL_BALANCE,
                'trade_number': 0
            }])
            df_equity = pd.concat([start_point, df_equity], ignore_index=True)
            
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8))
            
            ax1.plot(df_equity['timestamp'], df_equity['balance'], 
                    linewidth=2.5, color='#2E86AB', label='Balance')
            ax1.axhline(y=INITIAL_BALANCE, color='gray', linestyle='--', 
                       alpha=0.5, linewidth=1.5, label='Capital Inicial')
            
            ax1.fill_between(df_equity['timestamp'], INITIAL_BALANCE, df_equity['balance'], 
                           where=(df_equity['balance'] >= INITIAL_BALANCE), 
                           alpha=0.3, color='green', label='Profit')
            ax1.fill_between(df_equity['timestamp'], INITIAL_BALANCE, df_equity['balance'], 
                           where=(df_equity['balance'] < INITIAL_BALANCE), 
                           alpha=0.3, color='red', label='Loss')
            
            ax1.set_title(f'Equity Curve - desde {START_DATE.strftime("%d/%m/%Y")}', 
                         fontsize=16, fontweight='bold')
            ax1.set_ylabel('Balance (USD)', fontsize=12)
            ax1.legend(loc='best')
            ax1.grid(alpha=0.3)
            
            running_max = df_equity['balance'].expanding().max()
            drawdown = ((df_equity['balance'] - running_max) / running_max) * 100
            
            ax2.fill_between(df_equity['timestamp'], 0, drawdown, color='red', alpha=0.3)
            ax2.plot(df_equity['timestamp'], drawdown, color='darkred', linewidth=2)
            ax2.set_title('Drawdown (%)', fontsize=14, fontweight='bold')
            ax2.set_ylabel('Drawdown %', fontsize=12)
            ax2.set_xlabel('Data', fontsize=12)
            ax2.grid(alpha=0.3)
            
            plt.tight_layout()
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            plt.close()
            
            return buf.read()
            
        except Exception as e:
            print(f"Erro grafico: {e}")
            return None
    
    def _send_daily_report(self):
        print("\n" + "="*80)
        print("RELATORIO DIARIO")
        print("="*80)
        
        total_trades = len(self.all_trades)
        
        if total_trades == 0:
            msg = f"""📊 <b>RELATORIO DIARIO</b>

Periodo: {START_DATE.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}

Nenhum trade ainda.
Balance: ${self.paper_balance:,.2f}"""
            self.telegram.send_message(msg)
            self.last_daily_report = datetime.now().isoformat()
            self._save_state()
            return
        
        wins = [t for t in self.all_trades if t['pnl_usd'] > 0]
        losses = [t for t in self.all_trades if t['pnl_usd'] <= 0]
        
        num_wins = len(wins)
        num_losses = len(losses)
        win_rate = (num_wins / total_trades * 100) if total_trades > 0 else 0
        
        total_profit = sum(t['pnl_usd'] for t in wins)
        total_loss = sum(t['pnl_usd'] for t in losses)
        net_pnl = total_profit + total_loss
        
        avg_win = (total_profit / num_wins) if num_wins > 0 else 0
        avg_loss = (total_loss / num_losses) if num_losses > 0 else 0
        
        profit_factor = abs(total_profit / total_loss) if total_loss != 0 else 0
        return_pct = ((self.paper_balance / self.initial_balance) - 1) * 100
        
        today = datetime.now().date()
        trades_today = [t for t in self.all_trades 
                       if datetime.fromisoformat(t['exit_time']).date() == today]
        pnl_today = sum(t['pnl_usd'] for t in trades_today)
        
        days_running = (datetime.now() - self.start_date).days
        position_status = f"{self.position['side']} aberta" if self.position else "Sem posicao"
        
        last_trade = self.all_trades[-1]
        last_trade_time = datetime.fromisoformat(last_trade['exit_time']).strftime('%d/%m %H:%M')
        
        best_trade = max(self.all_trades, key=lambda x: x['pnl_usd'])
        worst_trade = min(self.all_trades, key=lambda x: x['pnl_usd'])
        
        msg = f"""📊 <b>RELATORIO DIARIO</b>
━━━━━━━━━━━━━━━━━━━━━━

📅 <b>Periodo:</b>
{START_DATE.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')} ({days_running} dias)

💰 <b>Capital:</b>
• Balance: ${self.paper_balance:,.2f}
• Inicial: ${self.initial_balance:,.2f}
• Return: {return_pct:+.2f}%
• PnL Total: ${net_pnl:+,.2f}

📈 <b>Performance:</b>
• Total Trades: {total_trades}
• Wins: {num_wins} ({win_rate:.1f}%)
• Losses: {num_losses} ({100-win_rate:.1f}%)
• Profit Factor: {profit_factor:.2f}

💵 <b>Medias:</b>
• Avg Win: ${avg_win:+,.2f}
• Avg Loss: ${avg_loss:+,.2f}
• Total Ganho: ${total_profit:+,.2f}
• Total Perdido: ${total_loss:+,.2f}

🏆 <b>Extremos:</b>
• Melhor: ${best_trade['pnl_usd']:+,.2f}
• Pior: ${worst_trade['pnl_usd']:+,.2f}

📊 <b>Hoje:</b>
• Trades: {len(trades_today)}
• PnL: ${pnl_today:+,.2f}

🎯 <b>Status:</b>
• Posicao: {position_status}
• Ultimo Trade: {last_trade_time}

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        
        self.telegram.send_message(msg)
        
        chart_bytes = self._create_equity_chart()
        if chart_bytes:
            caption = f"""📈 <b>Equity Curve</b>

{START_DATE.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}
Trades: {total_trades} | WR: {win_rate:.1f}% | Return: {return_pct:+.2f}%"""
            
            self.telegram.send_photo(chart_bytes, caption=caption)
        
        self.last_daily_report = datetime.now().isoformat()
        self._save_state()
    
    def run_backtest(self):
        print("\n" + "="*80)
        print("BACKTEST COMPLETO DESDE 01/01/2025")
        print("="*80)
        
        print(f"📥 Baixando dados desde {START_DATE.strftime('%d/%m/%Y')}...")
        
        since = int(START_DATE.timestamp() * 1000)
        all_candles = []
        max_requests = 250
        request_count = 0
        
        while request_count < max_requests:
            try:
                print(f"  Request {request_count + 1}... ({len(all_candles)} candles)")
                
                candles = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=500)
                
                if not candles:
                    break
                
                all_candles.extend(candles)
                since = candles[-1][0] + 1
                request_count += 1
                
                if candles[-1][0] >= int(datetime.now().timestamp() * 1000):
                    break
                
                time.sleep(0.2)
                
            except Exception as e:
                print(f"Erro: {e}")
                break
        
        if not all_candles:
            print("Nenhum candle!")
            return
        
        print(f"✅ {len(all_candles)} candles baixados")
        
        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['sma'] = df['close'].rolling(MA_PERIOD).mean()
        df['body_pct'] = df.apply(self._calculate_body_percent, axis=1)
        df.reset_index(inplace=True)
        
        print(f"📊 Processando {len(df)} candles...")
        
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
                        self._execute_trade('LONG', trigger, stop, 
                                          next_candle['timestamp'].to_pydatetime())
            
            elif ma_turn == 'DOWN':
                trigger = current['low']
                stop = current['high'] + TICK_SIZE
                
                if i + 1 < len(df):
                    next_candle = df.loc[i + 1]
                    if next_candle['low'] <= trigger:
                        self._execute_trade('SHORT', trigger, stop, 
                                          next_candle['timestamp'].to_pydatetime())
        
        print("\n" + "="*80)
        print("BACKTEST FINALIZADO")
        print("="*80)
        print(f"Periodo: {START_DATE.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}")
        print(f"Candles: {total_candles}")
        print(f"Dias uteis: {trading_days}")
        print(f"Horario NY: {trading_hours}")
        print(f"Viradas MA: {ma_turns}")
        print(f"Body > 45%: {body_ok}")
        print(f"Triggers: {triggers}")
        print(f"TRADES: {len(self.all_trades)}")
        print(f"BALANCE: ${self.paper_balance:,.2f}")
        print("="*80)
        
        pnl_pct = ((self.paper_balance / self.initial_balance) - 1) * 100
        wins = len([t for t in self.all_trades if t['pnl_usd'] > 0])
        win_rate = (wins / len(self.all_trades) * 100) if self.all_trades else 0
        
        msg = f"""📊 <b>BACKTEST COMPLETO</b>

📅 {START_DATE.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}

Candles: {total_candles}
Horario NY: {trading_hours}
Viradas MA: {ma_turns}
Triggers: {triggers}

<b>✅ TRADES: {len(self.all_trades)}</b>
Win Rate: {win_rate:.1f}%
Balance: ${self.paper_balance:,.2f}
Return: {pnl_pct:+.2f}%"""
        
        self.telegram.send_message(msg)
        self._save_state()
    
    def check_and_report(self):
        now = datetime.now()
        ny_now = now.astimezone(NY_TZ)
        
        if ny_now.hour == REPORT_HOUR_NY and ny_now.minute >= REPORT_MINUTE_NY:
            if self.last_daily_report:
                last_report_date = datetime.fromisoformat(self.last_daily_report).date()
                if last_report_date == now.date():
                    print("Relatorio ja enviado hoje")
                    return
            
            self._send_daily_report()

if __name__ == '__main__':
    try:
        bot = PaperTradingBot()
        bot.run_backtest()
        bot.check_and_report()
        print("\n✅ COMPLETO!")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()

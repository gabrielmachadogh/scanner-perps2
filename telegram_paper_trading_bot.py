"""
Bot de Paper Trading - BTC/USDT - MEXC
- Backtest maximo possivel (ate 10 anos)
- Body% reduzido para 42%
- Continua operando apos backtest
- Relatorios diarios as 16:10 BRT
- Report de lucro por ano
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
from collections import defaultdict

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Configuracao
SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'
MA_PERIOD = 8
BODY_MIN_PERCENT = 42  # REDUZIDO DE 45% PARA 42%
RR_RATIO = 2.1
COOLDOWN_HOURS = 12
RISK_PER_TRADE = 0.02
LEVERAGE = 2.5
TAKER_FEE = 0.0004
SLIPPAGE = 0.0002
INITIAL_BALANCE = 10000

# Tenta pegar 10 anos de dados
START_DATE = datetime.now() - timedelta(days=3650)  # 10 anos

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
BACKTEST_DONE_FILE = DATA_DIR / 'backtest_done.flag'

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
            print(f"Erro foto: {e}")
            return None

class PaperTradingBot:
    def __init__(self):
        print("="*80)
        print("BOT PAPER TRADING - MODO OPERACIONAL")
        print("="*80)
        
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            raise ValueError("Telegram nao configurado!")
        
        self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        
        print("Conectando MEXC...")
        self.exchange = ccxt.mexc({
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        print("✅ MEXC conectada")
        
        self.paper_balance = INITIAL_BALANCE
        self.initial_balance = INITIAL_BALANCE
        self.position = None
        self.all_trades = []
        self.equity_curve = []
        self.start_date = START_DATE
        self.last_trade_time = None
        self.last_daily_report = None
        self.us_holidays = holidays.US(years=range(2016, 2027))
        self.backtest_completed = False
        
        # Verifica se ja fez backtest
        if BACKTEST_DONE_FILE.exists():
            print("\n✅ Backtest ja foi executado anteriormente")
            self.backtest_completed = True
            self._load_state()
        else:
            print("\n🆕 Primeira execucao - deletando dados antigos...")
            for f in [TRADES_FILE, STATE_FILE, EQUITY_FILE]:
                if f.exists():
                    f.unlink()
        
        self._send_startup_message()
    
    def _load_state(self):
        """Carrega estado salvo"""
        if STATE_FILE.exists():
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                self.paper_balance = state.get('balance', INITIAL_BALANCE)
                self.initial_balance = state.get('initial_balance', INITIAL_BALANCE)
                self.start_date = datetime.fromisoformat(state.get('start_date', START_DATE.isoformat()))
                self.last_daily_report = state.get('last_daily_report')
                if state.get('last_trade_time'):
                    self.last_trade_time = datetime.fromisoformat(state['last_trade_time'])
                print(f"✅ Estado carregado - Balance: ${self.paper_balance:,.2f}")
        
        if TRADES_FILE.exists():
            with open(TRADES_FILE, 'r') as f:
                self.all_trades = json.load(f)
                print(f"✅ {len(self.all_trades)} trades carregados")
        
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
    
    def _execute_trade(self, side, entry, stop, signal_time, notify=True):
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
        
        if notify:
            msg = f"""🟢 <b>{side}</b>

Entry: ${entry_executed:,.2f}
Stop: ${stop:,.2f}
Target: ${target:,.2f}
Size: {size:.4f} BTC

{signal_time.strftime('%d/%m %H:%M')}"""
            self.telegram.send_message(msg)
    
    def _close_position(self, exit_price, outcome, exit_time, notify=True):
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
        
        if notify:
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
        if self.backtest_completed:
            msg = f"""🤖 <b>BOT OPERACIONAL</b>

Backtest ja concluido.
Continuando operacao normal...

Balance: ${self.paper_balance:,.2f}
Trades: {len(self.all_trades)}

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        else:
            msg = f"""🚀 <b>BACKTEST MAXIMO</b>

Tentando baixar ate 10 anos de dados...
Body% ajustado para 42%

Inicio: {START_DATE.strftime('%d/%m/%Y')}
Isso pode demorar varios minutos.

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        
        self.telegram.send_message(msg)
    
    def _create_equity_chart(self):
        if not self.equity_curve:
            return None
        
        try:
            df_equity = pd.DataFrame(self.equity_curve)
            df_equity['timestamp'] = pd.to_datetime(df_equity['timestamp'])
            
            start_point = pd.DataFrame([{
                'timestamp': self.start_date,
                'balance': INITIAL_BALANCE,
                'trade_number': 0
            }])
            df_equity = pd.concat([start_point, df_equity], ignore_index=True)
            
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10))
            
            ax1.plot(df_equity['timestamp'], df_equity['balance'], 
                    linewidth=2.5, color='#2E86AB', label='Balance')
            ax1.axhline(y=INITIAL_BALANCE, color='gray', linestyle='--', 
                       alpha=0.5, linewidth=1.5, label='Inicial')
            
            ax1.fill_between(df_equity['timestamp'], INITIAL_BALANCE, df_equity['balance'], 
                           where=(df_equity['balance'] >= INITIAL_BALANCE), 
                           alpha=0.3, color='green')
            ax1.fill_between(df_equity['timestamp'], INITIAL_BALANCE, df_equity['balance'], 
                           where=(df_equity['balance'] < INITIAL_BALANCE), 
                           alpha=0.3, color='red')
            
            years = (datetime.now() - self.start_date).days / 365
            ax1.set_title(f'Equity Curve - {years:.1f} Anos ({self.start_date.strftime("%d/%m/%Y")} - {datetime.now().strftime("%d/%m/%Y")})', 
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
    
    def _analyze_by_year(self):
        """Analisa lucro/trades por ano"""
        if not self.all_trades:
            return {}
        
        years_data = defaultdict(lambda: {
            'trades': 0,
            'wins': 0,
            'losses': 0,
            'total_pnl': 0,
            'start_balance': INITIAL_BALANCE,
            'end_balance': INITIAL_BALANCE
        })
        
        current_balance = INITIAL_BALANCE
        
        for trade in self.all_trades:
            exit_time = datetime.fromisoformat(trade['exit_time'])
            year = exit_time.year
            
            if years_data[year]['trades'] == 0:
                years_data[year]['start_balance'] = current_balance
            
            years_data[year]['trades'] += 1
            if trade['pnl_usd'] > 0:
                years_data[year]['wins'] += 1
            else:
                years_data[year]['losses'] += 1
            
            years_data[year]['total_pnl'] += trade['pnl_usd']
            current_balance = trade['balance_after']
            years_data[year]['end_balance'] = current_balance
        
        return dict(sorted(years_data.items()))
    
    def _send_summary(self):
        """Envia resumo completo com analise por ano"""
        
        total_trades = len(self.all_trades)
        
        if total_trades == 0:
            self.telegram.send_message("Nenhum trade executado!")
            return
        
        wins = [t for t in self.all_trades if t['pnl_usd'] > 0]
        losses = [t for t in self.all_trades if t['pnl_usd'] <= 0]
        
        num_wins = len(wins)
        num_losses = len(losses)
        win_rate = (num_wins / total_trades * 100)
        
        total_profit = sum(t['pnl_usd'] for t in wins)
        total_loss = sum(t['pnl_usd'] for t in losses)
        net_pnl = total_profit + total_loss
        
        avg_win = (total_profit / num_wins) if num_wins > 0 else 0
        avg_loss = (total_loss / num_losses) if num_losses > 0 else 0
        
        profit_factor = abs(total_profit / total_loss) if total_loss != 0 else 0
        return_pct = ((self.paper_balance / self.initial_balance) - 1) * 100
        
        best_trade = max(self.all_trades, key=lambda x: x['pnl_usd'])
        worst_trade = min(self.all_trades, key=lambda x: x['pnl_usd'])
        
        balances = [INITIAL_BALANCE] + [t['balance_after'] for t in self.all_trades]
        running_max = pd.Series(balances).expanding().max()
        drawdowns = ((pd.Series(balances) - running_max) / running_max * 100)
        max_dd = drawdowns.min()
        
        days = (datetime.now() - self.start_date).days
        years = days / 365
        
        # Analise por ano
        years_data = self._analyze_by_year()
        
        msg = f"""📊 <b>BACKTEST COMPLETO - BODY 42%</b>
━━━━━━━━━━━━━━━━━━━━━━

📅 <b>Periodo:</b>
{self.start_date.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}
{days} dias ({years:.1f} anos)

💰 <b>Capital:</b>
• Inicial: ${self.initial_balance:,.2f}
• Final: ${self.paper_balance:,.2f}
• Return: {return_pct:+.2f}%
• PnL Total: ${net_pnl:+,.2f}

📈 <b>Performance:</b>
• <b>Total Trades: {total_trades}</b>
• Wins: {num_wins} ({win_rate:.1f}%)
• Losses: {num_losses} ({100-win_rate:.1f}%)
• Profit Factor: {profit_factor:.2f}

💵 <b>Medias:</b>
• Avg Win: ${avg_win:+,.2f}
• Avg Loss: ${avg_loss:+,.2f}
• Win/Loss Ratio: {abs(avg_win/avg_loss):.2f}:1

🏆 <b>Extremos:</b>
• Melhor Trade: ${best_trade['pnl_usd']:+,.2f}
• Pior Trade: ${worst_trade['pnl_usd']:+,.2f}
• Max Drawdown: {max_dd:.2f}%

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        
        self.telegram.send_message(msg)
        
        # Report por ano
        if years_data:
            year_msg = "📊 <b>PERFORMANCE POR ANO:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            for year, data in years_data.items():
                start_bal = data['start_balance']
                end_bal = data['end_balance']
                year_return = ((end_bal / start_bal) - 1) * 100
                win_rate_year = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
                
                year_msg += f"<b>{year}:</b>\n"
                year_msg += f"• Trades: {data['trades']} ({data['wins']}W/{data['losses']}L)\n"
                year_msg += f"• Win Rate: {win_rate_year:.1f}%\n"
                year_msg += f"• PnL: ${data['total_pnl']:+,.2f}\n"
                year_msg += f"• Return: {year_return:+.2f}%\n"
                year_msg += f"• Balance: ${start_bal:,.0f} → ${end_bal:,.0f}\n\n"
            
            self.telegram.send_message(year_msg)
        
        # Grafico
        chart_bytes = self._create_equity_chart()
        if chart_bytes:
            caption = f"""📈 <b>Equity Curve - {years:.1f} Anos</b>

Trades: {total_trades} | WR: {win_rate:.1f}% | Return: {return_pct:+.2f}%
Body%: 42% (otimizado)"""
            self.telegram.send_photo(chart_bytes, caption=caption)
    
    def run_backtest(self):
        """Executa backtest completo"""
        
        if self.backtest_completed:
            print("\n⏩ Backtest ja concluido - pulando...")
            return
        
        print("\n" + "="*80)
        print("INICIANDO BACKTEST MAXIMO (ATE 10 ANOS)")
        print("="*80)
        
        print(f"Tentando desde {START_DATE.strftime('%d/%m/%Y')}...")
        
        since = int(START_DATE.timestamp() * 1000)
        all_candles = []
        max_requests = 900  # ~90k candles (10 anos)
        request_count = 0
        
        while request_count < max_requests:
            try:
                if request_count % 20 == 0:
                    print(f"  Progresso: {len(all_candles)} candles ({request_count}/{max_requests} requests)")
                
                candles = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=500)
                
                if not candles:
                    print(f"  Fim dos dados na request {request_count}")
                    break
                
                all_candles.extend(candles)
                since = candles[-1][0] + 1
                request_count += 1
                
                if candles[-1][0] >= int(datetime.now().timestamp() * 1000):
                    print(f"  Chegou no presente!")
                    break
                
                time.sleep(0.2)
                
            except Exception as e:
                print(f"Erro na request {request_count}: {e}")
                break
        
        if not all_candles:
            print("Nenhum candle!")
            return
        
        # Atualiza data inicial com base nos dados reais
        first_candle_time = datetime.fromtimestamp(all_candles[0][0] / 1000)
        self.start_date = first_candle_time
        
        print(f"\n✅ {len(all_candles)} candles baixados")
        print(f"📅 Periodo real: {first_candle_time.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}")
        
        years_available = (datetime.now() - first_candle_time).days / 365
        print(f"📊 {years_available:.1f} anos de dados")
        
        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['sma'] = df['close'].rolling(MA_PERIOD).mean()
        df['body_pct'] = df.apply(self._calculate_body_percent, axis=1)
        df.reset_index(inplace=True)
        
        print(f"Processando {len(df)} candles...")
        
        total_candles = 0
        trading_hours = 0
        ma_turns = 0
        body_ok = 0
        triggers = 0
        
        for i in range(MA_PERIOD + 2, len(df)):
            current = df.loc[i]
            current_time = current['timestamp'].to_pydatetime()
            total_candles += 1
            
            if total_candles % 5000 == 0:
                print(f"  {total_candles}/{len(df)} | Trades: {len(self.all_trades)} | Balance: ${self.paper_balance:,.0f}")
            
            if not self._is_trading_day(current_time):
                continue
            
            if self.position:
                if self.position['side'] == 'LONG':
                    if current['low'] <= self.position['stop']:
                        self._close_position(self.position['stop'], 'STOP', current_time, notify=False)
                    elif current['high'] >= self.position['target']:
                        self._close_position(self.position['target'], 'TARGET', current_time, notify=False)
                else:
                    if current['high'] >= self.position['stop']:
                        self._close_position(self.position['stop'], 'STOP', current_time, notify=False)
                    elif current['low'] <= self.position['target']:
                        self._close_position(self.position['target'], 'TARGET', current_time, notify=False)
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
                                          next_candle['timestamp'].to_pydatetime(), notify=False)
            
            elif ma_turn == 'DOWN':
                trigger = current['low']
                stop = current['high'] + TICK_SIZE
                
                if i + 1 < len(df):
                    next_candle = df.loc[i + 1]
                    if next_candle['low'] <= trigger:
                        self._execute_trade('SHORT', trigger, stop, 
                                          next_candle['timestamp'].to_pydatetime(), notify=False)
        
        print("\n" + "="*80)
        print("BACKTEST FINALIZADO")
        print("="*80)
        print(f"Periodo: {self.start_date.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}")
        print(f"Anos: {years_available:.1f}")
        print(f"Candles: {total_candles}")
        print(f"Horario NY: {trading_hours}")
        print(f"Viradas MA: {ma_turns}")
        print(f"Body > 42%: {body_ok}")
        print(f"Triggers: {triggers}")
        print(f"TRADES: {len(self.all_trades)}")
        print(f"BALANCE: ${self.paper_balance:,.2f}")
        print(f"RETURN: {((self.paper_balance/self.initial_balance - 1)*100):+.2f}%")
        print("="*80)
        
        self._save_state()
        
        # Marca backtest como concluido
        with open(BACKTEST_DONE_FILE, 'w') as f:
            f.write(datetime.now().isoformat())
        
        self.backtest_completed = True
        
        self._send_summary()
    
    def check_new_signals(self):
        """Checa novos sinais desde ultimo trade (modo operacional)"""
        
        if not self.backtest_completed:
            return
        
        print("\n🔍 Checando novos sinais...")
        
        # Pega ultimos 100 candles
        try:
            since = int((datetime.now() - timedelta(hours=100)).timestamp() * 1000)
            candles = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=100)
            
            if not candles:
                print("Sem candles novos")
                return
            
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            df['sma'] = df['close'].rolling(MA_PERIOD).mean()
            df['body_pct'] = df.apply(self._calculate_body_percent, axis=1)
            df.reset_index(inplace=True)
            
            # Processa apenas candles novos
            for i in range(MA_PERIOD + 2, len(df)):
                current = df.loc[i]
                current_time = current['timestamp'].to_pydatetime()
                
                # Pula candles ja processados
                if self.last_trade_time and current_time <= self.last_trade_time:
                    continue
                
                if not self._is_trading_day(current_time):
                    continue
                
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
                if not ma_turn:
                    continue
                
                if current['body_pct'] < BODY_MIN_PERCENT:
                    continue
                
                if ma_turn == 'UP':
                    trigger = current['high']
                    stop = current['low'] - TICK_SIZE
                    
                    if i + 1 < len(df):
                        next_candle = df.loc[i + 1]
                        if next_candle['high'] >= trigger:
                            self._execute_trade('LONG', trigger, stop, 
                                              next_candle['timestamp'].to_pydatetime())
                            print("✅ Novo trade LONG executado!")
                
                elif ma_turn == 'DOWN':
                    trigger = current['low']
                    stop = current['high'] + TICK_SIZE
                    
                    if i + 1 < len(df):
                        next_candle = df.loc[i + 1]
                        if next_candle['low'] <= trigger:
                            self._execute_trade('SHORT', trigger, stop, 
                                              next_candle['timestamp'].to_pydatetime())
                            print("✅ Novo trade SHORT executado!")
            
            print("✅ Verificacao de sinais completa")
            
        except Exception as e:
            print(f"Erro ao checar sinais: {e}")
    
    def send_daily_report(self):
        """Envia relatorio diario (modo operacional)"""
        
        if not self.backtest_completed:
            return
        
        print("\nEnviando relatorio diario...")
        
        total_trades = len(self.all_trades)
        
        if total_trades == 0:
            return
        
        wins = [t for t in self.all_trades if t['pnl_usd'] > 0]
        losses = [t for t in self.all_trades if t['pnl_usd'] <= 0]
        
        num_wins = len(wins)
        win_rate = (num_wins / total_trades * 100)
        
        total_pnl = sum(t['pnl_usd'] for t in self.all_trades)
        return_pct = ((self.paper_balance / self.initial_balance) - 1) * 100
        
        days = (datetime.now() - self.start_date).days
        
        today = datetime.now().date()
        trades_today = [t for t in self.all_trades 
                       if datetime.fromisoformat(t['exit_time']).date() == today]
        pnl_today = sum(t['pnl_usd'] for t in trades_today)
        
        position_status = f"{self.position['side']} aberta" if self.position else "Sem posicao"
        
        msg = f"""📊 <b>RELATORIO DIARIO</b>
━━━━━━━━━━━━━━━━━━━━━━

📅 Desde: {self.start_date.strftime('%d/%m/%Y')} ({days} dias)

💰 <b>Capital:</b>
• Balance: ${self.paper_balance:,.2f}
• Return: {return_pct:+.2f}%
• PnL Total: ${total_pnl:+,.2f}

📈 <b>Performance:</b>
• Trades: {total_trades}
• Win Rate: {win_rate:.1f}%

📊 <b>Hoje:</b>
• Trades: {len(trades_today)}
• PnL: ${pnl_today:+,.2f}

🎯 <b>Status:</b> {position_status}

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        
        self.telegram.send_message(msg)
        
        self.last_daily_report = datetime.now().isoformat()
        self._save_state()

if __name__ == '__main__':
    try:
        bot = PaperTradingBot()
        
        # Roda backtest (so na primeira vez)
        bot.run_backtest()
        
        # Checa novos sinais (modo operacional)
        bot.check_new_signals()
        
        # Relatorio diario
        now = datetime.now()
        ny_now = now.astimezone(NY_TZ)
        
        if ny_now.hour == REPORT_HOUR_NY and ny_now.minute >= REPORT_MINUTE_NY:
            if bot.last_daily_report:
                last_date = datetime.fromisoformat(bot.last_daily_report).date()
                if last_date != now.date():
                    bot.send_daily_report()
            else:
                bot.send_daily_report()
        
        print("\n✅ EXECUCAO COMPLETA!")
        
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()

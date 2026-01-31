"""
Bot de Paper Trading - BTC/USDT - MEXC
COMPARACAO: LONG ONLY 4H - COM e SEM Body% 45%
- Roda os 2 cenarios automaticamente
- Envia relatorio comparativo
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
TIMEFRAME = '4h'
MA_PERIOD = 8
RR_RATIO = 2.1
COOLDOWN_HOURS = 0  # SEM COOLDOWN
RISK_PER_TRADE = 0.02
LEVERAGE = 2.5
TAKER_FEE = 0.0004
SLIPPAGE = 0.0002
INITIAL_BALANCE = 10000

START_DATE = datetime.now() - timedelta(days=1825)

NY_TZ = pytz.timezone('America/New_York')
TICK_SIZE = 0.1

DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)

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
            print(f"Erro: {e}")
            return None
    
    def send_photo(self, photo_bytes, caption=""):
        try:
            url = f"{self.base_url}/sendPhoto"
            files = {'photo': photo_bytes}
            data = {'chat_id': self.chat_id, 'caption': caption, 'parse_mode': 'HTML'}
            response = requests.post(url, files=files, data=data, timeout=30)
            return response.json()
        except:
            return None

class LongOnlyBacktest:
    """Classe para rodar backtest LONG ONLY"""
    
    def __init__(self, df, with_body_filter=False):
        self.df = df
        self.with_body_filter = with_body_filter
        self.body_min = 45 if with_body_filter else 0
        
        self.paper_balance = INITIAL_BALANCE
        self.initial_balance = INITIAL_BALANCE
        self.position = None
        self.all_trades = []
        self.equity_curve = []
        self.last_trade_time = None
    
    def _detect_ma_turn(self, df, index):
        if index < 2:
            return None
        
        ma_prev2 = df.loc[index - 2, 'sma']
        ma_prev1 = df.loc[index - 1, 'sma']
        ma_curr = df.loc[index, 'sma']
        
        if pd.isna(ma_prev2) or pd.isna(ma_prev1) or pd.isna(ma_curr):
            return None
        
        # APENAS LONG
        if ma_prev1 < ma_prev2 and ma_curr > ma_prev1:
            return 'UP'
        
        return None
    
    def _calculate_body_percent(self, row):
        range_size = row['high'] - row['low']
        if range_size == 0:
            return 0
        body_size = abs(row['close'] - row['open'])
        return (body_size / range_size) * 100
    
    def _calculate_position_size(self, entry, stop):
        risk_usd = self.paper_balance * RISK_PER_TRADE
        risk_per_btc = abs(entry - stop)
        if risk_per_btc == 0:
            return 0
        return (risk_usd / risk_per_btc) * LEVERAGE
    
    def _execute_trade(self, entry, stop, signal_time):
        entry_executed = entry * (1 + SLIPPAGE)
        
        risk_distance = abs(entry_executed - stop)
        target = entry_executed + (risk_distance * RR_RATIO)
        
        size = self._calculate_position_size(entry_executed, stop)
        entry_fee = (size * entry_executed / LEVERAGE) * TAKER_FEE
        self.paper_balance -= entry_fee
        
        self.position = {
            'entry': entry_executed,
            'stop': stop,
            'target': target,
            'size': size,
            'entry_time': signal_time.isoformat(),
            'entry_fee': entry_fee
        }
    
    def _close_position(self, exit_price, outcome, exit_time):
        if not self.position:
            return
        
        exit_executed = exit_price * (1 - SLIPPAGE)
        pnl_gross = (exit_executed - self.position['entry']) * self.position['size']
        
        exit_fee = (self.position['size'] * exit_executed / LEVERAGE) * TAKER_FEE
        pnl_net = pnl_gross - self.position['entry_fee'] - exit_fee
        
        self.paper_balance += pnl_net
        
        entry_dt = datetime.fromisoformat(self.position['entry_time'])
        duration_hours = (exit_time - entry_dt).total_seconds() / 3600
        
        trade = {
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
        
        self.equity_curve.append({
            'timestamp': exit_time.isoformat(),
            'balance': self.paper_balance,
            'trade_number': len(self.all_trades)
        })
        
        self.position = None
    
    def run(self):
        """Executa o backtest"""
        print(f"\n{'='*80}")
        print(f"LONG ONLY 4H - {'COM Body% 45%' if self.with_body_filter else 'SEM Body%'}")
        print(f"{'='*80}")
        
        for i in range(MA_PERIOD + 2, len(self.df)):
            current = self.df.loc[i]
            current_time = current['timestamp'].to_pydatetime()
            
            if i % 500 == 0:
                print(f"  {i}/{len(self.df)} | {len(self.all_trades)} trades | ${self.paper_balance:,.0f}")
            
            # Gerencia posicao
            if self.position:
                if current['low'] <= self.position['stop']:
                    self._close_position(self.position['stop'], 'STOP', current_time)
                elif current['high'] >= self.position['target']:
                    self._close_position(self.position['target'], 'TARGET', current_time)
                continue
            
            # Detecta virada
            ma_turn = self._detect_ma_turn(self.df, i)
            if not ma_turn:
                continue
            
            # Filtro de Body%
            if self.with_body_filter:
                body_pct = self._calculate_body_percent(current)
                if body_pct < self.body_min:
                    continue
            
            # LONG
            trigger = current['high']
            stop = current['low'] - TICK_SIZE
            
            if i + 1 < len(self.df):
                next_candle = self.df.loc[i + 1]
                if next_candle['high'] >= trigger:
                    self._execute_trade(trigger, stop, next_candle['timestamp'].to_pydatetime())
        
        print(f"\n✅ Trades: {len(self.all_trades)}")
        print(f"✅ Balance: ${self.paper_balance:,.2f}")
        print(f"✅ Return: {((self.paper_balance/INITIAL_BALANCE - 1)*100):+.2f}%")
        
        return self.get_stats()
    
    def get_stats(self):
        """Retorna estatisticas"""
        if not self.all_trades:
            return None
        
        wins = [t for t in self.all_trades if t['pnl_usd'] > 0]
        losses = [t for t in self.all_trades if t['pnl_usd'] <= 0]
        
        total_profit = sum(t['pnl_usd'] for t in wins)
        total_loss = sum(t['pnl_usd'] for t in losses)
        
        balances = [INITIAL_BALANCE] + [t['balance_after'] for t in self.all_trades]
        running_max = pd.Series(balances).expanding().max()
        drawdowns = ((pd.Series(balances) - running_max) / running_max * 100)
        max_dd = drawdowns.min()
        
        return {
            'total_trades': len(self.all_trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': (len(wins) / len(self.all_trades) * 100) if self.all_trades else 0,
            'final_balance': self.paper_balance,
            'return_pct': ((self.paper_balance / INITIAL_BALANCE) - 1) * 100,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'profit_factor': abs(total_profit / total_loss) if total_loss != 0 else 0,
            'avg_win': (total_profit / len(wins)) if wins else 0,
            'avg_loss': (total_loss / len(losses)) if losses else 0,
            'max_dd': max_dd,
            'equity_curve': self.equity_curve,
            'trades': self.all_trades
        }

class ComparativeBot:
    """Bot que roda os 2 cenarios e compara"""
    
    def __init__(self):
        print("="*80)
        print("COMPARACAO: LONG ONLY 4H - COM e SEM Body%")
        print("="*80)
        
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            raise ValueError("Telegram nao configurado!")
        
        self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        
        print("\nConectando MEXC...")
        self.exchange = ccxt.mexc({
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        print("✅ Conectado")
        
        self.start_date = START_DATE
        
        self._send_startup_message()
    
    def _send_startup_message(self):
        msg = f"""🚀 <b>COMPARACAO - LONG ONLY 4H</b>

📊 <b>Cenarios:</b>
1️⃣ SEM restricao de Body%
2️⃣ COM Body% ≥ 45%

⏰ TF: 4h
⏳ Cooldown: SEM
🕐 Horario: 24/7
📊 Direcao: LONG ONLY

Baixando dados...

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        
        self.telegram.send_message(msg)
    
    def download_data(self):
        """Baixa dados historicos"""
        print("\nBaixando dados...")
        
        test_dates = [
            datetime.now() - timedelta(days=3650),
            datetime.now() - timedelta(days=1825),
            datetime.now() - timedelta(days=1095),
        ]
        
        actual_start = None
        for test_date in test_dates:
            try:
                since = int(test_date.timestamp() * 1000)
                test_candles = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=1)
                if test_candles:
                    actual_start = test_date
                    print(f"✅ Dados desde: {test_date.strftime('%d/%m/%Y')}")
                    break
            except:
                continue
        
        if not actual_start:
            actual_start = datetime.now() - timedelta(days=1095)
        
        self.start_date = actual_start
        since = int(actual_start.timestamp() * 1000)
        
        all_candles = []
        max_requests = 900
        request_count = 0
        
        while request_count < max_requests:
            try:
                if request_count % 20 == 0:
                    print(f"  {len(all_candles)} candles")
                
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
            self.telegram.send_message("❌ Sem dados")
            return None
        
        first_candle = datetime.fromtimestamp(all_candles[0][0] / 1000)
        self.start_date = first_candle
        
        print(f"✅ {len(all_candles)} candles baixados")
        print(f"📅 {first_candle.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}")
        
        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['sma'] = df['close'].rolling(MA_PERIOD).mean()
        df.reset_index(inplace=True)
        
        return df
    
    def create_comparison_chart(self, stats1, stats2):
        """Cria grafico comparativo"""
        try:
            fig, axes = plt.subplots(2, 2, figsize=(18, 12))
            
            # Equity Curve - Sem Body%
            ax1 = axes[0, 0]
            if stats1 and stats1['equity_curve']:
                df1 = pd.DataFrame(stats1['equity_curve'])
                df1['timestamp'] = pd.to_datetime(df1['timestamp'])
                ax1.plot(df1['timestamp'], df1['balance'], linewidth=2.5, color='#2E86AB', label='Sem Body%')
                ax1.axhline(y=INITIAL_BALANCE, color='gray', linestyle='--', alpha=0.5)
                ax1.fill_between(df1['timestamp'], INITIAL_BALANCE, df1['balance'], 
                               where=(df1['balance'] >= INITIAL_BALANCE), alpha=0.3, color='green')
                ax1.fill_between(df1['timestamp'], INITIAL_BALANCE, df1['balance'], 
                               where=(df1['balance'] < INITIAL_BALANCE), alpha=0.3, color='red')
            ax1.set_title('Equity - SEM Body%', fontsize=14, fontweight='bold')
            ax1.set_ylabel('Balance (USD)')
            ax1.grid(alpha=0.3)
            
            # Equity Curve - Com Body%
            ax2 = axes[0, 1]
            if stats2 and stats2['equity_curve']:
                df2 = pd.DataFrame(stats2['equity_curve'])
                df2['timestamp'] = pd.to_datetime(df2['timestamp'])
                ax2.plot(df2['timestamp'], df2['balance'], linewidth=2.5, color='#FF6B35', label='Com Body 45%')
                ax2.axhline(y=INITIAL_BALANCE, color='gray', linestyle='--', alpha=0.5)
                ax2.fill_between(df2['timestamp'], INITIAL_BALANCE, df2['balance'], 
                               where=(df2['balance'] >= INITIAL_BALANCE), alpha=0.3, color='green')
                ax2.fill_between(df2['timestamp'], INITIAL_BALANCE, df2['balance'], 
                               where=(df2['balance'] < INITIAL_BALANCE), alpha=0.3, color='red')
            ax2.set_title('Equity - COM Body 45%', fontsize=14, fontweight='bold')
            ax2.set_ylabel('Balance (USD)')
            ax2.grid(alpha=0.3)
            
            # Comparacao de Metricas - Barras
            ax3 = axes[1, 0]
            metrics = ['Total Trades', 'Win Rate %', 'Return %', 'Profit Factor']
            
            if stats1 and stats2:
                values1 = [
                    stats1['total_trades'],
                    stats1['win_rate'],
                    stats1['return_pct'],
                    stats1['profit_factor']
                ]
                values2 = [
                    stats2['total_trades'],
                    stats2['win_rate'],
                    stats2['return_pct'],
                    stats2['profit_factor']
                ]
                
                x = np.arange(len(metrics))
                width = 0.35
                
                ax3.bar(x - width/2, values1, width, label='Sem Body%', color='#2E86AB', alpha=0.8)
                ax3.bar(x + width/2, values2, width, label='Com Body 45%', color='#FF6B35', alpha=0.8)
                
                ax3.set_ylabel('Valor')
                ax3.set_title('Comparacao de Metricas', fontsize=14, fontweight='bold')
                ax3.set_xticks(x)
                ax3.set_xticklabels(metrics, rotation=45, ha='right')
                ax3.legend()
                ax3.grid(alpha=0.3, axis='y')
            
            # Drawdown Comparison
            ax4 = axes[1, 1]
            
            if stats1 and stats1['equity_curve']:
                df1 = pd.DataFrame(stats1['equity_curve'])
                df1['timestamp'] = pd.to_datetime(df1['timestamp'])
                balances1 = [INITIAL_BALANCE] + [e['balance'] for e in stats1['equity_curve']]
                running_max1 = pd.Series(balances1).expanding().max()
                dd1 = ((pd.Series(balances1) - running_max1) / running_max1 * 100)
                ax4.plot(df1['timestamp'], dd1[1:], linewidth=2, color='#2E86AB', label='Sem Body%', alpha=0.7)
            
            if stats2 and stats2['equity_curve']:
                df2 = pd.DataFrame(stats2['equity_curve'])
                df2['timestamp'] = pd.to_datetime(df2['timestamp'])
                balances2 = [INITIAL_BALANCE] + [e['balance'] for e in stats2['equity_curve']]
                running_max2 = pd.Series(balances2).expanding().max()
                dd2 = ((pd.Series(balances2) - running_max2) / running_max2 * 100)
                ax4.plot(df2['timestamp'], dd2[1:], linewidth=2, color='#FF6B35', label='Com Body 45%', alpha=0.7)
            
            ax4.set_title('Drawdown Comparison', fontsize=14, fontweight='bold')
            ax4.set_ylabel('Drawdown %')
            ax4.set_xlabel('Data')
            ax4.legend()
            ax4.grid(alpha=0.3)
            
            plt.tight_layout()
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            plt.close()
            
            return buf.read()
        except Exception as e:
            print(f"Erro ao criar grafico: {e}")
            return None
    
    def send_comparison(self, stats1, stats2):
        """Envia relatorio comparativo"""
        
        days = (datetime.now() - self.start_date).days
        years = days / 365
        
        # Mensagem comparativa
        msg = f"""📊 <b>COMPARACAO - LONG ONLY 4H</b>
━━━━━━━━━━━━━━━━━━━━━━

📅 {self.start_date.strftime('%d/%m/%Y')} - {datetime.now().strftime('%d/%m/%Y')}
{days} dias ({years:.1f} anos)

━━━━━━━━━━━━━━━━━━━━━━
1️⃣ <b>SEM BODY%:</b>

• Trades: {stats1['total_trades'] if stats1 else 0}
• Win Rate: {stats1['win_rate']:.1f}% ({stats1['wins']}W/{stats1['losses']}L)
• Balance: ${stats1['final_balance']:,.2f}
• Return: {stats1['return_pct']:+.2f}%
• PF: {stats1['profit_factor']:.2f}
• Max DD: {stats1['max_dd']:.2f}%
• Avg Win: ${stats1['avg_win']:+,.2f}
• Avg Loss: ${stats1['avg_loss']:+,.2f}

━━━━━━━━━━━━━━━━━━━━━━
2️⃣ <b>COM BODY% 45%:</b>

• Trades: {stats2['total_trades'] if stats2 else 0}
• Win Rate: {stats2['win_rate']:.1f}% ({stats2['wins']}W/{stats2['losses']}L)
• Balance: ${stats2['final_balance']:,.2f}
• Return: {stats2['return_pct']:+.2f}%
• PF: {stats2['profit_factor']:.2f}
• Max DD: {stats2['max_dd']:.2f}%
• Avg Win: ${stats2['avg_win']:+,.2f}
• Avg Loss: ${stats2['avg_loss']:+,.2f}

━━━━━━━━━━━━━━━━━━━━━━
🏆 <b>COMPARACAO:</b>

{'🥇 SEM Body%' if stats1['return_pct'] > stats2['return_pct'] else '🥇 COM Body 45%'} MELHOR RETURN
{'🎯 SEM Body%' if stats1['win_rate'] > stats2['win_rate'] else '🎯 COM Body 45%'} MELHOR WIN RATE
{'💪 SEM Body%' if stats1['profit_factor'] > stats2['profit_factor'] else '💪 COM Body 45%'} MELHOR PROFIT FACTOR
{'🛡️ SEM Body%' if stats1['max_dd'] > stats2['max_dd'] else '🛡️ COM Body 45%'} MENOR DRAWDOWN

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"""
        
        self.telegram.send_message(msg)
        
        # Grafico comparativo
        chart_bytes = self.create_comparison_chart(stats1, stats2)
        if chart_bytes:
            caption = f"📊 Comparacao LONG ONLY 4H\n{years:.1f} anos de dados"
            self.telegram.send_photo(chart_bytes, caption=caption)
    
    def run(self):
        """Executa os 2 backtests e compara"""
        
        # Baixa dados
        df = self.download_data()
        if df is None:
            return
        
        # Cenario 1: SEM Body%
        bt1 = LongOnlyBacktest(df, with_body_filter=False)
        stats1 = bt1.run()
        
        # Cenario 2: COM Body% 45%
        bt2 = LongOnlyBacktest(df, with_body_filter=True)
        stats2 = bt2.run()
        
        # Envia comparacao
        if stats1 and stats2:
            self.send_comparison(stats1, stats2)
        else:
            self.telegram.send_message("❌ Erro nos backtests")
        
        print("\n" + "="*80)
        print("✅ COMPARACAO COMPLETA")
        print("="*80)

if __name__ == '__main__':
    try:
        bot = ComparativeBot()
        bot.run()
        print("\n✅ COMPLETO!")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()

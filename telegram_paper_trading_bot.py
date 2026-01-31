"""
telegram_paper_trading_bot.py

Bot de Paper Trading com Notificações Telegram
- Backtest desde 01/01/2026
- Continua em modo live após backtest
- Notificações diárias às 16:10 BRT (11:10 NY)
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

# Carrega variáveis de ambiente
load_dotenv()

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Trading
SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'
MA_PERIOD = 8
BODY_MIN_PERCENT = 45
RR_RATIO = 2.1
COOLDOWN_HOURS = 12
RISK_PER_TRADE = 0.02  # 2%
LEVERAGE = 2.5

# Fees e Slippage
TAKER_FEE = 0.0004  # 0.04%
SLIPPAGE = 0.0002   # 0.02%

# Paper Trading
INITIAL_BALANCE = 10000
START_DATE = datetime(2026, 1, 1)  # Bot ficou online em 01/01/2026

# Horário de Trading (NY Time)
NY_TZ = pytz.timezone('America/New_York')
SESSION_START_HOUR = 8   # 8 AM NY
SESSION_END_HOUR = 11    # 11 AM NY (primeiras 3 horas)

# Horário de Notificação
REPORT_HOUR_NY = 11      # 11 AM NY
REPORT_MINUTE_NY = 10    # 11:10 AM NY (16:10 BRT)

# Tick size BTC
TICK_SIZE = 0.1  # 1 tick = $0.10

# Diretórios
DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)

# Arquivos
TRADES_FILE = DATA_DIR / 'telegram_trades.json'
STATE_FILE = DATA_DIR / 'telegram_state.json'
EQUITY_FILE = DATA_DIR / 'equity_curve.json'

# =============================================================================
# TELEGRAM NOTIFIER
# =============================================================================

class TelegramNotifier:
    """Gerencia notificações via Telegram"""
    
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, text: str, parse_mode: str = "HTML"):
        """Envia mensagem de texto"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': parse_mode
            }
            response = requests.post(url, data=data, timeout=10)
            return response.json()
        except Exception as e:
            print(f"❌ Erro ao enviar mensagem: {e}")
            return None
    
    def send_photo(self, photo_bytes: bytes, caption: str = ""):
        """Envia imagem"""
        try:
            url = f"{self.base_url}/sendPhoto"
            files = {'photo': photo_bytes}
            data = {
                'chat_id': self.chat_id,
                'caption': caption,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, files=files, data=data, timeout=30)
            return response.json()
        except Exception as e:
            print(f"❌ Erro ao enviar foto: {e}")
            return None

# =============================================================================
# PAPER TRADING BOT
# =============================================================================

class PaperTradingBot:
    """Bot de Paper Trading com Telegram"""
    
    def __init__(self):
        print("="*80)
        print("📱 BTC PAPER TRADING BOT")
        print("="*80)
        
        # Valida configuração
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("❌ TELEGRAM_BOT_TOKEN não configurado!")
        if not TELEGRAM_CHAT_ID:
            raise ValueError("❌ TELEGRAM_CHAT_ID não configurado!")
        
        # Telegram
        self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        
        # Exchange (apenas dados públicos)
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        
        # Estado inicial
        self.paper_balance = INITIAL_BALANCE
        self.initial_balance = INITIAL_BALANCE
        self.position = None
        self.all_trades = []
        self.equity_curve = []
        self.start_date = START_DATE
        self.last_daily_report = None
        self.last_trade_time = None
        
        # Feriados americanos
        self.us_holidays = holidays.US(years=range(2026, 2030))
        
        # Carrega estado salvo (se existir)
        self._load_state()
        
        print(f"✅ Configurado")
        print(f"💰 Balance: ${self.paper_balance:,.2f}")
        print(f"📊 Trades históricos: {len(self.all_trades)}")
        print("="*80)
    
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
                print(f"✅ Estado carregado de {STATE_FILE}")
        
        if TRADES_FILE.exists():
            with open(TRADES_FILE, 'r') as f:
                self.all_trades = json.load(f)
                print(f"✅ {len(self.all_trades)} trades carregados")
        
        if EQUITY_FILE.exists():
            with open(EQUITY_FILE, 'r') as f:
                self.equity_curve = json.load(f)
    
    def _save_state(self):
        """Salva estado atual"""
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
    
    def _is_trading_day(self, dt: datetime) -> bool:
        """Verifica se é dia útil de trading (sem feriados e sem fim de semana)"""
        # Fim de semana
        if dt.weekday() >= 5:  # Sábado=5, Domingo=6
            return False
        
        # Feriado americano
        date_only = dt.date()
        if date_only in self.us_holidays:
            return False
        
        return True
    
    def _is_trading_hours(self, dt: datetime) -> bool:
        """Verifica se está no horário de trading (8-11 AM NY)"""
        ny_time = dt.astimezone(NY_TZ)
        hour = ny_time.hour
        return SESSION_START_HOUR <= hour < SESSION_END_HOUR
    
    def _calculate_body_percent(self, row) -> float:
        """Calcula body% do candle"""
        range_size = row['high'] - row['low']
        if range_size == 0:
            return 0
        body_size = abs(row['close'] - row['open'])
        return (body_size / range_size) * 100
    
    def _detect_ma_turn(self, df: pd.DataFrame, index: int) -> str:
        """
        Detecta virada da média
        Retorna: 'UP', 'DOWN' ou None
        """
        if index < 2:
            return None
        
        ma_prev2 = df.loc[index - 2, 'sma']
        ma_prev1 = df.loc[index - 1, 'sma']
        ma_curr = df.loc[index, 'sma']
        
        # Virou para cima
        if ma_prev1 < ma_prev2 and ma_curr > ma_prev1:
            return 'UP'
        
        # Virou para baixo
        if ma_prev1 > ma_prev2 and ma_curr < ma_prev1:
            return 'DOWN'
        
        return None
    
    def _in_cooldown(self, current_time: datetime) -> bool:
        """Verifica se está em cooldown (12h após último trade)"""
        if self.last_trade_time is None:
            return False
        
        hours_since_last = (current_time - self.last_trade_time).total_seconds() / 3600
        return hours_since_last < COOLDOWN_HOURS
    
    def _calculate_position_size(self, entry: float, stop: float) -> float:
        """Calcula tamanho da posição baseado em risco de 2%"""
        risk_usd = self.paper_balance * RISK_PER_TRADE
        risk_per_btc = abs(entry - stop)
        
        if risk_per_btc == 0:
            return 0
        
        position_size = risk_usd / risk_per_btc
        position_size_with_leverage = position_size * LEVERAGE
        
        return position_size_with_leverage
    
    def _execute_trade(self, side: str, entry: float, stop: float, signal_time: datetime):
        """Executa entrada em uma posição"""
        
        # Aplica slippage na entrada
        if side == 'LONG':
            entry_executed = entry * (1 + SLIPPAGE)
        else:
            entry_executed = entry * (1 - SLIPPAGE)
        
        # Calcula target
        risk_distance = abs(entry_executed - stop)
        if side == 'LONG':
            target = entry_executed + (risk_distance * RR_RATIO)
        else:
            target = entry_executed - (risk_distance * RR_RATIO)
        
        # Tamanho da posição
        size = self._calculate_position_size(entry_executed, stop)
        
        # Calcula fee de entrada
        position_value = size * entry_executed / LEVERAGE
        entry_fee = position_value * TAKER_FEE
        
        # Atualiza balance com fee
        self.paper_balance -= entry_fee
        
        # Salva posição
        self.position = {
            'side': side,
            'entry': entry_executed,
            'stop': stop,
            'target': target,
            'size': size,
            'entry_time': signal_time.isoformat(),
            'entry_fee': entry_fee
        }
        
        # Notificação
        msg = self._format_entry_message(self.position)
        self.telegram.send_message(msg)
        
        print(f"\n{'🟢' if side == 'LONG' else '🔴'} Entrada {side}")
        print(f"   Entry: ${entry_executed:,.2f}")
        print(f"   Stop: ${stop:,.2f}")
        print(f"   Target: ${target:,.2f}")
        print(f"   Size: {size:.4f} BTC")
    
    def _close_position(self, exit_price: float, outcome: str, exit_time: datetime):
        """Fecha a posição atual"""
        
        if not self.position:
            return
        
        # Aplica slippage na saída
        if self.position['side'] == 'LONG':
            if outcome == 'TARGET':
                exit_executed = exit_price * (1 - SLIPPAGE)
            else:  # STOP
                exit_executed = exit_price * (1 - SLIPPAGE)
        else:  # SHORT
            if outcome == 'TARGET':
                exit_executed = exit_price * (1 + SLIPPAGE)
            else:  # STOP
                exit_executed = exit_price * (1 + SLIPPAGE)
        
        # Calcula PnL
        size = self.position['size']
        entry = self.position['entry']
        
        if self.position['side'] == 'LONG':
            pnl_gross = (exit_executed - entry) * size
        else:
            pnl_gross = (entry - exit_executed) * size
        
        # Fee de saída
        position_value = size * exit_executed / LEVERAGE
        exit_fee = position_value * TAKER_FEE
        
        # PnL líquido
        pnl_net = pnl_gross - self.position['entry_fee'] - exit_fee
        
        # Atualiza balance
        self.paper_balance += pnl_net
        
        # Duração
        entry_dt = datetime.fromisoformat(self.position['entry_time'])
        duration_hours = (exit_time - entry_dt).total_seconds() / 3600
        
        # Salva trade
        trade = {
            'side': self.position['side'],
            'entry': entry,
            'exit': exit_executed,
            'stop': self.position['stop'],
            'target': self.position['target'],
            'size': size,
            'outcome': outcome,
            'pnl_usd': pnl_net,
            'pnl_pct': (pnl_net / self.paper_balance) * 100,
            'fees_total': self.position['entry_fee'] + exit_fee,
            'balance_after': self.paper_balance,
            'entry_time': self.position['entry_time'],
            'exit_time': exit_time.isoformat(),
            'duration_hours': duration_hours
        }
        
        self.all_trades.append(trade)
        self.last_trade_time = exit_time
        
        # Equity curve
        self.equity_curve.append({
            'timestamp': exit_time.isoformat(),
            'balance': self.paper_balance,
            'trade_number': len(self.all_trades)
        })
        
        # Notificação
        msg = self._format_exit_message(trade)
        self.telegram.send_message(msg)
        
        print(f"\n{'🎯' if outcome == 'TARGET' else '🛑'} Saída: {outcome}")
        print(f"   Exit: ${exit_executed:,.2f}")
        print(f"   PnL: ${pnl_net:+,.2f} ({trade['pnl_pct']:+.2f}%)")
        print(f"   Balance: ${self.paper_balance:,.2f}")
        
        # Limpa posição
        self.position = None
        
        # Salva estado
        self._save_state()
    
    def _format_entry_message(self, position: dict) -> str:
        """Formata mensagem de entrada"""
        side_emoji = "🟢" if position['side'] == 'LONG' else "🔴"
        
        msg = f"""
{side_emoji} <b>NOVA POSIÇÃO {position['side']}</b>

📊 <b>Setup:</b>
• Entry: ${position['entry']:,.2f}
• Stop: ${position['stop']:,.2f}
• Target: ${position['target']:,.2f}
• R:R: {RR_RATIO}:1

💰 <b>Gestão:</b>
• Size: {position['size']:.4f} BTC
• Risk: {RISK_PER_TRADE*100}% (${self.paper_balance * RISK_PER_TRADE:,.2f})
• Leverage: {LEVERAGE}x
• Entry Fee: ${position['entry_fee']:.2f}

⏰ {datetime.fromisoformat(position['entry_time']).strftime('%d/%m/%Y %H:%M')}
        """
        return msg.strip()
    
    def _format_exit_message(self, trade: dict) -> str:
        """Formata mensagem de saída"""
        if trade['outcome'] == 'TARGET':
            emoji = "🎯✅"
            outcome_text = "TARGET ATINGIDO"
        else:
            emoji = "🛑"
            outcome_text = "STOP LOSS"
        
        pnl_emoji = "💚" if trade['pnl_usd'] > 0 else "❤️"
        
        msg = f"""
{emoji} <b>{outcome_text}</b>

📊 <b>Trade:</b>
• Side: {trade['side']}
• Entry: ${trade['entry']:,.2f}
• Exit: ${trade['exit']:,.2f}
• Duração: {trade['duration_hours']:.1f}h

{pnl_emoji} <b>Resultado:</b>
• PnL: ${trade['pnl_usd']:+,.2f} ({trade['pnl_pct']:+.2f}%)
• Fees: ${trade['fees_total']:.2f}
• Balance: ${trade['balance_after']:,.2f}

⏰ {datetime.fromisoformat(trade['exit_time']).strftime('%d/%m/%Y %H:%M')}
        """
        return msg.strip()
    
    def _create_equity_chart(self) -> bytes:
        """Cria gráfico da equity curve"""
        if not self.equity_curve:
            return None
        
        df_equity = pd.DataFrame(self.equity_curve)
        df_equity['timestamp'] = pd.to_datetime(df_equity['timestamp'])
        
        plt.figure(figsize=(12, 6))
        plt.plot(df_equity['timestamp'], df_equity['balance'], linewidth=2, color='#2E86AB')
        plt.axhline(y=INITIAL_BALANCE, color='gray', linestyle='--', alpha=0.5, label='Capital Inicial')
        plt.fill_between(df_equity['timestamp'], INITIAL_BALANCE, df_equity['balance'], 
                         where=(df_equity['balance'] >= INITIAL_BALANCE), alpha=0.3, color='green')
        plt.fill_between(df_equity['timestamp'], INITIAL_BALANCE, df_equity['balance'], 
                         where=(df_equity['balance'] < INITIAL_BALANCE), alpha=0.3, color='red')
        
        plt.title('Equity Curve - Paper Trading BTC', fontsize=14, fontweight='bold')
        plt.xlabel('Data')
        plt.ylabel('Balance (USD)')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        
        # Salva em bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        
        return buf.read()
    
    def _send_daily_report(self):
        """Envia relatório diário"""
        
        # Estatísticas
        total_trades = len(self.all_trades)
        wins = len([t for t in self.all_trades if t['pnl_usd'] > 0])
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        total_pnl = sum(t['pnl_usd'] for t in self.all_trades)
        total_return_pct = ((self.paper_balance / self.initial_balance) - 1) * 100
        
        # Avg win/loss
        winning_trades = [t for t in self.all_trades if t['pnl_usd'] > 0]
        losing_trades = [t for t in self.all_trades if t['pnl_usd'] <= 0]
        
        avg_win = np.mean([t['pnl_pct'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t['pnl_pct'] for t in losing_trades]) if losing_trades else 0
        
        # Trades hoje
        today = datetime.now().date()
        trades_today = [t for t in self.all_trades if datetime.fromisoformat(t['exit_time']).date() == today]
        pnl_today = sum(t['pnl_usd'] for t in trades_today)
        
        # Dias rodando
        days_running = (datetime.now() - self.start_date).days
        
        # Mensagem
        total_emoji = "📈" if total_return_pct > 0 else "📉"
        position_status = f"{self.position['side']} aberta" if self.position else "Sem posição"
        last_trade = datetime.fromisoformat(self.all_trades[-1]['exit_time']).strftime('%d/%m/%Y %H:%M') if self.all_trades else 'Nenhum'
        
        msg = f"""
📊 <b>RELATÓRIO DIÁRIO</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 <b>Capital:</b>
• Balance atual: ${self.paper_balance:,.2f}
• Balance inicial: ${self.initial_balance:,.2f}
{total_emoji} Return total: {total_return_pct:+.2f}%

📈 <b>Performance:</b>
• Total trades: {total_trades}
• Wins: {wins} ({win_rate:.1f}%)
• Losses: {losses}

💵 <b>Lucros:</b>
• PnL acumulado: ${total_pnl:+,.2f}
• Avg win: {avg_win:+.2f}%
• Avg loss: {avg_loss:+.2f}%

📊 <b>Hoje:</b>
• Trades: {len(trades_today)}
• PnL hoje: ${pnl_today:+,.2f}

🎯 <b>Status:</b>
• Posição: {position_status}
• Último trade: {last_trade}
• Dias rodando: {days_running}

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}
        """
        
        # Envia mensagem
        self.telegram.send_message(msg.strip())
        
        # Envia gráfico
        if self.equity_curve:
            chart_bytes = self._create_equity_chart()
            if chart_bytes:
                self.telegram.send_photo(chart_bytes, caption="📈 <b>Equity Curve</b>")
        
        # Atualiza último report
        self.last_daily_report = datetime.now().isoformat()
        self._save_state()
        
        print("✅ Relatório diário enviado")
    
    def run_backtest(self):
        """Executa backtest desde START_DATE até agora"""
        
        print("\n" + "="*80)
        print("🔄 INICIANDO BACKTEST")
        print("="*80)
        
        # Se já temos trades, pula backtest
        if self.all_trades:
            print(f"⚠️ Backtest já executado ({len(self.all_trades)} trades)")
            print("="*80 + "\n")
            return
        
        # Baixa dados históricos
        print(f"📥 Baixando dados de {START_DATE.strftime('%Y-%m-%d')} até agora...")
        
        since = int(START_DATE.timestamp() * 1000)
        all_candles = []
        
        while True:
            candles = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=1000)
            if not candles:
                break
            
            all_candles.extend(candles)
            since = candles[-1][0] + 1
            
            # Para quando chegar no presente
            if candles[-1][0] >= int(datetime.now().timestamp() * 1000):
                break
            
            time.sleep(self.exchange.rateLimit / 1000)
        
        # Cria DataFrame
        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        # Calcula indicadores
        df['sma'] = df['close'].rolling(MA_PERIOD).mean()
        df['body_pct'] = df.apply(self._calculate_body_percent, axis=1)
        
        df.reset_index(inplace=True)
        
        print(f"✅ {len(df)} candles baixados")
        print(f"📊 Processando sinais...")
        
        # Processa cada candle
        for i in range(MA_PERIOD + 2, len(df)):
            current = df.loc[i]
            current_time = current['timestamp'].to_pydatetime()
            
            # Verifica se é dia/horário de trading
            if not self._is_trading_day(current_time):
                continue
            
            # Verifica posição aberta
            if self.position:
                # Checa stop/target
                if self.position['side'] == 'LONG':
                    if current['low'] <= self.position['stop']:
                        self._close_position(self.position['stop'], 'STOP', current_time)
                    elif current['high'] >= self.position['target']:
                        self._close_position(self.position['target'], 'TARGET', current_time)
                else:  # SHORT
                    if current['high'] >= self.position['stop']:
                        self._close_position(self.position['stop'], 'STOP', current_time)
                    elif current['low'] <= self.position['target']:
                        self._close_position(self.position['target'], 'TARGET', current_time)
                
                continue
            
            # Não está em posição - busca sinais
            if not self._is_trading_hours(current_time):
                continue
            
            if self._in_cooldown(current_time):
                continue
            
            # Detecta virada da média
            ma_turn = self._detect_ma_turn(df, i)
            
            if ma_turn and current['body_pct'] >= BODY_MIN_PERCENT:
                
                # LONG setup
                if ma_turn == 'UP':
                    trigger = current['high']
                    stop = current['low'] - TICK_SIZE
                    
                    # Verifica se preço rompeu no próximo candle
                    if i + 1 < len(df):
                        next_candle = df.loc[i + 1]
                        if next_candle['high'] >= trigger:
                            self._execute_trade('LONG', trigger, stop, next_candle['timestamp'].to_pydatetime())
                
                # SHORT setup
                elif ma_turn == 'DOWN':
                    trigger = current['low']
                    stop = current['high'] + TICK_SIZE
                    
                    # Verifica se preço rompeu no próximo candle
                    if i + 1 < len(df):
                        next_candle = df.loc[i + 1]
                        if next_candle['low'] <= trigger:
                            self._execute_trade('SHORT', trigger, stop, next_candle['timestamp'].to_pydatetime())
        
        print(f"✅ Backtest concluído")
        print(f"📊 Total de trades: {len(self.all_trades)}")
        print(f"💰 Balance final: ${self.paper_balance:,.2f}")
        print("="*80 + "\n")
        
        # Salva tudo
        self._save_state()
    
    def check_and_report(self):
        """Verifica se deve enviar relatório diário"""
        
        now = datetime.now()
        ny_now = now.astimezone(NY_TZ)
        
        # Verifica se é hora do relatório (11:10 AM NY)
        if ny_now.hour == REPORT_HOUR_NY and ny_now.minute >= REPORT_MINUTE_NY:
            
            # Verifica se já enviou hoje
            if self.last_daily_report:
                last_report_date = datetime.fromisoformat(self.last_daily_report).date()
                if last_report_date == now.date():
                    print("✅ Relatório diário já enviado hoje")
                    return
            
            # Envia relatório
            self._send_daily_report()

# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    try:
        # Cria bot
        bot = PaperTradingBot()
        
        # Roda backtest (apenas na primeira vez)
        bot.run_backtest()
        
        # Verifica se deve enviar relatório
        bot.check_and_report()
        
        print("\n✅ Bot executado com sucesso!")
        
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()

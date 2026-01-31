"""
telegram_paper_trading_bot.py

Bot de Paper Trading com Notificações Telegram
- Usa variáveis de ambiente (.env)
- Seguro para GitHub
- Rastreamento completo desde o início
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

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

# Telegram (variáveis de ambiente)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Trading
SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'
MA_PERIOD = 8
BODY_MIN_PERCENT = 45
RR_RATIO = 2.2
COOLDOWN_BARS = 12
RISK_PER_TRADE = 0.02
LEVERAGE = 2.5

# Paper Trading
INITIAL_BALANCE = 10000

# Session NY
SESSION_START_HOUR = 8
SESSION_END_HOUR = 17
TIMING_WINDOW_PERCENT = 33.33

# Relatório diário
DAILY_REPORT_HOUR = 18  # 18h (6 PM)

# Diretórios
DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)

# Arquivos
TRADES_FILE = DATA_DIR / 'telegram_trades.json'
STATE_FILE = DATA_DIR / 'telegram_state.json'
EQUITY_FILE = DATA_DIR / 'equity_curve.json'

# =============================================================================
# TELEGRAM API
# =============================================================================

class TelegramNotifier:
    """
    Envia notificações pro Telegram
    """
    
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, text: str, parse_mode: str = "HTML"):
        """Envia mensagem texto"""
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
    
    def format_trade_entry(self, setup: dict) -> str:
        """Formata mensagem de entrada"""
        side_emoji = "🟢" if setup['side'] == 'LONG' else "🔴"
        
        msg = f"""
{side_emoji} <b>NOVA POSIÇÃO {setup['side']}</b>

📊 <b>Setup:</b>
• Entry: ${setup['entry']:,.2f}
• Stop: ${setup['stop']:,.2f}
• Target: ${setup['target']:,.2f}
• R:R: {RR_RATIO}

💰 <b>Gestão:</b>
• Size: {setup['size']:.4f} BTC
• Risk: ${setup['risk_usd']:,.2f} ({RISK_PER_TRADE*100}%)
• Leverage: {LEVERAGE}x

⏰ {setup['timestamp']}
        """
        return msg.strip()
    
    def format_trade_exit(self, trade: dict) -> str:
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
• Duração: {trade.get('duration_hours', 0):.1f}h

{pnl_emoji} <b>Resultado:</b>
• PnL: ${trade['pnl_usd']:+,.2f} ({trade['pnl_pct']:+.2f}%)
• Balance: ${trade['balance_after']:,.2f}

⏰ {trade['exit_time']}
        """
        return msg.strip()
    
    def format_daily_report(self, stats: dict) -> str:
        """Formata relatório diário"""
        
        total_emoji = "📈" if stats['total_return_pct'] > 0 else "📉"
        
        msg = f"""
📊 <b>RELATÓRIO DIÁRIO</b>
━━━━━━━━━━━━━━━━━━━━━━

💰 <b>Capital:</b>
• Balance atual: ${stats['current_balance']:,.2f}
• Balance inicial: ${stats['initial_balance']:,.2f}
{total_emoji} Return total: {stats['total_return_pct']:+.2f}%

📈 <b>Performance:</b>
• Total trades: {stats['total_trades']}
• Wins: {stats['wins']} ({stats['win_rate']:.1f}%)
• Losses: {stats['losses']}

💵 <b>Lucros:</b>
• Lucro acumulado: ${stats['total_profit_usd']:+,.2f}
• Avg win: {stats['avg_win_pct']:+.2f}%
• Avg loss: {stats['avg_loss_pct']:+.2f}%

📊 <b>Hoje:</b>
• Trades: {stats['trades_today']}
• PnL hoje: ${stats['pnl_today_usd']:+,.2f} ({stats['pnl_today_pct']:+.2f}%)

🎯 <b>Status:</b>
• Posição: {stats['position_status']}
• Último trade: {stats['last_trade_time']}
• Dias rodando: {stats['days_running']}

⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}
        """
        return msg.strip()

# =============================================================================
# BOT PRINCIPAL
# =============================================================================

class TelegramPaperTradingBot:
    """
    Bot Paper Trading com Telegram
    """
    
    def __init__(self):
        print("="*80)
        print("📱 TELEGRAM PAPER TRADING BOT")
        print("="*80)
        
        # Valida configuração
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("❌ TELEGRAM_BOT_TOKEN não configurado! Configure no arquivo .env")
        
        if not TELEGRAM_CHAT_ID:
            raise ValueError("❌ TELEGRAM_CHAT_ID não configurado! Configure no arquivo .env")
        
        # Telegram
        self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        
        # Exchange (só dados)
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        
        # Estado
        self.paper_balance = INITIAL_BALANCE
        self.initial_balance = INITIAL_BALANCE
        self.position = None
        self.last_trade_bar = -999
        self.all_trades = []
        self.equity_curve = []
        self.start_date = datetime.now()
        self.last_daily_report = None
        
        # Carrega estado
        self._load_state()
        
        # Envia mensagem de início
        self._send_startup_message()
        
        print(f"Balance: ${self.paper_balance:,.2f}")
        print(f"Telegram configurado: Chat ID {TELEGRAM_CHAT_ID}")
        print("="*80)
    
    def _load_state(self):
        """Carrega estado salvo"""
        if STATE_FILE.exists():
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                self.paper_balance = state.get('balance', INITIAL_BALANCE)
                self.initial_balance = state.get('initial_balance', INITIAL_BALANCE)
                self.last_trade_bar = state.get('last_trade_bar', -999)
                self.start_date = datetime.fromisoformat(
                    state.get('start_date', datetime.now().isoformat())
                )
                self.last_daily_report = state.get('last_daily_report')
                
                print(f"✅ Estado carregado")
        
        if TRADES_FILE.exists():
            with open(TRADES_FILE, 'r') as f:
                self.all_trades = json.load(f)
                print(f"✅ {len(self.all_trades)} trades carregados")
        
        if EQUITY_FILE.exists():
            with open(EQUITY_FILE, 'r') as f:
                self.equity_curve = json.load(f)
    
    def _save_state(self):
        """Salva estado"""
        state = {
            'balance': self.paper_balance,
            'initial_balance': self.initial_balance,
            'last_trade_bar': self.last_trade_bar,
            'start_date': self.start_date.isoformat(),
            'last_daily_report': self.last_daily_report,
            'last_update': datetime.now().isoformat()
        }
        
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        
        with open(TRADES_FILE, 'w') as f:
            json.dump(self.all_trades, f, indent=2)
        
        with open(EQUITY_FILE, 'w') as f:
            json.dump(self.equity_curve, f, indent=2)
    
    def _send_startup_message(self):
        """Mensagem de startup"""
        
        days_running = (datetime.now() - self.start_date).days
        
        msg = f"""
🚀 <b>BOT INICIADO</b>

📊 <b>Setup:</b>
• MA: SMA {MA_PERIOD}
• Body%: > {BODY_MIN_PERCENT}%
• R:R: {RR_RATIO}
• Leverage: {LEVERAGE}x

💰 <b>Capital:</b>
• Balance: ${self.paper_balance:,.2f}
• Inicial: ${self.initial_balance:,.2f}
• Return: {((self.paper_balance/self.initial_balance - 1)*100):+.2f}%

📈 <b>Histórico:</b>
• Total trades: {len(self.all_trades)}
• Dias rodando: {days_running}

✅ Bot online e monitorando...
        """
        
        self.telegram.send_message(msg.strip())
    
    def _get_ohlc(self, limit=500):
        """Busca dados"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=limit)
            df = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            print(f"❌ Erro ao buscar dados: {e}")
            return pd.DataFrame()
    
    def _calculate_indicators(self, df):
        """Calcula indicadores"""
        df['ma'] = df['close'].rolling(MA_PERIOD).mean()
        
        df['ma_turned_up'] = (
            (df['ma'] > df['ma'].shift(1)) &
            (df['ma'].shift(1) <= df['ma'].shift(2))
        )
        
        df['ma_turned_down'] = (
            (df['ma'] < df['ma'].shift(1)) &
            (df['ma'].shift(1) >= df['ma'].shift(2))
        )
        
        df['body_size'] = (df['close'] - df['open']).abs()
        df['bar_size'] = df['high'] - df['low']
        df['body_pct'] = (df['body_size'] / df['bar_size']) * 100
        
        return df
    
    def _is_ny_session(self, timestamp):
        """Checa sessão NY"""
        hour = timestamp.hour
        return SESSION_START_HOUR <= hour < SESSION_END_HOUR
    
    def _get_session_progress(self, timestamp):
        """Progresso sessão"""
        hour = timestamp.hour
        if not self._is_ny_session(timestamp):
            return 100
        
        elapsed = hour - SESSION_START_HOUR
        duration = SESSION_END_HOUR - SESSION_START_HOUR
        return (elapsed / duration) * 100
    
    def _find_setup(self, df):
        """Procura setup"""
        if len(df) < MA_PERIOD + 5:
            return None
        
        last_bar = df.iloc[-2]
        
        if not self._is_ny_session(last_bar['timestamp']):
            return None
        
        progress = self._get_session_progress(last_bar['timestamp'])
        if progress >= TIMING_WINDOW_PERCENT:
            return None
        
        bars_since = len(df) - self.last_trade_bar
        if bars_since < COOLDOWN_BARS:
            return None
        
        if last_bar['body_pct'] < BODY_MIN_PERCENT:
            return None
        
        # Setup LONG
        if last_bar['ma_turned_up']:
            return self._create_setup(
                side='LONG',
                trigger=last_bar['high'],
                stop=last_bar['low'],
                bar_index=len(df) - 2,
                timestamp=last_bar['timestamp']
            )
        
        # Setup SHORT
        if last_bar['ma_turned_down']:
            return self._create_setup(
                side='SHORT',
                trigger=last_bar['low'],
                stop=last_bar['high'],
                bar_index=len(df) - 2,
                timestamp=last_bar['timestamp']
            )
        
        return None
    
    def _create_setup(self, side, trigger, stop, bar_index, timestamp):
        """Cria setup completo"""
        
        # Position size
        risk_amount = self.paper_balance * RISK_PER_TRADE
        stop_distance_pct = abs(trigger - stop) / trigger
        position_value = (risk_amount / stop_distance_pct) * LEVERAGE
        size = position_value / trigger
        
        # Target
        risk = abs(trigger - stop)
        if side == 'LONG':
            target = trigger + (risk * RR_RATIO)
        else:
            target = trigger - (risk * RR_RATIO)
        
        return {
            'side': side,
            'entry': trigger,
            'stop': stop,
            'target': target,
            'size': size,
            'risk_usd': risk_amount,
            'bar_index': bar_index,
            'timestamp': timestamp.strftime('%d/%m/%Y %H:%M')
        }
    
    def _execute_paper_trade(self, setup):
        """Executa trade e notifica"""
        
        self.position = {
            'side': setup['side'],
            'entry': setup['entry'],
            'stop': setup['stop'],
            'target': setup['target'],
            'size': setup['size'],
            'entry_time': setup['timestamp']
        }
        
        self.last_trade_bar = setup['bar_index']
        
        # Notifica entrada
        msg = self.telegram.format_trade_entry(setup)
        self.telegram.send_message(msg)
        
        print(f"🔵 POSIÇÃO {setup['side']}: ${setup['entry']:,.2f}")
    
    def _check_position(self, current_bar):
        """Checa posição"""
        
        if not self.position:
            return
        
        side = self.position['side']
        entry = self.position['entry']
        stop = self.position['stop']
        target = self.position['target']
        
        high = current_bar['high']
        low = current_bar['low']
        
        exit_price = None
        outcome = None
        
        # LONG
        if side == 'LONG':
            if low <= stop:
                exit_price = stop
                outcome = 'STOP'
            elif high >= target:
                exit_price = target
                outcome = 'TARGET'
        
        # SHORT
        else:
            if high >= stop:
                exit_price = stop
                outcome = 'STOP'
            elif low <= target:
                exit_price = target
                outcome = 'TARGET'
        
        if exit_price:
            self._close_paper_position(
                exit_price=exit_price,
                outcome=outcome,
                exit_time=current_bar['timestamp']
            )
    
    def _close_paper_position(self, exit_price, outcome, exit_time):
        """Fecha posição e notifica"""
        
        side = self.position['side']
        entry = self.position['entry']
        
        # PnL
        if side == 'LONG':
            pnl_pct = ((exit_price - entry) / entry)
        else:
            pnl_pct = ((entry - exit_price) / entry)
        
        pnl_pct *= LEVERAGE
        pnl_pct -= 0.0008  # fees
        
        pnl_usd = self.paper_balance * pnl_pct
        self.paper_balance += pnl_usd
        
        # Duração
        entry_dt = datetime.strptime(self.position['entry_time'], '%d/%m/%Y %H:%M')
        duration_hours = (exit_time - entry_dt).total_seconds() / 3600
        
        # Trade record
        trade = {
            'entry_time': self.position['entry_time'],
            'exit_time': exit_time.strftime('%d/%m/%Y %H:%M'),
            'side': side,
            'entry': round(entry, 2),
            'exit': round(exit_price, 2),
            'pnl_usd': round(pnl_usd, 2),
            'pnl_pct': round(pnl_pct * 100, 2),
            'outcome': outcome,
            'balance_after': round(self.paper_balance, 2),
            'duration_hours': round(duration_hours, 1)
        }
        
        self.all_trades.append(trade)
        
        # Equity curve
        self.equity_curve.append({
            'timestamp': exit_time.isoformat(),
            'equity': round(self.paper_balance, 2)
        })
        
        # Notifica saída
        msg = self.telegram.format_trade_exit(trade)
        self.telegram.send_message(msg)
        
        print(f"🔴 FECHADO: {outcome} | PnL: ${pnl_usd:+,.2f}")
        
        self.position = None
        self._save_state()
    
    def _check_daily_report(self):
        """Checa se deve enviar relatório diário"""
        
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        
        # Se já enviou hoje, pula
        if self.last_daily_report == today_str:
            return
        
        # Se passou do horário
        if now.hour >= DAILY_REPORT_HOUR:
            self._send_daily_report()
            self.last_daily_report = today_str
            self._save_state()
    
    def _send_daily_report(self):
        """Envia relatório diário"""
        
        stats = self._calculate_stats()
        
        # Mensagem texto
        msg = self.telegram.format_daily_report(stats)
        self.telegram.send_message(msg)
        
        # Gráfico equity curve
        if len(self.equity_curve) > 1:
            chart_bytes = self._generate_equity_chart()
            if chart_bytes:
                self.telegram.send_photo(
                    chart_bytes,
                    caption=f"📈 Equity Curve - {len(self.all_trades)} trades"
                )
    
    def _calculate_stats(self) -> dict:
        """Calcula estatísticas"""
        
        total_trades = len(self.all_trades)
        wins = len([t for t in self.all_trades if t['pnl_usd'] > 0])
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        total_return_pct = ((self.paper_balance / self.initial_balance) - 1) * 100
        total_profit_usd = self.paper_balance - self.initial_balance
        
        # Avg win/loss
        wins_list = [t['pnl_pct'] for t in self.all_trades if t['pnl_usd'] > 0]
        losses_list = [t['pnl_pct'] for t in self.all_trades if t['pnl_usd'] <= 0]
        
        avg_win_pct = np.mean(wins_list) if wins_list else 0
        avg_loss_pct = np.mean(losses_list) if losses_list else 0
        
        # Hoje
        today_str = datetime.now().strftime('%d/%m/%Y')
        trades_today = [t for t in self.all_trades if t['exit_time'].startswith(today_str)]
        
        pnl_today_usd = sum([t['pnl_usd'] for t in trades_today])
        pnl_today_pct = (pnl_today_usd / self.initial_balance) * 100
        
        # Status
        position_status = f"{self.position['side']} @ ${self.position['entry']:,.2f}" if self.position else "FLAT"
        
        last_trade_time = self.all_trades[-1]['exit_time'] if self.all_trades else "Nenhum"
        
        days_running = (datetime.now() - self.start_date).days
        
        return {
            'current_balance': self.paper_balance,
            'initial_balance': self.initial_balance,
            'total_return_pct': total_return_pct,
            'total_profit_usd': total_profit_usd,
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'avg_win_pct': avg_win_pct,
            'avg_loss_pct': avg_loss_pct,
            'trades_today': len(trades_today),
            'pnl_today_usd': pnl_today_usd,
            'pnl_today_pct': pnl_today_pct,
            'position_status': position_status,
            'last_trade_time': last_trade_time,
            'days_running': days_running
        }
    
    def _generate_equity_chart(self) -> bytes:
        """Gera gráfico de equity curve"""
        
        try:
            dates = [datetime.fromisoformat(e['timestamp']) for e in self.equity_curve]
            equity = [e['equity'] for e in self.equity_curve]
            
            # Adiciona ponto inicial
            dates.insert(0, self.start_date)
            equity.insert(0, self.initial_balance)
            
            plt.figure(figsize=(12, 6))
            plt.plot(dates, equity, linewidth=2, color='#2ecc71')
            plt.axhline(y=self.initial_balance, color='gray', linestyle='--', alpha=0.5)
            
            plt.title('Equity Curve - Paper Trading', fontsize=16, fontweight='bold')
            plt.xlabel('Data')
            plt.ylabel('Balance ($)')
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            
            # Salva em bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100)
            buf.seek(0)
            plt.close()
            
            return buf.getvalue()
            
        except Exception as e:
            print(f"❌ Erro ao gerar gráfico: {e}")
            return None
    
    def run(self):
        """Loop principal"""
        
        print("🚀 Bot iniciado - monitorando mercado...\n")
        
        iteration = 0
        
        while True:
            try:
                iteration += 1
                
                # Busca dados
                df = self._get_ohlc(limit=500)
                if df.empty:
                    time.sleep(300)
                    continue
                
                # Indicadores
                df = self._calculate_indicators(df)
                
                # Checa posição
                if self.position:
                    current_bar = df.iloc[-1]
                    self._check_position(current_bar)
                
                # Procura setup
                else:
                    setup = self._find_setup(df)
                    if setup:
                        self._execute_paper_trade(setup)
                
                # Relatório diário
                self._check_daily_report()
                
                # Status console
                if iteration % 12 == 0:  # a cada hora
                    print(f"[{datetime.now().strftime('%d/%m %H:%M')}] "
                          f"Balance: ${self.paper_balance:,.2f} | "
                          f"Trades: {len(self.all_trades)} | "
                          f"Position: {self.position['side'] if self.position else 'FLAT'}")
                
                time.sleep(300)  # 5 min
                
            except KeyboardInterrupt:
                print("\n🛑 Bot parado")
                
                # Envia mensagem de parada
                self.telegram.send_message(
                    "🛑 <b>BOT PARADO</b>\n\n"
                    f"Balance final: ${self.paper_balance:,.2f}\n"
                    f"Total trades: {len(self.all_trades)}"
                )
                
                break
                
            except Exception as e:
                print(f"❌ Erro: {e}")
                
                # Notifica erro
                self.telegram.send_message(
                    f"⚠️ <b>ERRO NO BOT</b>\n\n"
                    f"<code>{str(e)}</code>\n\n"
                    "Bot continua rodando..."
                )
                
                time.sleep(60)

# =============================================================================
# EXECUÇÃO
# =============================================================================

if __name__ == "__main__":
    bot = TelegramPaperTradingBot()
    bot.run()

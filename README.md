# 🤖 BTC Paper Trading Bot

Bot de Paper Trading automático para Bitcoin (BTC/USDT) com notificações via Telegram.

## 📊 Estratégia

- **Ativo:** BTC/USDT (Binance Futures)
- **Timeframe:** 1 hora (H1)
- **Indicador:** SMA de 8 períodos
- **Entrada:** Rompimento da máxima/mínima do candle após virada da média
- **Body%:** Mínimo 45%
- **R:R:** 2.1:1
- **Leverage:** 2.5x
- **Risk:** 2% por trade
- **Cooldown:** 12 horas após cada trade

### 🕐 Horário de Operação

- **Horário:** 8:00 - 11:00 AM (horário de Nova York)
- **Dias:** Segunda a sexta (exceto feriados americanos)

## 🚀 Como Usar

### 1️⃣ Configurar Secrets no GitHub

1. Vá em **Settings** → **Secrets and variables** → **Actions**
2. Adicione:
   - `TELEGRAM_BOT_TOKEN`: Token do seu bot
   - `TELEGRAM_CHAT_ID`: ID do chat para receber notificações

### 2️⃣ Executar

O bot roda **automaticamente todos os dias às 16:10 BRT** (11:10 AM NY).

Você também pode rodar manualmente:
1. Vá em **Actions**
2. Selecione **Paper Trading Bot**
3. Clique em **Run workflow**

## 📈 Notificações

Você receberá via Telegram:
- ✅ Entrada em posições
- ✅ Saída de posições (stop/target)
- ✅ Relatório diário com equity curve

## 📂 Dados Salvos

Os dados ficam salvos em `/data`:
- `telegram_state.json`: Estado atual do bot
- `telegram_trades.json`: Histórico completo de trades
- `equity_curve.json`: Curva de capital

## ⚙️ Configuração

Principais parâmetros em `telegram_paper_trading_bot.py`:

```python
INITIAL_BALANCE = 10000
RISK_PER_TRADE = 0.02  # 2%
LEVERAGE = 2.5
RR_RATIO = 2.1
MA_PERIOD = 8
BODY_MIN_PERCENT = 45

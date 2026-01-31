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
2. Verifique se existem:
   - `TELEGRAM_BOT_TOKEN`: Token do seu bot (formato: `123456:ABC-DEF...`)
   - `TELEGRAM_CHAT_ID`: ID do chat (número positivo ou negativo)

### 2️⃣ Executar

O bot roda **automaticamente todos os dias às 16:10 BRT** (11:10 AM NY).

Você também pode rodar manualmente:
1. Vá em **Actions**
2. Selecione **Paper Trading Bot**
3. Clique em **Run workflow**

### 3️⃣ Verificar Logs com Debug

Os logs agora mostram:
- ✅ Se as variáveis foram carregadas
- ✅ Conteúdo das mensagens sendo enviadas
- ✅ Resposta completa da API do Telegram
- ✅ Status de cada operação

## 🧪 Testar Telegram Manualmente

Você pode rodar o teste de conexão localmente:

```bash
python test_telegram.py

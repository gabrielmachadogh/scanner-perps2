# 📱 Telegram Paper Trading Bot

Bot de paper trading automatizado com notificações Telegram para estratégia de trend following em BTC/USDT.

## 🎯 Features

- ✅ Paper trading simulado (sem risco real)
- ✅ Notificações Telegram em tempo real
- ✅ Relatório diário automático (18h)
- ✅ Rastreamento completo desde o início
- ✅ Gráfico de equity curve
- ✅ Análise de win/loss desde o dia 1

## 📊 Setup da Estratégia

- **Timeframe**: 1H
- **Ativo**: BTC/USDT
- **Sessão**: NY (08:00-17:00)
- **Janela**: Primeiras 3 horas
- **MA**: SMA 8
- **Filtro**: Body% > 45
- **R:R**: 2.2
- **Leverage**: 2.5x (paper)
- **Risk**: 2% por trade

## 🚀 Como Usar

### 1. Criar Bot no Telegram

1. Procure por `@BotFather` no Telegram
2. Envie `/newbot`
3. Escolha um nome
4. Guarde o **token** que o BotFather enviar

### 2. Pegar seu Chat ID

1. Procure por `@userinfobot` no Telegram
2. Envie qualquer mensagem
3. Guarde o **Chat ID** que ele responder

### 3. Clonar e Configurar

```bash
# Clone o repositório
git clone https://github.com/seu-usuario/trading-bot.git
cd trading-bot

# Crie ambiente virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows

# Instale dependências
pip install -r requirements.txt

# Configure suas credenciais
cp .env.example .env
nano .env  # ou use seu editor favorito

# Edite .env e coloque:
# TELEGRAM_BOT_TOKEN=seu_token_aqui
# TELEGRAM_CHAT_ID=seu_chat_id_aqui

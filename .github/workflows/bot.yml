name: Paper Trading Bot

on:
  schedule:
    # Roda todo dia às 16:10 BRT (19:10 UTC)
    - cron: '10 19 * * *'
  
  workflow_dispatch:  # Permite rodar manualmente

permissions:
  contents: write  # Permissão para fazer commit

jobs:
  run-bot:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v3
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          fetch-depth: 0
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      
      - name: Run Paper Trading Bot
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python telegram_paper_trading_bot.py
      
      - name: Commit and push data
        run: |
          git config --local user.name 'github-actions[bot]'
          git config --local user.email 'github-actions[bot]@users.noreply.github.com'
          git add data/
          git diff --quiet && git diff --staged --quiet || (git commit -m "📊 Update trading data [$(date +'%Y-%m-%d %H:%M')]" && git push)

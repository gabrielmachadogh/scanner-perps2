"""
test_telegram.py

Script simples para testar conexão com Telegram
"""

import os
from dotenv import load_dotenv
import requests
import json

load_dotenv()

print("="*80)
print("🧪 TESTE DE CONEXÃO TELEGRAM")
print("="*80 + "\n")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

print("📋 VARIÁVEIS DE AMBIENTE:")
print(f"Token presente: {'✅ Sim' if TOKEN else '❌ NÃO'}")
if TOKEN:
    print(f"Token (primeiros 20 chars): {TOKEN[:20]}...")
    print(f"Token (tamanho): {len(TOKEN)} caracteres")
else:
    print("Token: ❌ VAZIO")

print(f"\nChat ID presente: {'✅ Sim' if CHAT_ID else '❌ NÃO'}")
if CHAT_ID:
    print(f"Chat ID: {CHAT_ID}")
else:
    print("Chat ID: ❌ VAZIO")

print("\n" + "="*80)

if TOKEN and CHAT_ID:
    print("📤 ENVIANDO MENSAGEM DE TESTE...")
    print("="*80)
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {
        'chat_id': CHAT_ID,
        'text': '🧪 <b>Teste de Conexão</b>\n\nSe você recebeu esta mensagem, o bot está funcionando! ✅',
        'parse_mode': 'HTML'
    }
    
    try:
        print(f"URL: {url[:60]}...")
        print(f"Chat ID: {CHAT_ID}")
        print("\nEnviando...")
        
        response = requests.post(url, data=data, timeout=10)
        
        print(f"\n✅ Status Code: {response.status_code}")
        result = response.json()
        print(f"\nResposta completa:")
        print(json.dumps(result, indent=2))
        
        if result.get('ok'):
            print("\n" + "="*80)
            print("✅ SUCESSO! Mensagem enviada!")
            print("="*80)
        else:
            print("\n" + "="*80)
            print("❌ ERRO na resposta do Telegram:")
            print(f"Descrição: {result.get('description', 'Sem descrição')}")
            print("="*80)
            
    except Exception as e:
        print("\n" + "="*80)
        print(f"❌ EXCEÇÃO: {e}")
        print("="*80)
        import traceback
        traceback.print_exc()
else:
    print("\n" + "="*80)
    print("❌ VARIÁVEIS NÃO CONFIGURADAS!")
    print("="*80)
    print("\nConfigure no arquivo .env:")
    print("TELEGRAM_BOT_TOKEN=seu_token_aqui")
    print("TELEGRAM_CHAT_ID=seu_chat_id_aqui")
    print("="*80)

import os
from telethon import TelegramClient
from telethon.sessions import StringSession

api_id = int(os.getenv("TG_API_ID") or input("TG_API_ID: ").strip())
api_hash = os.getenv("TG_API_HASH") or input("TG_API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nTG_SESSION_STRING:\n")
    print(client.session.save())
    print("\nGuarde isso como senha. Não envie para ninguém e não cole em prints.\n")

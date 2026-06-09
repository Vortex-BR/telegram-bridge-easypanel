# Telegram Bridge EasyPanel v1.4

Bridge Python/FastAPI + Telethon para n8n.

## Endpoints

### GET /health
Verifica se o serviço está online.

### POST /consultar
Compatível com versões anteriores. Usa `COMMAND_TEMPLATE`, `FIELD_1`, `FIELD_2`, `FIELD_1_PATH` e `FIELD_2_PATH` do Environment.

### POST /executar
Novo endpoint flexível. Permite enviar qualquer comando e configurar quais campos extrair por requisição.

Exemplo:

```json
{
  "command": "/cnpj 08301891000551",
  "fields": [
    {"name": "person_name", "path": "Person>Name"},
    {"name": "person_taxid", "path": "Person>Taxid"},
    {"name": "company_phones", "path": "Phones>*"}
  ]
}
```

## Variáveis de ambiente principais

```env
TG_API_ID=
TG_API_HASH=
TG_SESSION_STRING=
BOT_USERNAME=@NeoSystemBuscas_bot
BRIDGE_TOKEN=token_forte
PORT=8000
REQUEST_TIMEOUT_SECONDS=75
POLL_INTERVAL_SECONDS=1.5
AUTO_CONFIRM_BUTTONS=true
CONFIRM_BUTTON_TEXT=Confirmar
CONFIRM_WAIT_SECONDS=18
PARSE_DEBUG=false
```


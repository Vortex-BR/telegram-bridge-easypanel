# Telegram HTML Bridge para Easypanel + n8n

Serviço Python/FastAPI que usa sua conta Telegram via Telethon para enviar comando a um bot, aguardar resposta com `.html`, extrair somente dois campos e devolver JSON para o n8n salvar no Google Sheets.

## Variáveis de ambiente

Configure no Easypanel:

```env
TG_API_ID=123456
TG_API_HASH=seu_api_hash
TG_SESSION_STRING=sua_session_string
BOT_USERNAME=@username_do_bot
COMMAND_TEMPLATE=/cnpj {cnpj_digits}
FIELD_1=nome
FIELD_2=cpf
AUTO_CONFIRM_BUTTONS=true
CONFIRM_BUTTON_TEXT=Confirmar
CONFIRM_WAIT_SECONDS=18
REQUEST_TIMEOUT_SECONDS=75
POLL_INTERVAL_SECONDS=1.5
BRIDGE_TOKEN=crie_um_token_forte_aqui
PORT=8000
```

## Como gerar TG_SESSION_STRING

No seu PC ou em um terminal seguro:

```bash
pip install telethon
TG_API_ID=123456 TG_API_HASH=seu_api_hash python generate_session.py
```

O Telegram vai pedir seu telefone e código. Copie a string gerada para o Easypanel.

## Endpoint para o n8n

POST `/consultar`

Header opcional, se `BRIDGE_TOKEN` estiver configurado:

```http
X-Bridge-Token: seu_token
```

Body:

```json
{
  "cnpj": "00.000.000/0001-00",
  "lead_name": "Nome da Empresa"
}
```

Resposta esperada:

```json
{
  "status": "FOUND",
  "nome": "valor extraido",
  "cpf": "valor extraido",
  "confirm_clicked": true,
  "confirm_button_text": "✅ Confirmar Pesquisa",
  "telegram_file_name": "arquivo.html",
  "bot": "@username_do_bot",
  "command": "/cnpj 00000000000100"
}
```

## n8n

Use um node HTTP Request:

- Method: POST
- URL: `https://SEU-SERVICO.easypanel.../consultar`
- Send Body: JSON
- Header: `X-Bridge-Token`
- Body:

```json
{
  "cnpj": "={{$json.cnpj}}",
  "lead_name": "={{$json.lead_name}}"
}
```

Depois use Google Sheets `Append or Update` para salvar `nm` e `fcp` na linha do lead.

## Bot com botão de confirmação

Se o bot pedir para clicar em um botão como `✅ Confirmar Pesquisa`, deixe estas variáveis no Easypanel:

```env
AUTO_CONFIRM_BUTTONS=true
CONFIRM_BUTTON_TEXT=Confirmar
CONFIRM_WAIT_SECONDS=18
```

O bridge vai enviar o comando, aguardar a mensagem do bot com botões, clicar no botão que contém `Confirmar` e depois continuar aguardando o `.html`.

import asyncio
import html as html_lib
import io
import os
import re
import time
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.sessions import StringSession

TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
COMMAND_TEMPLATE = os.getenv("COMMAND_TEMPLATE", "/consulta {cnpj_digits}")
FIELD_1 = os.getenv("FIELD_1", "nm")
FIELD_2 = os.getenv("FIELD_2", "fcp")
DEFAULT_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "75"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "1.5"))
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
AUTO_CONFIRM_BUTTONS = os.getenv("AUTO_CONFIRM_BUTTONS", "true").lower() in ("1", "true", "yes", "sim")
CONFIRM_BUTTON_TEXT = os.getenv("CONFIRM_BUTTON_TEXT", "Confirmar")
CONFIRM_WAIT_SECONDS = float(os.getenv("CONFIRM_WAIT_SECONDS", "18"))

if not TG_API_ID or not TG_API_HASH or not TG_SESSION_STRING or not BOT_USERNAME:
    print("[WARN] Configure TG_API_ID, TG_API_HASH, TG_SESSION_STRING e BOT_USERNAME nas variáveis de ambiente.")

app = FastAPI(title="Telegram HTML Bridge", version="1.1.0")
client = TelegramClient(StringSession(TG_SESSION_STRING), TG_API_ID, TG_API_HASH)
telegram_lock = asyncio.Lock()


class ConsultaRequest(BaseModel):
    cnpj: Optional[str] = None
    cnpj_digits: Optional[str] = None
    lead_name: Optional[str] = None
    command: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=10, le=180)


def only_digits(value: Optional[str]) -> str:
    return re.sub(r"\D+", "", value or "")


def clean_value(value: Optional[str]) -> str:
    if not value:
        return ""
    value = html_lib.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip('"\'` :;=-')


def field_patterns(field: str):
    f = re.escape(field)
    return [
        # JSON-like: "nm": "valor" ou 'nm': 'valor'
        re.compile(rf"[\"']{f}[\"']\s*[:=]\s*[\"']([^\"']+)[\"']", re.I | re.S),
        # input name="nm" value="valor"
        re.compile(rf"<input[^>]+(?:name|id)=[\"']{f}[\"'][^>]*value=[\"']([^\"']*)[\"'][^>]*>", re.I | re.S),
        # input value="valor" name="nm"
        re.compile(rf"<input[^>]+value=[\"']([^\"']*)[\"'][^>]*(?:name|id)=[\"']{f}[\"'][^>]*>", re.I | re.S),
        # tabela: <td>nm</td><td>valor</td>
        re.compile(rf"<t[dh][^>]*>\s*{f}\s*</t[dh]>\s*<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S),
        # label/span/div próximo: nm: valor
        re.compile(rf"\b{f}\b\s*[:=\-]\s*([^\n\r<|;]+)", re.I | re.S),
    ]


def extract_field(html_text: str, field: str) -> str:
    for pattern in field_patterns(field):
        match = pattern.search(html_text or "")
        if match:
            value = clean_value(match.group(1))
            if value:
                return value
    return ""


def parse_html(html_text: str) -> dict:
    return {
        FIELD_1: extract_field(html_text, FIELD_1),
        FIELD_2: extract_field(html_text, FIELD_2),
    }


async def download_message_html(message) -> tuple[str, str]:
    filename = ""
    if getattr(message, "file", None):
        filename = message.file.name or "telegram-file.html"
        bio = io.BytesIO()
        await client.download_media(message, file=bio)
        raw = bio.getvalue()
        for encoding in ("utf-8", "latin-1", "windows-1252"):
            try:
                return raw.decode(encoding, errors="strict"), filename
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore"), filename

    text = message.raw_text or ""
    if "<html" in text.lower() or FIELD_1.lower() in text.lower() or FIELD_2.lower() in text.lower():
        return text, "message-text.html"

    return "", filename


def is_html_candidate(message) -> bool:
    if getattr(message, "file", None):
        name = (message.file.name or "").lower()
        mime = (message.file.mime_type or "").lower()
        return name.endswith((".html", ".htm")) or "html" in mime
    text = (message.raw_text or "").lower()
    return "<html" in text or FIELD_1.lower() in text or FIELD_2.lower() in text


def normalize_button_text(value: str) -> str:
    value = clean_value(value or "").lower()
    value = re.sub(r"[^a-z0-9à-ÿ]+", " ", value, flags=re.I).strip()
    return value


def button_matches(text: str, target: str) -> bool:
    a = normalize_button_text(text)
    b = normalize_button_text(target)
    if not a or not b:
        return False
    return a == b or b in a


async def try_click_confirm_button(message) -> tuple[bool, str]:
    if not AUTO_CONFIRM_BUTTONS:
        return False, "auto_confirm_disabled"

    buttons = getattr(message, "buttons", None)
    if not buttons:
        return False, "no_buttons"

    for i, row in enumerate(buttons):
        for j, button in enumerate(row):
            text = getattr(button, "text", "") or ""
            if button_matches(text, CONFIRM_BUTTON_TEXT):
                await message.click(i, j)
                return True, text

    return False, "confirm_button_not_found"


@app.on_event("startup")
async def startup():
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TG_SESSION_STRING inválido ou expirado. Gere uma nova sessão.")


@app.on_event("shutdown")
async def shutdown():
    await client.disconnect()


@app.get("/health")
async def health():
    return {"ok": True, "bot": BOT_USERNAME, "fields": [FIELD_1, FIELD_2], "auto_confirm_buttons": AUTO_CONFIRM_BUTTONS, "confirm_button_text": CONFIRM_BUTTON_TEXT}


@app.post("/consultar")
async def consultar(payload: ConsultaRequest, x_bridge_token: Optional[str] = Header(default=None)):
    if BRIDGE_TOKEN and x_bridge_token != BRIDGE_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")

    cnpj_digits = only_digits(payload.cnpj_digits or payload.cnpj)
    if not cnpj_digits and not payload.command:
        raise HTTPException(status_code=400, detail="Envie cnpj/cnpj_digits ou command")

    command = payload.command or COMMAND_TEMPLATE.format(
        cnpj=payload.cnpj or cnpj_digits,
        cnpj_digits=cnpj_digits,
        lead_name=payload.lead_name or "",
    )
    timeout = payload.timeout_seconds or DEFAULT_TIMEOUT

    async with telegram_lock:
        bot = await client.get_entity(BOT_USERNAME)
        sent = await client.send_message(bot, command)
        started_at = time.time()
        deadline = started_at + timeout
        confirm_deadline = started_at + min(CONFIRM_WAIT_SECONDS, timeout)
        last_error = ""
        confirm_clicked = False
        confirm_button_text = ""
        checked_button_message_ids = set()

        while time.time() < deadline:
            # Busca apenas mensagens novas depois do comando enviado para evitar pegar HTML antigo.
            messages = await client.get_messages(bot, limit=20, min_id=sent.id)
            for msg in reversed(messages):
                # Alguns bots pedem confirmação via botão antes de enviar o HTML.
                # Ex.: "✅ Confirmar Pesquisa". O bridge clica uma única vez.
                if AUTO_CONFIRM_BUTTONS and not confirm_clicked and msg.id not in checked_button_message_ids:
                    checked_button_message_ids.add(msg.id)
                    if time.time() <= confirm_deadline:
                        try:
                            clicked, clicked_text = await try_click_confirm_button(msg)
                            if clicked:
                                confirm_clicked = True
                                confirm_button_text = clicked_text
                                # Dá um pequeno tempo para o bot processar o callback.
                                await asyncio.sleep(1.0)
                                break
                        except Exception as exc:
                            last_error = f"Erro ao clicar no botão de confirmação: {exc}"

                if not is_html_candidate(msg):
                    continue
                html_text, filename = await download_message_html(msg)
                if not html_text:
                    continue
                parsed = parse_html(html_text)
                status = "FOUND" if parsed.get(FIELD_1) and parsed.get(FIELD_2) else "PARTIAL"
                return {
                    "status": status,
                    "command": command,
                    "bot": BOT_USERNAME,
                    "telegram_message_id": msg.id,
                    "telegram_file_name": filename,
                    "confirm_clicked": confirm_clicked,
                    "confirm_button_text": confirm_button_text,
                    "elapsed_seconds": round(time.time() - started_at, 2),
                    **parsed,
                    "error": "" if status == "FOUND" else f"HTML encontrado, mas não foi possível extrair todos os campos: {FIELD_1}, {FIELD_2}",
                }

            await asyncio.sleep(POLL_INTERVAL)

        return {
            "status": "TIMEOUT",
            "command": command,
            "bot": BOT_USERNAME,
            FIELD_1: "",
            FIELD_2: "",
            "telegram_file_name": "",
            "confirm_clicked": confirm_clicked,
            "confirm_button_text": confirm_button_text,
            "elapsed_seconds": round(time.time() - started_at, 2),
            "error": last_error or f"Nenhum HTML recebido em {timeout}s",
        }

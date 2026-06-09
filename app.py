import asyncio
import html as html_lib
import io
import os
import re
import time
import unicodedata
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.sessions import StringSession

TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
COMMAND_TEMPLATE = os.getenv("COMMAND_TEMPLATE", "/cnpj {cnpj_digits}")
FIELD_1 = os.getenv("FIELD_1", "nome")
FIELD_2 = os.getenv("FIELD_2", "cpf")
FIELD_1_ALIASES = os.getenv("FIELD_1_ALIASES", "")
FIELD_2_ALIASES = os.getenv("FIELD_2_ALIASES", "")
FIELD_1_PATH = os.getenv("FIELD_1_PATH", "")
FIELD_2_PATH = os.getenv("FIELD_2_PATH", "")
DEFAULT_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "75"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "1.5"))
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
AUTO_CONFIRM_BUTTONS = os.getenv("AUTO_CONFIRM_BUTTONS", "true").lower() in ("1", "true", "yes", "sim")
CONFIRM_BUTTON_TEXT = os.getenv("CONFIRM_BUTTON_TEXT", "Confirmar")
CONFIRM_WAIT_SECONDS = float(os.getenv("CONFIRM_WAIT_SECONDS", "18"))
PARSE_DEBUG = os.getenv("PARSE_DEBUG", "false").lower() in ("1", "true", "yes", "sim")

if not TG_API_ID or not TG_API_HASH or not TG_SESSION_STRING or not BOT_USERNAME:
    print("[WARN] Configure TG_API_ID, TG_API_HASH, TG_SESSION_STRING e BOT_USERNAME nas variáveis de ambiente.")

app = FastAPI(title="Telegram HTML Bridge", version="1.4.0")
client = TelegramClient(StringSession(TG_SESSION_STRING), TG_API_ID, TG_API_HASH)
telegram_lock = asyncio.Lock()


class ConsultaRequest(BaseModel):
    cnpj: Optional[str] = None
    cnpj_digits: Optional[str] = None
    lead_name: Optional[str] = None
    command: Optional[str] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=10, le=180)


class FieldSpec(BaseModel):
    name: str
    path: Optional[str] = None
    aliases: Optional[list[str] | str] = None
    regex: Optional[str] = None
    multiple: Optional[bool] = False


class ExecuteRequest(BaseModel):
    command: str
    fields: list[FieldSpec] = Field(default_factory=list)
    timeout_seconds: Optional[int] = Field(default=None, ge=10, le=180)
    auto_confirm_buttons: Optional[bool] = None
    confirm_button_text: Optional[str] = None
    confirm_wait_seconds: Optional[float] = Field(default=None, ge=0, le=60)
    request_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


def only_digits(value: Optional[str]) -> str:
    return re.sub(r"\D+", "", value or "")


def clean_value(value: Optional[str]) -> str:
    if not value:
        return ""
    value = html_lib.unescape(str(value))
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip('"\'` :;=-')


def normalize_key(value: str) -> str:
    value = html_lib.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    return value


def split_aliases(field: str, aliases: str | list[str] | None) -> list[str]:
    base: list[str] = [field]
    if aliases:
        if isinstance(aliases, list):
            base.extend([str(a).strip() for a in aliases if str(a).strip()])
        else:
            base.extend([a.strip() for a in str(aliases).split(",") if a.strip()])
    nf = normalize_key(field)
    if nf in ("nome", "nm", "name", "person name", "person nome"):
        base.extend(["nome", "nm", "name", "nome completo", "nome do proprietario", "nome do socio", "socio", "administrador", "responsavel", "representante"])
    if nf in ("cpf", "fcp", "documento", "taxid"):
        base.extend(["cpf", "fcp", "taxid", "documento", "cpf do socio", "cpf do proprietario", "cpf responsavel", "cpf administrador"])
    if nf in ("telefone", "telefones", "phone", "phones", "celular", "whatsapp"):
        base.extend(["telefone", "telefones", "phone", "phones", "celular", "whatsapp", "number", "numero"])
    out = []
    seen = set()
    for a in base:
        na = normalize_key(a)
        if na and na not in seen:
            out.append(a)
            seen.add(na)
    return out


def alias_matches(label: str, alias: str) -> bool:
    l = normalize_key(label)
    a = normalize_key(alias)
    if not l or not a:
        return False
    if l == a:
        return True
    l_words = set(l.split())
    a_words = set(a.split())
    return a in l or (a_words and a_words.issubset(l_words))


def html_to_text(html_text: str) -> str:
    txt = html_text or ""
    txt = re.sub(r"<script[\s\S]*?</script>", " ", txt, flags=re.I)
    txt = re.sub(r"<style[\s\S]*?</style>", " ", txt, flags=re.I)
    txt = re.sub(r"</(tr|td|th|div|p|li|br|h\d)>", "\n", txt, flags=re.I)
    txt = re.sub(r"<(br|hr)\s*/?>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html_lib.unescape(txt)
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    txt = re.sub(r"\n\s+", "\n", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    return txt.strip()


def extract_table_pairs(html_text: str) -> list[tuple[str, str]]:
    pairs = []
    for tr in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html_text or "", flags=re.I):
        cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", tr, flags=re.I)
        cleaned = [clean_value(c) for c in cells]
        cleaned = [c for c in cleaned if c]
        if len(cleaned) >= 2:
            pairs.append((cleaned[0], cleaned[1]))
            if len(cleaned) >= 4:
                pairs.append((cleaned[2], cleaned[3]))
    return pairs


def extract_text_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for line in (text or "").splitlines():
        line = clean_value(line)
        if not line:
            continue
        m = re.match(r"^(.{1,70}?)(?:\s*[:=]\s*|\s+-\s+)(.{1,250})$", line)
        if m:
            key = clean_value(m.group(1))
            val = clean_value(m.group(2))
            if key and val:
                pairs.append((key, val))
    return pairs


def extract_json_like(html_text: str, aliases: list[str]) -> tuple[str, str]:
    for alias in aliases:
        f = re.escape(alias)
        patterns = [
            re.compile(rf"[\"']{f}[\"']\s*[:=]\s*[\"']([^\"']+)[\"']", re.I | re.S),
            re.compile(rf"<input[^>]+(?:name|id)=[\"'][^\"']*{f}[^\"']*[\"'][^>]*value=[\"']([^\"']*)[\"'][^>]*>", re.I | re.S),
            re.compile(rf"<input[^>]+value=[\"']([^\"']*)[\"'][^>]*(?:name|id)=[\"'][^\"']*{f}[^\"']*[\"'][^>]*>", re.I | re.S),
        ]
        for pattern in patterns:
            match = pattern.search(html_text or "")
            if match:
                value = clean_value(match.group(1))
                if value:
                    return value, alias
    return "", ""


def extract_section_rows(html_text: str) -> list[dict]:
    rows: list[dict] = []
    current_section = ""
    token_re = re.compile(
        r'<div[^>]+class=["\'][^"\']*\bsec\b[^"\']*["\'][^>]*>([\s\S]*?)</div>|'
        r'<div[^>]+class=["\'][^"\']*\brow\b[^"\']*["\'][^>]*>([\s\S]*?)</div>',
        re.I,
    )
    for match in token_re.finditer(html_text or ""):
        sec_html, row_html = match.group(1), match.group(2)
        if sec_html is not None:
            current_section = clean_value(sec_html)
            continue
        if row_html is None:
            continue
        key_match = re.search(r'<span[^>]+class=["\'][^"\']*\bk\b[^"\']*["\'][^>]*>([\s\S]*?)</span>', row_html, flags=re.I)
        val_match = re.search(r'<span[^>]+class=["\'][^"\']*\bv\b[^"\']*["\'][^>]*>([\s\S]*?)</span>', row_html, flags=re.I)
        if key_match and val_match:
            rows.append({
                "section": current_section,
                "key": clean_value(key_match.group(1)),
                "value": clean_value(val_match.group(1)),
            })
    return rows


def parse_path(path: str) -> tuple[str, str]:
    value = clean_value(path or "")
    if not value:
        return "", ""
    parts = re.split(r"\s*(?:->|>|/|\.)\s*", value, maxsplit=1)
    if len(parts) != 2:
        return "", ""
    return parts[0].strip(), parts[1].strip()


def extract_by_path(html_text: str, path: str, multiple: bool = False) -> tuple[Any, str]:
    section, key = parse_path(path)
    if not section or not key:
        return ([], "") if multiple else ("", "")
    ns = normalize_key(section)
    nk = normalize_key(key)
    values = []
    label = ""
    for row in extract_section_rows(html_text):
        if normalize_key(row.get("section", "")) == ns and (nk == "*" or normalize_key(row.get("key", "")) == nk):
            value = clean_value(row.get("value", ""))
            if value:
                values.append(value)
                label = f"{row.get('section')}->{row.get('key')}"
                if not multiple:
                    return value, label
    if multiple:
        return values, label or f"{section}->{key}"
    return "", ""


def extract_by_alias(html_text: str, field: str, aliases_env: str | list[str] | None, multiple: bool = False) -> tuple[Any, str]:
    aliases = split_aliases(field, aliases_env)
    value, matched = extract_json_like(html_text, aliases)
    if value:
        return value, matched

    found = []
    matched_key = ""
    for key, val in extract_table_pairs(html_text):
        for alias in aliases:
            if alias_matches(key, alias):
                if multiple:
                    found.append(val)
                    matched_key = key
                    break
                return val, key

    text = html_to_text(html_text)
    for key, val in extract_text_pairs(text):
        for alias in aliases:
            if alias_matches(key, alias):
                if multiple:
                    found.append(val)
                    matched_key = key
                    break
                return val, key

    if multiple and found:
        return found, matched_key

    for alias in aliases:
        f = re.escape(alias)
        pattern = re.compile(rf"(?:^|[\n\r>])\s*[^\n\r<]{{0,50}}\b{f}\b[^\n\r<]{{0,50}}\s*[:=\-]\s*([^\n\r<|;]{{1,250}})", re.I | re.S)
        m = pattern.search(text)
        if m:
            v = clean_value(m.group(1))
            if v:
                return v, alias

    nf = normalize_key(field)
    if nf in ("cpf", "fcp", "documento", "taxid"):
        m = re.search(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|\*{2,}\d{3,}\*{2,}", text)
        if m:
            return m.group(0), "cpf_regex_fallback"

    return ([], "") if multiple else ("", "")


def extract_phones(html_text: str) -> list[dict]:
    phones = []
    current: dict[str, str] = {}
    for row in extract_section_rows(html_text):
        if normalize_key(row.get("section", "")) not in ("phones", "telefones", "phone", "telefone"):
            continue
        key = normalize_key(row.get("key", ""))
        value = clean_value(row.get("value", ""))
        if not value:
            continue
        if key == "type":
            if current.get("number") or current.get("area"):
                phones.append(current)
            current = {"type": value}
        elif key in ("area", "ddd"):
            current["area"] = only_digits(value)
        elif key in ("number", "numero", "phone", "telefone"):
            current["number"] = only_digits(value)
        else:
            current[key] = value
    if current.get("number") or current.get("area"):
        phones.append(current)

    out = []
    seen = set()
    for p in phones:
        digits = f"{p.get('area','')}{p.get('number','')}"
        if len(digits) < 8 or digits in seen:
            continue
        seen.add(digits)
        p["phone_digits"] = digits
        p["phone_display"] = f"({p.get('area','')}) {p.get('number','')}" if p.get("area") else p.get("number", "")
        out.append(p)
    return out


def apply_regex(html_text: str, regex: str, multiple: bool = False) -> tuple[Any, str]:
    text = html_to_text(html_text)
    try:
        pattern = re.compile(regex, flags=re.I | re.S)
    except re.error:
        return ([], "invalid_regex") if multiple else ("", "invalid_regex")
    if multiple:
        values = []
        for m in pattern.finditer(text):
            values.append(clean_value(m.group(1) if m.groups() else m.group(0)))
        return values, "regex"
    m = pattern.search(text)
    if m:
        return clean_value(m.group(1) if m.groups() else m.group(0)), "regex"
    return "", ""


def safe_pairs_preview(html_text: str, limit: int = 30) -> list[dict]:
    pairs = extract_table_pairs(html_text) + extract_text_pairs(html_to_text(html_text))
    out = []
    seen = set()
    for k, v in pairs:
        nk = normalize_key(k)
        if not nk or nk in seen:
            continue
        seen.add(nk)
        vv = clean_value(v)
        masked = vv[:4] + "..." if len(vv) > 8 else vv
        out.append({"key": k, "value_preview": masked})
        if len(out) >= limit:
            break
    return out


def parse_html_default(html_text: str) -> dict:
    v1, m1 = extract_by_path(html_text, FIELD_1_PATH) if FIELD_1_PATH else ("", "")
    if not v1:
        v1, m1 = extract_by_alias(html_text, FIELD_1, FIELD_1_ALIASES)

    v2, m2 = extract_by_path(html_text, FIELD_2_PATH) if FIELD_2_PATH else ("", "")
    if not v2:
        v2, m2 = extract_by_alias(html_text, FIELD_2, FIELD_2_ALIASES)

    parsed = {FIELD_1: v1, FIELD_2: v2, "matched_aliases": {FIELD_1: m1, FIELD_2: m2}}
    if PARSE_DEBUG:
        parsed["field_paths"] = {FIELD_1: FIELD_1_PATH, FIELD_2: FIELD_2_PATH}
        parsed["section_rows_preview"] = extract_section_rows(html_text)[:40]
        parsed["detected_pairs_preview"] = safe_pairs_preview(html_text)
        parsed["text_preview"] = html_to_text(html_text)[:3000]
    return parsed


def parse_html_dynamic(html_text: str, fields: list[FieldSpec]) -> dict:
    parsed: dict[str, Any] = {}
    matched: dict[str, str] = {}

    for spec in fields:
        name = spec.name.strip()
        if not name:
            continue

        nf = normalize_key(name)
        if nf in ("telefones", "telefone", "phones", "phone_list") and (not spec.path or normalize_key(spec.path) in ("phones", "phones *", "telefones")):
            phone_list = extract_phones(html_text)
            parsed[name] = "; ".join(p.get("phone_display") or p.get("phone_digits", "") for p in phone_list)
            parsed[f"{name}_list"] = phone_list
            matched[name] = "Phones section"
            continue

        value: Any = [] if spec.multiple else ""
        source = ""
        if spec.path:
            value, source = extract_by_path(html_text, spec.path, multiple=bool(spec.multiple))
        if (not value) and spec.regex:
            value, source = apply_regex(html_text, spec.regex, multiple=bool(spec.multiple))
        if not value:
            value, source = extract_by_alias(html_text, name, spec.aliases, multiple=bool(spec.multiple))
        parsed[name] = value
        matched[name] = source

    parsed["matched_aliases"] = matched
    if PARSE_DEBUG:
        parsed["section_rows_preview"] = extract_section_rows(html_text)[:50]
        parsed["detected_pairs_preview"] = safe_pairs_preview(html_text)
        parsed["text_preview"] = html_to_text(html_text)[:3000]
    return parsed


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


async def try_click_confirm_button(message, target_text: str | None = None) -> tuple[bool, str]:
    buttons = getattr(message, "buttons", None)
    if not buttons:
        return False, "no_buttons"
    target = target_text or CONFIRM_BUTTON_TEXT
    for i, row in enumerate(buttons):
        for j, button in enumerate(row):
            text = getattr(button, "text", "") or ""
            if button_matches(text, target):
                await message.click(i, j)
                return True, text
    return False, "confirm_button_not_found"


async def execute_telegram_command(command: str, timeout: int, parser_fn, auto_confirm: bool, confirm_text: str, confirm_wait: float) -> dict:
    async with telegram_lock:
        bot = await client.get_entity(BOT_USERNAME)
        sent = await client.send_message(bot, command)
        started_at = time.time()
        deadline = started_at + timeout
        confirm_deadline = started_at + min(confirm_wait, timeout)
        last_error = ""
        confirm_clicked = False
        confirm_button_text = ""
        checked_button_message_ids = set()

        while time.time() < deadline:
            messages = await client.get_messages(bot, limit=25, min_id=sent.id)
            for msg in reversed(messages):
                if auto_confirm and not confirm_clicked and msg.id not in checked_button_message_ids:
                    checked_button_message_ids.add(msg.id)
                    if time.time() <= confirm_deadline:
                        try:
                            clicked, clicked_text = await try_click_confirm_button(msg, confirm_text)
                            if clicked:
                                confirm_clicked = True
                                confirm_button_text = clicked_text
                                await asyncio.sleep(1.0)
                                break
                        except Exception as exc:
                            last_error = f"Erro ao clicar no botão de confirmação: {exc}"

                if not is_html_candidate(msg):
                    continue
                html_text, filename = await download_message_html(msg)
                if not html_text:
                    continue
                parsed = parser_fn(html_text)
                return {
                    "telegram_found_html": True,
                    "command": command,
                    "bot": BOT_USERNAME,
                    "telegram_message_id": msg.id,
                    "telegram_file_name": filename,
                    "confirm_clicked": confirm_clicked,
                    "confirm_button_text": confirm_button_text,
                    "elapsed_seconds": round(time.time() - started_at, 2),
                    **parsed,
                }
            await asyncio.sleep(POLL_INTERVAL)

        return {
            "status": "TIMEOUT",
            "telegram_found_html": False,
            "command": command,
            "bot": BOT_USERNAME,
            "telegram_file_name": "",
            "confirm_clicked": confirm_clicked,
            "confirm_button_text": confirm_button_text,
            "elapsed_seconds": round(time.time() - started_at, 2),
            "error": last_error or f"Nenhum HTML recebido em {timeout}s",
        }


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
    return {
        "ok": True,
        "version": "1.4.0",
        "bot": BOT_USERNAME,
        "fields": [FIELD_1, FIELD_2],
        "field_paths": {FIELD_1: FIELD_1_PATH, FIELD_2: FIELD_2_PATH},
        "auto_confirm_buttons": AUTO_CONFIRM_BUTTONS,
        "confirm_button_text": CONFIRM_BUTTON_TEXT,
        "parse_debug": PARSE_DEBUG,
        "endpoints": ["/consultar", "/executar"],
    }


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
    result = await execute_telegram_command(
        command=command,
        timeout=timeout,
        parser_fn=parse_html_default,
        auto_confirm=AUTO_CONFIRM_BUTTONS,
        confirm_text=CONFIRM_BUTTON_TEXT,
        confirm_wait=CONFIRM_WAIT_SECONDS,
    )
    if result.get("status") == "TIMEOUT":
        return result
    status = "FOUND" if result.get(FIELD_1) and result.get(FIELD_2) else "PARTIAL"
    return {
        "status": status,
        **result,
        "error": "" if status == "FOUND" else f"HTML encontrado, mas não foi possível extrair todos os campos: {FIELD_1}, {FIELD_2}",
    }


@app.post("/executar")
async def executar(payload: ExecuteRequest, x_bridge_token: Optional[str] = Header(default=None)):
    if BRIDGE_TOKEN and x_bridge_token != BRIDGE_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")
    command = clean_value(payload.command)
    if not command:
        raise HTTPException(status_code=400, detail="Envie command")

    timeout = payload.timeout_seconds or DEFAULT_TIMEOUT
    fields = payload.fields or []
    parser_fn = lambda html_text: parse_html_dynamic(html_text, fields)
    result = await execute_telegram_command(
        command=command,
        timeout=timeout,
        parser_fn=parser_fn,
        auto_confirm=AUTO_CONFIRM_BUTTONS if payload.auto_confirm_buttons is None else payload.auto_confirm_buttons,
        confirm_text=payload.confirm_button_text or CONFIRM_BUTTON_TEXT,
        confirm_wait=payload.confirm_wait_seconds if payload.confirm_wait_seconds is not None else CONFIRM_WAIT_SECONDS,
    )
    if result.get("status") == "TIMEOUT":
        return result

    requested_names = [f.name for f in fields]
    missing = [name for name in requested_names if not result.get(name)]
    status = "FOUND" if not missing else "PARTIAL"
    return {
        "status": status,
        "request_id": payload.request_id or "",
        "metadata": payload.metadata or {},
        **result,
        "missing_fields": missing,
        "error": "" if status == "FOUND" else f"HTML encontrado, mas faltaram campos: {', '.join(missing)}",
    }

#!/usr/bin/env python3
"""Mollo Telegram Bot — conecta @mollo_adolfo_bot con Mollo Brain."""
import asyncio
import logging
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.constants import ChatAction
from telegram.error import BadRequest

BOT_TOKEN = "8736838046:AAGgD4EH21nhbNvf7moEo8-u3swOjTs-nl4"
BRAIN_URL  = "http://localhost:8002"
MAX_LEN    = 4000  # Telegram limita a 4096 chars por mensaje

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def split_text(text: str, max_len: int = MAX_LEN) -> list[str]:
    """Divide texto largo en bloques respetando párrafos."""
    if len(text) <= max_len:
        return [text]
    parts, current = [], []
    for line in text.splitlines(keepends=True):
        if sum(len(l) for l in current) + len(line) > max_len:
            parts.append("".join(current))
            current = []
        current.append(line)
    if current:
        parts.append("".join(current))
    return parts


async def stream_from_brain(pregunta: str, session_id: str) -> str:
    """Llama a /chat/stream y devuelve la respuesta completa."""
    chunks = []
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{BRAIN_URL}/chat/stream",
            json={
                "pregunta": pregunta,
                "usar_memoria": True,
                "session_id": session_id,
            },
        ) as r:
            r.raise_for_status()
            async for chunk in r.aiter_text():
                chunks.append(chunk)
    return "".join(chunks)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola, soy *Mollo* — tu asistente ejecutivo.\n\n"
        "Escríbeme cualquier pregunta sobre finanzas, estrategia, operaciones o tu VPS.",
        parse_mode="Markdown",
    )


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BRAIN_URL}/memory/")
            data = r.json()
        convs   = len(data.get("conversaciones", []))
        learns  = data.get("aprendizajes", [])
        text = f"*Memoria de Mollo*\n\nConversaciones: {convs}\nAprendizajes: {len(learns)}"
        if learns:
            text += "\n\n*Últimos aprendizajes:*"
            for l in learns[-5:]:
                text += f"\n· [{l['tema']}] {l['insight']}"
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error al obtener memoria: {e}")


async def cmd_vps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("_Analizando VPS…_", parse_mode="Markdown")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{BRAIN_URL}/vps/resumen")
            d = r.json()
        alertas = "\n".join(d.get("alertas", []))
        text = (
            f"*Estado del VPS*\n\n"
            f"CPU: {d.get('cpu_uso_pct')}%\n"
            f"RAM: {d.get('ram_uso_pct')}% ({d.get('ram_disponible_mb')} MB libres)\n"
            f"Disco: {d.get('disco_uso_pct')}% ({d.get('disco_disponible')} libres)\n"
            f"Contenedores: {d.get('contenedores_activos')}\n\n"
            f"{alertas}"
        )
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"Error al obtener estado: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pregunta  = update.message.text.strip()
    chat_id   = str(update.effective_chat.id)
    session_id = f"telegram_{chat_id}"

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    status_msg = await update.message.reply_text("_✍️ pensando…_", parse_mode="Markdown")

    try:
        respuesta = await stream_from_brain(pregunta, session_id)
    except httpx.ConnectError:
        await status_msg.edit_text("⚠️ No puedo conectar con Mollo Brain. ¿Está corriendo en el VPS?")
        return
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Error: {e}")
        return

    partes = split_text(respuesta)

    # Primera parte reemplaza el "pensando..."
    try:
        await status_msg.edit_text(partes[0])
    except BadRequest:
        await status_msg.delete()
        await update.message.reply_text(partes[0])

    # Partes adicionales si el mensaje es muy largo
    for parte in partes[1:]:
        await update.message.reply_text(parte)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("vps",    cmd_vps))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Mollo Bot arrancado — esperando mensajes de Telegram")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Mollo Telegram Bot — conecta @mollo_adolfo_bot con Mollo Brain."""
import asyncio
import logging
import os
import tempfile
from pathlib import Path
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.constants import ChatAction
from telegram.error import BadRequest
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
BRAIN_URL  = "http://localhost:8002"
MAX_LEN    = 4000

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN no encontrado en .env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def split_text(text: str, max_len: int = MAX_LEN) -> list[str]:
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


async def call_brain(pregunta: str, session_id: str, modo: str | None = None) -> str:
    payload: dict = {"pregunta": pregunta, "usar_memoria": True, "session_id": session_id}
    if modo:
        payload["modo"] = modo

    chunks = []
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream("POST", f"{BRAIN_URL}/chat/stream", json=payload) as r:
            r.raise_for_status()
            async for chunk in r.aiter_text():
                chunks.append(chunk)
    return "".join(chunks)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola, soy *Mollo* — tu asistente ejecutivo personal.\n\n"
        "Puedo responder preguntas, buscar en internet, monitorear tu VPS y ejecutar automatizaciones.\n\n"
        "Comandos disponibles:\n"
        "/vps — estado del servidor\n"
        "/memory — resumen de memoria\n"
        "/temas — memoria por temas especializados\n"
        "/briefing — briefing ejecutivo del día\n"
        "/agente <consulta> — forzar modo agente con herramientas\n\n"
        "📸 *NanoBanana Vision:*\n"
        "Envíame una foto o video y lo analizo con NanoBanana Pro — calidad, composición, iluminación y más.",
        parse_mode="Markdown",
    )


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BRAIN_URL}/memory/")
            data = r.json()
        convs  = len(data.get("conversaciones", []))
        learns = data.get("aprendizajes", [])
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


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Genera un briefing ejecutivo del día."""
    chat_id    = str(update.effective_chat.id)
    session_id = f"telegram_{chat_id}"
    msg = await update.message.reply_text("_📋 Preparando tu briefing…_", parse_mode="Markdown")
    try:
        respuesta = await call_brain(
            "Genera mi briefing ejecutivo del día: estado del VPS, "
            "tareas y proyectos pendientes de los que tengas registro, "
            "y 3 prioridades estratégicas para hoy.",
            session_id=session_id,
            modo="agente",
        )
        partes = split_text(respuesta)
        await msg.edit_text(partes[0])
        for parte in partes[1:]:
            await update.message.reply_text(parte)
    except Exception as e:
        await msg.edit_text(f"Error generando briefing: {e}")


async def cmd_temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el resumen de la memoria por temas especializados."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BRAIN_URL}/memory/topics")
            data = r.json()

        lines = ["*Memoria por Temas*\n"]
        for key, info in data.items():
            icono = "🧠" if info["tiene_memoria"] else "⚪"
            conv = info["conversaciones_procesadas"]
            lines.append(f"{icono} *{info['nombre']}* ({conv} convs)")
            if info["tiene_memoria"]:
                lines.append(f"_{info['resumen'][:120]}…_")
                if info["pendientes"]:
                    for p in info["pendientes"][:2]:
                        lines.append(f"  ◦ {p}")
            lines.append("")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error al obtener temas: {e}")


async def cmd_agente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fuerza modo agente para la consulta — /agente <texto>"""
    chat_id    = str(update.effective_chat.id)
    session_id = f"telegram_{chat_id}"
    pregunta   = " ".join(context.args) if context.args else ""

    if not pregunta:
        await update.message.reply_text("Uso: /agente <tu consulta>")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    msg = await update.message.reply_text("_⚙️ Ejecutando agente…_", parse_mode="Markdown")
    try:
        respuesta = await call_brain(pregunta, session_id=session_id, modo="agente")
        partes = split_text(respuesta)
        try:
            await msg.edit_text(partes[0])
        except BadRequest:
            await msg.delete()
            await update.message.reply_text(partes[0])
        for parte in partes[1:]:
            await update.message.reply_text(parte)
    except Exception as e:
        await msg.edit_text(f"Error en agente: {e}")


async def _analyze_telegram_file(
    file_id: str,
    filename: str,
    suffix: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    caption: str = "",
) -> None:
    """Descarga un archivo de Telegram y lo manda a /vision/analyze."""
    chat_id = str(update.effective_chat.id)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    status_msg = await update.message.reply_text("_🔍 Analizando con NanoBanana…_", parse_mode="Markdown")

    try:
        tg_file = await context.bot.get_file(file_id)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        async with httpx.AsyncClient(timeout=120) as client:
            with open(tmp_path, "rb") as fh:
                resp = await client.post(
                    f"{BRAIN_URL}/vision/analyze",
                    files={"file": (filename, fh, _mime_for(suffix))},
                    data={"model": "models/nano-banana-pro-preview"},
                )
            resp.raise_for_status()
            data = resp.json()

        texto = _format_vision_result(data, caption)
        try:
            await status_msg.edit_text(texto, parse_mode="Markdown")
        except BadRequest:
            await status_msg.delete()
            await update.message.reply_text(texto, parse_mode="Markdown")

    except Exception as e:
        await status_msg.edit_text(f"⚠️ Error en análisis visual: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _mime_for(suffix: str) -> str:
    import mimetypes
    return mimetypes.guess_type(f"f{suffix}")[0] or "application/octet-stream"


def _format_vision_result(data: dict, caption: str = "") -> str:
    if "error" in data and "puntuacion_global" not in data:
        return f"⚠️ Error: {data['error']}"

    score  = data.get("puntuacion_global", "?")
    desc   = data.get("descripcion", "")
    dims   = data.get("dimensiones", {})
    tips   = data.get("sugerencias", [])
    tags   = data.get("etiquetas", [])
    modelo = data.get("modelo", "")

    stars = "⭐" * round(float(score)) if score != "?" else ""
    lines = [f"📸 *Análisis NanoBanana* — {score}/10 {stars}"]

    if desc:
        lines.append(f"\n_{desc}_")

    if dims:
        lines.append("\n*Dimensiones:*")
        emojis = {
            "composicion": "🖼",
            "nitidez":     "🔍",
            "iluminacion": "💡",
            "colores":     "🎨",
            "encuadre":    "📐",
            "movimiento":  "🎬",
            "audio":       "🔊",
        }
        for key, val in dims.items():
            ico = emojis.get(key, "•")
            p   = val.get("puntuacion", "?")
            c   = val.get("comentario", "")
            lines.append(f"{ico} *{key.capitalize()}* {p}/10 — {c}")

    if tips:
        lines.append("\n*Sugerencias:*")
        for t in tips[:2]:
            lines.append(f"  · {t}")

    if tags:
        lines.append("\n" + " ".join(f"`{t}`" for t in tags[:5]))

    if modelo:
        lines.append(f"\n_vía {modelo.split('/')[-1]}_")

    return "\n".join(lines)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo   = update.message.photo[-1]  # mayor resolución
    caption = update.message.caption or ""
    await _analyze_telegram_file(
        file_id=photo.file_id,
        filename="photo.jpg",
        suffix=".jpg",
        update=update,
        context=context,
        caption=caption,
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video   = update.message.video
    suffix  = Path(video.file_name or "video.mp4").suffix or ".mp4"
    caption = update.message.caption or ""
    await _analyze_telegram_file(
        file_id=video.file_id,
        filename=video.file_name or f"video{suffix}",
        suffix=suffix,
        update=update,
        context=context,
        caption=caption,
    )


async def handle_document_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fotos/videos enviados como archivo (sin compresión de Telegram)."""
    doc    = update.message.document
    suffix = Path(doc.file_name or "").suffix.lower()
    from gemini_vision import IMAGE_EXTS, VIDEO_EXTS
    if suffix not in IMAGE_EXTS | VIDEO_EXTS:
        return  # no es media — ignorar
    caption = update.message.caption or ""
    await _analyze_telegram_file(
        file_id=doc.file_id,
        filename=doc.file_name or f"file{suffix}",
        suffix=suffix,
        update=update,
        context=context,
        caption=caption,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pregunta   = update.message.text.strip()
    chat_id    = str(update.effective_chat.id)
    session_id = f"telegram_{chat_id}"

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    status_msg = await update.message.reply_text("_✍️ pensando…_", parse_mode="Markdown")

    try:
        respuesta = await call_brain(pregunta, session_id=session_id)
    except httpx.ConnectError:
        await status_msg.edit_text("⚠️ No puedo conectar con Mollo Brain. ¿Está corriendo en el VPS?")
        return
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Error: {e}")
        return

    partes = split_text(respuesta)
    try:
        await status_msg.edit_text(partes[0])
    except BadRequest:
        await status_msg.delete()
        await update.message.reply_text(partes[0])
    for parte in partes[1:]:
        await update.message.reply_text(parte)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("memory",   cmd_memory))
    app.add_handler(CommandHandler("vps",      cmd_vps))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("temas",    cmd_temas))
    app.add_handler(CommandHandler("agente",   cmd_agente))
    app.add_handler(MessageHandler(filters.PHOTO,   handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO,   handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Mollo Bot arrancado — esperando mensajes de Telegram")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

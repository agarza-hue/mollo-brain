import os
import uuid
import subprocess
import requests
import httpx
import tempfile
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from openai import OpenAI
import anthropic
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from noticias import briefing_noticias

load_dotenv("/opt/mollo-telegram/.env")

import dropbox as _dropbox
from dropbox.exceptions import AuthError as _DropboxAuthError


def _subir_dropbox(file_path: Path, categoria: str) -> str:
    """Sube el archivo a Dropbox en /Mollo/<categoria>/<filename>. Retorna ruta o error."""
    try:
        env_file = Path("/root/mollo_brain/.env")
        from dotenv import dotenv_values
        env = dotenv_values(env_file)
        dbx = _dropbox.Dropbox(
            app_key=env.get("DROPBOX_APP_KEY", ""),
            app_secret=env.get("DROPBOX_APP_SECRET", ""),
            oauth2_refresh_token=env.get("DROPBOX_REFRESH_TOKEN", ""),
        )
        dest = f"/Mollo/{categoria}/{file_path.name}"
        with open(file_path, "rb") as f:
            data = f.read()
        dbx.files_upload(data, dest, mode=_dropbox.files.WriteMode.overwrite)
        return dest
    except Exception as e:
        return f"ERROR:{e}"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mollo")

QDRANT_URL = "http://127.0.0.1:6333"
QDRANT_COLLECTION = "mollo_empresa"
EMBED_MODEL = "nomic-embed-text"
MOLLO_BRAIN_URL = "http://127.0.0.1:8002"

CATEGORIAS = [
    "financiero", "estrategia", "rrhh", "ventas",
    "operaciones", "iso9001", "contratos", "general"
]

MODEL_LABEL_TG = {
    "simple":   "GPT-4o-mini",
    "medio":    "GPT-4o",
    "complejo": "Claude Sonnet",
    "agente":   "GPT-4o + tools",
    "rapido":   "Llama 3.3 70B (Groq)",
}

# Tiers de routing que el usuario puede forzar con /modo (paridad con el CLI)
MODOS_VALIDOS = {"simple", "medio", "complejo", "agente", "rapido"}
MODOS_AUTO    = {"auto", "reset", "off", "ninguno", "none"}

TEMP_DIR = Path(tempfile.gettempdir()) / "mollo_uploads"
TEMP_DIR.mkdir(exist_ok=True)

qdrant = QdrantClient(url=QDRANT_URL)
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
claude_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── utilidades ──────────────────────────────────────────────────────────────

def run_cmd(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr)[:3500]
    except subprocess.TimeoutExpired:
        return "Timeout: el comando tardó demasiado."
    except Exception as e:
        return str(e)

def embed_text(text):
    r = requests.post(
        "http://127.0.0.1:11434/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60
    )
    r.raise_for_status()
    return r.json()["embedding"]

def chunk_text(text, size=1200, overlap=200):
    chunks, start = [], 0
    while start < len(text):
        chunk = text[start:start + size].strip()
        if chunk:
            chunks.append(chunk)
        start += size - overlap
    return chunks

def ensure_collection():
    try:
        qdrant.get_collection(QDRANT_COLLECTION)
    except Exception:
        size = len(embed_text("test"))
        qdrant.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=size, distance=Distance.COSINE)
        )

# ── memoria ──────────────────────────────────────────────────────────────────

def buscar_memoria(query, limit=3):
    try:
        vector = embed_text(query)
        hits = qdrant.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=vector,
            limit=limit
        )
        contexto = []
        for h in hits:
            source = h.payload.get("source", "sin_fuente")
            text = h.payload.get("text", "")
            contexto.append(f"[Fuente: {source}]\n{text}")
        return "\n\n".join(contexto)
    except Exception:
        return ""

def ingestar_en_memoria(texto, fuente="telegram"):
    """Embeds y guarda directamente en Qdrant sin pasos manuales."""
    ensure_collection()
    chunks = chunk_text(texto)
    points = []
    for idx, chunk in enumerate(chunks):
        vector = embed_text(chunk)
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={"source": fuente, "chunk": idx, "text": chunk}
        ))
    qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
    return len(points)

def enriquecer_conocimiento(texto):
    """Claude estructura el texto bruto en conocimiento limpio y recuperable."""
    try:
        r = claude_client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=1500,
            system=(
                "Eres un experto en gestión del conocimiento empresarial. "
                "Convierte el texto que te dan en conocimiento estructurado, claro y recuperable para una base vectorial. "
                "Extrae: hechos clave, reglas, procedimientos, fechas, nombres, datos importantes. "
                "Escribe en español limpio. Sé conciso y usa viñetas o secciones cuando ayude."
            ),
            messages=[{"role": "user", "content": f"Estructura este conocimiento:\n\n{texto}"}]
        )
        return r.content[0].text
    except Exception as e:
        return texto  # si falla, guarda el texto original

# ── IA: respuestas ────────────────────────────────────────────────────────────

def ask_mollo(text):
    memoria = buscar_memoria(text)
    prompt = f"""Eres Mollo, asistente empresarial de Adolfo.
Responde en español claro, ejecutivo y práctico.

Memoria relevante:
{memoria[:1200]}

Solicitud:
{text}
"""
    r = subprocess.run(
        ["ollama", "run", OLLAMA_MODEL, prompt],
        capture_output=True, text=True, timeout=90
    )
    return r.stdout.strip()[:3900] if r.returncode == 0 else r.stderr[:3900]

def ask_openai(text):
    try:
        memoria = buscar_memoria(text)
        r = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[
                {"role": "system", "content": f"Eres Mollo, asesor ejecutivo de Adolfo. Usa esta memoria si aplica:\n{memoria[:1200]}"},
                {"role": "user", "content": text}
            ],
        )
        return r.choices[0].message.content[:3900]
    except Exception as e:
        return f"Error OpenAI: {e}"

def ask_claude(text):
    try:
        memoria = buscar_memoria(text)
        system = (
            "Eres Mollo, asistente empresarial personal de Adolfo. "
            "Respondes en español impecable: sin errores ortográficos, sin texto cortado, sin frases incompletas. "
            "Eres ejecutivo, práctico y directo. Cubres finanzas, estrategia, RH, ventas, proyectos, ISO 9001, VPS e IA. "
            "Siempre terminas tus respuestas de forma completa, nunca a la mitad de una frase."
        )
        if memoria:
            system += f"\n\nMemoria relevante del contexto de Adolfo:\n{memoria[:1200]}"
        r = claude_client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": text}]
        )
        return r.content[0].text[:3900]
    except Exception as e:
        return f"Error Claude: {e}"

# ── VPS: interpretación de comandos ──────────────────────────────────────────

def interpretar_cmd_vps(instruccion):
    """Claude traduce lenguaje natural a un comando bash seguro."""
    try:
        r = claude_client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=300,
            system=(
                "Eres un experto en administración Linux (Ubuntu/Debian, systemd, docker, python, ollama). "
                "El usuario te da una instrucción en lenguaje natural. Responde ÚNICAMENTE con el comando bash exacto, sin explicación, sin markdown, sin comillas extra. "
                "Si el comando es ambiguo, destructivo sin confirmación explícita (rm -rf, drop, truncate), o no tiene sentido, responde: ERROR: <motivo breve>."
            ),
            messages=[{"role": "user", "content": instruccion}]
        )
        return r.content[0].text.strip()
    except Exception as e:
        return f"ERROR: {e}"

# ── status ────────────────────────────────────────────────────────────────────

def status_vps():
    return (
        "VPS STATUS\n\n"
        f"Uptime:\n{run_cmd('uptime')}\n\n"
        f"Memoria:\n{run_cmd('free -h')}\n\n"
        f"Disco:\n{run_cmd('df -h /')}\n\n"
        "Servicios:\n"
        f"  Ollama: {run_cmd('systemctl is-active ollama').strip()}\n"
        f"  Mollo Telegram: {run_cmd('systemctl is-active mollo-telegram').strip()}\n"
        f"  Mollo Autonomo: {run_cmd('systemctl is-active mollo-autonomo').strip()}\n"
        f"  Qdrant: {run_cmd('docker ps --filter name=mollo-qdrant --format {{.Status}}').strip()}"
    )

# ── archivos: descarga y procesamiento ───────────────────────────────────────

async def descargar_archivo(file_obj, filename: str) -> Path:
    """Descarga un archivo de Telegram al directorio temporal."""
    dest = TEMP_DIR / filename
    await file_obj.download_to_drive(str(dest))
    return dest


def subir_a_mollo_brain(file_path: Path, categoria: str) -> dict:
    """Envía el archivo al endpoint /docs/upload de Mollo Brain."""
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{MOLLO_BRAIN_URL}/docs/upload",
            files={"file": (file_path.name, f)},
            data={"categoria": categoria},
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json()


def analizar_con_mollo(pregunta: str, categoria: str = None) -> str:
    """Consulta Mollo Brain con RAG para análisis del documento recién subido."""
    payload = {
        "pregunta": pregunta,
        "top_k": 4,
    }
    if categoria:
        payload["categoria"] = categoria
    resp = requests.post(
        f"{MOLLO_BRAIN_URL}/chat/ask",
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("respuesta", "")


def teclado_categorias(token: str) -> InlineKeyboardMarkup:
    """Genera teclado inline con las categorías. token es clave corta en context.user_data."""
    botones = []
    fila = []
    for i, cat in enumerate(CATEGORIAS):
        fila.append(InlineKeyboardButton(cat, callback_data=f"cat|{cat}|{token}"))
        if len(fila) == 2:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    return InlineKeyboardMarkup(botones)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe documentos (PDF, Word, Excel, TXT, etc.) enviados al bot."""
    user_id = str(update.effective_user.id)
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("No autorizado.")
        return

    doc = update.message.document
    filename = doc.file_name or f"doc_{doc.file_id[:8]}"
    ext = Path(filename).suffix.lower()
    tipos_soportados = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".csv"}

    if ext not in tipos_soportados:
        await update.message.reply_text(
            f"Formato `{ext}` no soportado.\n"
            f"Acepto: PDF, Word, Excel, TXT, CSV."
        )
        return

    # Token corto para el callback (evita límite de 64 bytes de Telegram)
    token = str(uuid.uuid4())[:8]
    context.user_data[f"pending_{token}"] = {
        "file_id": doc.file_id,
        "filename": filename,
    }

    await update.message.reply_text(
        f"📄 Archivo recibido: *{filename}*\n\n¿En qué categoría lo guardo?",
        parse_mode="Markdown",
        reply_markup=teclado_categorias(token),
    )


async def handle_callback_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa la selección de categoría y sube el documento a Mollo Brain."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|", 2)
    if len(parts) != 3 or parts[0] != "cat":
        return

    _, categoria, token = parts
    pending = context.user_data.pop(f"pending_{token}", None)
    if not pending:
        await query.edit_message_text("⚠️ Sesión expirada. Envía el archivo de nuevo.")
        return

    file_id  = pending["file_id"]
    filename = pending["filename"]

    await query.edit_message_text(f"⏳ Descargando y procesando *{filename}* en `{categoria}`...", parse_mode="Markdown")

    try:
        # Descargar desde Telegram
        tg_file = await context.bot.get_file(file_id)
        file_path = await descargar_archivo(tg_file, filename)

        # Subir a Mollo Brain
        resultado = subir_a_mollo_brain(file_path, categoria)
        chunks = resultado.get("chunks_indexados", 0)

        # Subir a Dropbox
        ruta_dropbox = _subir_dropbox(file_path, categoria)
        dropbox_ok = not ruta_dropbox.startswith("ERROR:")
        dropbox_linea = f"☁️ Dropbox: `{ruta_dropbox}`" if dropbox_ok else f"⚠️ Dropbox: no guardado"

        await query.edit_message_text(
            f"✅ *{filename}* guardado en `{categoria}`\n"
            f"📊 {chunks} fragmentos indexados en memoria\n"
            f"{dropbox_linea}\n\n"
            f"⏳ Analizando contenido...",
            parse_mode="Markdown",
        )

        # Análisis automático con Claude
        analisis = analizar_con_mollo(
            f"Analiza el documento '{filename}' que acabo de subir. "
            f"Dame los puntos más importantes, datos clave y recomendaciones de acción.",
            categoria=categoria,
        )

        # Limpiar archivo temporal
        file_path.unlink(missing_ok=True)

        respuesta_final = (
            f"✅ *{filename}* — {chunks} fragmentos indexados\n"
            f"📁 Categoría: `{categoria}`\n"
            f"{dropbox_linea}\n\n"
            f"📋 *Análisis de Mollo:*\n\n{analisis}"
        )

        for i in range(0, len(respuesta_final), 3900):
            if i == 0:
                await query.edit_message_text(respuesta_final[i:i+3900], parse_mode="Markdown")
            else:
                await query.message.reply_text(respuesta_final[i:i+3900], parse_mode="Markdown")

    except Exception as e:
        await query.edit_message_text(f"❌ Error procesando el archivo:\n`{e}`", parse_mode="Markdown")


# ── Mollo Brain streaming ────────────────────────────────────────────────────

async def _ask_brain_stream(pregunta: str, session_id: str, status_msg, modo: str = None) -> str:
    """Llama a /chat/stream de Mollo Brain, filtra metadata interna y muestra el modelo usado."""
    from telegram.error import BadRequest

    chunks: list[str] = []
    last_edit_len = 0
    modo_detected = [None]
    meta_parsed = [False]

    async def _edit_if_changed():
        nonlocal last_edit_len
        current = "".join(chunks)
        if len(current) - last_edit_len < 80:
            return
        preview = current[:3900] + (" ▌" if len(current) <= 3900 else "…")
        try:
            await status_msg.edit_text(preview)
            last_edit_len = len(current)
        except BadRequest:
            pass

    payload = {"pregunta": pregunta, "usar_memoria": True, "session_id": session_id}
    if modo:
        payload["modo"] = modo

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{MOLLO_BRAIN_URL}/chat/stream",
                json=payload,
            ) as r:
                r.raise_for_status()
                async for chunk in r.aiter_text():
                    # Primer chunk puede contener metadata interna "\x02modo:Etiqueta\n"
                    if not meta_parsed[0] and chunk.startswith("\x02"):
                        meta_parsed[0] = True
                        newline = chunk.find("\n")
                        meta_line = chunk[1:newline] if newline != -1 else chunk[1:]
                        parts = meta_line.split(":", 1)
                        if parts:
                            modo_detected[0] = parts[0]
                        rest = chunk[newline + 1:] if newline != -1 else ""
                        if rest:
                            chunks.append(rest)
                            await _edit_if_changed()
                        continue
                    meta_parsed[0] = True
                    chunks.append(chunk)
                    await _edit_if_changed()
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Error conectando con Mollo Brain: {e}")
        return ""

    full = "".join(chunks)

    # Footer con modelo usado
    label = MODEL_LABEL_TG.get(modo_detected[0] or "", "")
    full_con_footer = full + (f"\n\n_{label}_" if label else "")

    partes = [full_con_footer[i:i+3900] for i in range(0, max(len(full_con_footer), 1), 3900)]
    try:
        await status_msg.edit_text(partes[0])
    except BadRequest:
        pass
    for parte in partes[1:]:
        await status_msg.reply_text(parte)

    return full


# ── handlers telegram ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"Mollo activo ✅\nTu user_id: {user_id}\n\n"
        "📁 DOCUMENTOS (envía el archivo directamente):\n"
        "  PDF / Word / Excel / TXT → Mollo lo guarda y analiza\n"
        "  /docs                    → lista documentos guardados\n"
        "  /sync_dropbox            → sincroniza docs de Dropbox al RAG\n\n"
        "💬 CONVERSACIÓN:\n"
        "  <mensaje>        → Mollo responde con Claude + memoria\n\n"
        "📰 NOTICIAS:\n"
        "  /noticias        → briefing de USA y Mexico\n\n"
        "🧠 APRENDIZAJE:\n"
        "  /aprende <texto> → guarda conocimiento en memoria\n"
        "  /memoria <tema>  → consulta la memoria de Mollo\n\n"
        "🖥️ VPS:\n"
        "  /status          → estado del servidor\n"
        "  /reporte         → diagnóstico ejecutivo con IA\n"
        "  /vps <acción>    → Claude propone comando bash\n"
        "  /confirmar       → ejecuta comando propuesto\n"
        "  /cancelar        → cancela comando pendiente\n"
        "  /cmd <bash>      → ejecuta comando directo\n\n"
        "🤖 IA DIRECTA:\n"
        "  /claude <texto>  → Claude Sonnet\n"
        "  /openai <texto>  → GPT-4.1-mini\n"
        "  /mollo <texto>   → modelo local Ollama\n\n"
        "📊 COSTOS Y ROUTING:\n"
        "  /stats           → costos y ahorro del auto-routing\n"
        "  /modo <nivel>    → fija modelo (simple/medio/complejo/agente/rapido/auto)"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("No autorizado.")
        return

    text = update.message.text.strip()
    low = text.lower()

    # ── /docs ──
    if low.startswith("/docs"):
        try:
            resp = requests.get(f"{MOLLO_BRAIN_URL}/docs/list", timeout=10)
            docs = resp.json().get("documentos", [])
            if not docs:
                respuesta = "No hay documentos guardados aún.\nEnvíame un PDF, Word o Excel y lo proceso."
            else:
                lines = [f"📁 *{d['categoria']}* — {d['nombre']} ({d['tamaño_kb']} KB)" for d in docs]
                respuesta = f"📚 *Documentos en Mollo Brain ({len(docs)}):*\n\n" + "\n".join(lines)
        except Exception as e:
            respuesta = f"Error consultando documentos: {e}"

    # ── /noticias ──
    elif low.startswith("/noticias"):
        await update.message.reply_text("Obteniendo noticias de USA y Mexico...")
        respuesta = briefing_noticias()

    # ── /status ──
    elif low.startswith("/status"):
        try:
            resp = requests.get(f"{MOLLO_BRAIN_URL}/vps/resumen", timeout=15)
            d = resp.json()
            alertas = "\n".join(d.get("alertas", []))
            respuesta = (
                f"🖥️ *VPS Status*\n"
                f"Uptime: {d.get('uptime')}\n"
                f"CPU: {d.get('cpu_uso_pct')}%\n"
                f"RAM: {d.get('ram_uso_pct')}% usado — {d.get('ram_disponible_mb')} MB libres\n"
                f"Disco: {d.get('disco_uso_pct')}% usado — {d.get('disco_disponible')} libres\n"
                f"Contenedores: {d.get('contenedores_activos')} activos\n\n"
                f"Alertas:\n{alertas}"
            )
        except Exception:
            respuesta = status_vps()

    # ── /reporte ──
    elif low.startswith("/reporte"):
        await update.message.reply_text("Generando reporte ejecutivo del VPS...")
        try:
            resp = requests.post(
                f"{MOLLO_BRAIN_URL}/vps/ask",
                json={"pregunta": "Dame un reporte ejecutivo completo del VPS con alertas y recomendaciones de acción"},
                timeout=90,
            )
            respuesta = resp.json().get("respuesta", "Sin respuesta")
        except Exception:
            respuesta = ask_openai(status_vps() + "\nDame diagnóstico ejecutivo y acciones recomendadas.")

    # ── /aprende ──
    elif low.startswith("/aprende"):
        contenido = text.replace("/aprende", "", 1).strip()
        if len(contenido) < 20:
            respuesta = "Texto muy corto. Escribe algo más completo para que Mollo aprenda."
        else:
            await update.message.reply_text("Procesando con Claude y guardando en memoria...")
            conocimiento = enriquecer_conocimiento(contenido)
            chunks = ingestar_en_memoria(conocimiento, fuente=f"telegram_{datetime.now().strftime('%Y%m%d')}")
            respuesta = (
                f"Aprendido y guardado en memoria ({chunks} fragmentos).\n\n"
                f"Conocimiento estructurado:\n{conocimiento[:800]}"
            )

    # ── /memoria ──
    elif low.startswith("/memoria"):
        query = text.replace("/memoria", "", 1).strip() or "Mollo"
        respuesta = buscar_memoria(query) or "No encontré memoria relacionada."

    # ── /briefing → resumen ejecutivo del día vía Mollo Brain agente ──
    elif low.startswith("/briefing"):
        session_id = f"telegram_{user_id}"
        status_msg = await update.message.reply_text("_📋 Preparando tu briefing…_", parse_mode="Markdown")
        await _ask_brain_stream(
            "Genera mi briefing ejecutivo del día: estado del VPS, "
            "proyectos y tareas pendientes de los que tengas registro, "
            "y 3 prioridades estratégicas para hoy.",
            session_id, status_msg, modo="agente",
        )
        return

    # ── /temas → memoria por temas especializados ──
    elif low.startswith("/temas"):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{MOLLO_BRAIN_URL}/memory/topics")
                data = r.json()
            lines = ["*Memoria por Temas*\n"]
            for key, info in data.items():
                icono = "🧠" if info.get("tiene_memoria") else "⚪"
                conv = info.get("conversaciones_procesadas", 0)
                lines.append(f"{icono} *{info['nombre']}* ({conv} convs)")
                if info.get("tiene_memoria"):
                    lines.append(f"_{info['resumen'][:120]}…_")
                    for p in info.get("pendientes", [])[:2]:
                        lines.append(f"  ◦ {p}")
                lines.append("")
            respuesta = "\n".join(lines)
        except Exception as e:
            respuesta = f"Error obteniendo temas: {e}"

    # ── /stats → dashboard de costos (paridad con CLI cmd_stats) ──
    elif low.startswith("/stats") or low.startswith("/costos"):
        try:
            EXCLUDE = "claude_code,external"  # imports, no son auto-routing
            all_lt = requests.get(f"{MOLLO_BRAIN_URL}/costs/lifetime", timeout=10).json()
            rt     = requests.get(f"{MOLLO_BRAIN_URL}/costs/lifetime",
                                  params={"exclude_modos": EXCLUDE}, timeout=10).json()
            models = requests.get(f"{MOLLO_BRAIN_URL}/costs/by_model", timeout=10).json()

            q_all   = all_lt.get("queries", 0) or 0
            tok_all = (all_lt.get("input_tokens", 0) or 0) + (all_lt.get("output_tokens", 0) or 0)
            cost_all = all_lt.get("actual_cost", 0) or 0

            q_r    = rt.get("queries", 0) or 0
            cost_r = rt.get("actual_cost", 0) or 0
            base_r = rt.get("baseline_cost", 0) or 0
            saved_r = rt.get("savings", 0) or 0
            pct_r   = rt.get("savings_pct", 0) or 0
            emoji = "🟢" if saved_r >= 0 else "🔴"
            signo = "+" if saved_r >= 0 else ""

            top = sorted(models or [], key=lambda m: m.get("actual_cost", 0) or 0, reverse=True)[:5]
            top_lines = "\n".join(
                f"  • {m.get('model','?')} ({m.get('modo','?')}) — "
                f"{m.get('queries',0)}q · ${m.get('actual_cost',0):.4f}"
                for m in top
            ) or "  (sin datos)"

            respuesta = (
                "📊 Costos — Mollo\n\n"
                f"Total tracked (incl. imports claude_code+external):\n"
                f"  {q_all} queries · {tok_all:,} tokens · real ${cost_all:.4f}\n\n"
                f"Auto-routing:\n"
                f"  {q_r} queries · real ${cost_r:.4f} · baseline(Sonnet) ${base_r:.4f}\n"
                f"  {emoji} ahorro routing: {signo}${saved_r:.4f} ({pct_r:.0f}%)\n\n"
                f"Top modelos por costo:\n{top_lines}"
            )
        except Exception as e:
            respuesta = f"Error obteniendo costos: {e}"

    # ── /modo → fija el tier de routing por chat (paridad con s.modo del CLI) ──
    elif low.startswith("/modo"):
        arg = text.replace("/modo", "", 1).strip().lower()
        if not arg:
            cur = context.user_data.get("modo")
            respuesta = (
                f"Modo actual: {cur or 'auto-routing'}\n\n"
                "Uso: /modo <simple|medio|complejo|agente|rapido|auto>\n"
                "  auto → Mollo elige el modelo (default)"
            )
        elif arg in MODOS_AUTO:
            context.user_data.pop("modo", None)
            respuesta = "Modo: auto-routing — Mollo elige el modelo por consulta."
        elif arg in MODOS_VALIDOS:
            context.user_data["modo"] = arg
            label = MODEL_LABEL_TG.get(arg, arg)
            respuesta = (
                f"Modo fijado: {arg} → {label}\n"
                "Aplica a tus mensajes libres. /modo auto para volver a auto-routing."
            )
        else:
            respuesta = (
                f"Modo desconocido: '{arg}'\n"
                "Válidos: simple, medio, complejo, agente, rapido, auto"
            )

    # ── /sync_dropbox ──
    elif low.startswith("/sync_dropbox"):
        full = "--full" in text
        await update.message.reply_text("☁️ Sincronizando Dropbox → RAG…")
        try:
            import subprocess as _sp
            args = ["--full"] if full else []
            result = _sp.run(
                ["/opt/mollo-telegram/venv/bin/python3", "/root/scripts/sync_dropbox_rag.py"] + args,
                capture_output=True, text=True, timeout=300,
            )
            lines = [l for l in result.stdout.splitlines() if any(k in l for k in ["✓", "nuevos:", "ERROR", "━━━"])]
            resumen = "\n".join(lines[-12:]) if lines else result.stdout[-600:]
            respuesta = f"☁️ *Sync Dropbox → RAG completado*\n\n```\n{resumen}\n```"
            await update.message.reply_text(respuesta, parse_mode="Markdown")
        except Exception as e:
            respuesta = f"Error en sync: {e}"

    # ── /vps ──
    elif low.startswith("/vps"):
        instruccion = text.replace("/vps", "", 1).strip()
        if not instruccion:
            respuesta = "Uso: /vps <qué quieres hacer en el VPS>"
        else:
            await update.message.reply_text("Analizando instrucción...")
            cmd = interpretar_cmd_vps(instruccion)
            if cmd.startswith("ERROR:"):
                respuesta = f"No puedo ejecutar eso:\n{cmd}"
            else:
                context.user_data["pending_cmd"] = cmd
                respuesta = (
                    f"Ejecutaré este comando:\n\n`{cmd}`\n\n"
                    "Responde /confirmar para ejecutar o /cancelar para abortar."
                )

    # ── /confirmar ──
    elif low.startswith("/confirmar"):
        cmd = context.user_data.pop("pending_cmd", None)
        if not cmd:
            respuesta = "No hay comando pendiente."
        else:
            await update.message.reply_text(f"Ejecutando:\n`{cmd}`")
            resultado = run_cmd(cmd)
            respuesta = f"Resultado:\n{resultado}"

    # ── /cancelar ──
    elif low.startswith("/cancelar"):
        context.user_data.pop("pending_cmd", None)
        respuesta = "Comando cancelado."

    # ── /cmd ──
    elif low.startswith("/cmd"):
        cmd = text.replace("/cmd", "", 1).strip()
        if not cmd:
            respuesta = "Uso: /cmd <comando bash>"
        else:
            await update.message.reply_text(f"Ejecutando:\n`{cmd}`")
            respuesta = run_cmd(cmd)

    # ── /openai → Mollo Brain modo medio (GPT-4o con RAG + memoria) ──
    elif low.startswith("/openai"):
        query = text.replace("/openai", "", 1).strip()
        if not query:
            await update.message.reply_text("Uso: /openai <consulta>")
            return
        session_id = f"telegram_{user_id}"
        status_msg = await update.message.reply_text("_⚡ GPT-4o procesando…_", parse_mode="Markdown")
        await _ask_brain_stream(query, session_id, status_msg, modo="medio")
        return

    # ── /claude → Mollo Brain modo complejo (Claude Sonnet con RAG + memoria) ──
    elif low.startswith("/claude"):
        query = text.replace("/claude", "", 1).strip()
        if not query:
            await update.message.reply_text("Uso: /claude <consulta>")
            return
        session_id = f"telegram_{user_id}"
        status_msg = await update.message.reply_text("_🧠 Claude Sonnet procesando…_", parse_mode="Markdown")
        await _ask_brain_stream(query, session_id, status_msg, modo="complejo")
        return

    # ── /mollo ──
    elif low.startswith("/mollo"):
        query = text.replace("/mollo", "", 1).strip() or text
        await update.message.reply_text("Consultando modelo local...")
        respuesta = ask_mollo(query)

    # ── mensaje libre → Mollo Brain (RAG + memoria semántica + aprendizajes) ──
    else:
        session_id = f"telegram_{user_id}"
        forced_modo = context.user_data.get("modo")  # fijado vía /modo
        status_msg = await update.message.reply_text("_✍️ pensando…_", parse_mode="Markdown")
        respuesta = await _ask_brain_stream(text, session_id, status_msg, modo=forced_modo)
        return  # _ask_brain_stream ya edita el mensaje

    for i in range(0, len(respuesta), 3900):
        await update.message.reply_text(respuesta[i:i + 3900])


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    # Documentos enviados al chat
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    # Selección de categoría via botones inline
    app.add_handler(CallbackQueryHandler(handle_callback_categoria, pattern=r"^cat\|"))
    # Mensajes de texto
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()

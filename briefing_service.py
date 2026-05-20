"""
Daily Brief programado — corre cada mañana a las 6 AM CDMX vía systemd timer.

Genera un brief ejecutivo extendido (estado VPS + proyectos + 3 prioridades +
CONNECTIONS + PATTERN + QUESTION) consultando Mollo en modo `complejo` (Sonnet)
para que use RAG + memoria semántica + topic_memory.

Lo entrega vía Telegram al bot `@mollo_adolfo_bot` (chat_id en
/opt/mollo-telegram/.env como ALLOWED_USER_ID).

Uso CLI:
    /root/venv/bin/python -m briefing_service
"""
import os
import sys
import json
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv("/root/mollo_brain/.env")
load_dotenv("/opt/mollo-telegram/.env")  # TELEGRAM_TOKEN + ALLOWED_USER_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("briefing")

BRAIN_URL       = os.getenv("BRAIN_URL", "http://localhost:8002")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

BRIEF_PROMPT = """Eres Mollo. Es la mañana de Adolfo. Genera el briefing ejecutivo del día estructurado en 6 secciones, en este orden exacto. Sé directo, en español, sin muletillas. Usa markdown ligero (encabezados ### y bullets).

### 1. Estado del VPS
Una línea con: CPU, RAM, disco, contenedores activos. Si algo está en rojo (CPU >80%, RAM >85%, disco >85%), márcalo con ⚠️. Si todo está bien, una sola línea de status.

### 2. Proyectos activos
3-5 bullets máximo de los proyectos en curso (MolloIA, Vantamedia, SinergyOS, Strategy OS, Excel RE, etc.) con su estado más reciente según contexto disponible. Si no hay update real, no inventes — di "sin update reciente".

### 3. 3 prioridades estratégicas para hoy
3 bullets numerados. Específicas, accionables, alineadas con el momentum actual del negocio. Cada una con un verbo de acción al inicio.

### 4. CONNECTIONS
2-3 conexiones interesantes entre lo que Adolfo guardó/conversó esta semana y memorias más viejas (de meses anteriores). Cita el origen entre paréntesis si aplica. Si no hay conexiones reales, di "sin conexiones nuevas hoy" — NO inventes.

### 5. PATTERN
1 párrafo (3-4 oraciones max) sobre el patrón que está emergiendo en lo que Adolfo está leyendo, pensando o construyendo. ¿Qué obsesión está formando su brain implícitamente?

### 6. QUESTION
1 pregunta para sentarse a pensar hoy. NO una tarea. Una pregunta que abra un eje de reflexión basado en el PATTERN identificado. Una sola línea.

Cierra con la fecha en formato "Brief del DD/MM/YYYY · Mollo".
"""


def generate_daily_brief() -> str:
    """Llama a /chat/ask con modo='complejo' y devuelve la respuesta del agente."""
    payload = {
        "pregunta":     BRIEF_PROMPT,
        "modo":         "complejo",
        "session_id":   "daily_brief",
        "usar_memoria": True,
    }
    try:
        r = requests.post(
            f"{BRAIN_URL}/chat/ask",
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        text = data.get("respuesta", "")
        if not text:
            raise RuntimeError(f"respuesta vacía. data={data}")
        logger.info("brief generado · modelo=%s · %d chars",
                    data.get("modelo"), len(text))
        return text
    except Exception as e:
        logger.exception("error generando brief")
        return f"⚠️ Error generando brief: {type(e).__name__}: {e}"


def send_to_telegram(text: str) -> bool:
    """Envía texto al bot Telegram. Trocea en mensajes ≤4096 chars (límite TG)."""
    if not TELEGRAM_TOKEN or not ALLOWED_USER_ID:
        logger.error("TELEGRAM_TOKEN o ALLOWED_USER_ID no configurados")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # TG max 4096 chars per message — split por párrafos si excede
    chunks = []
    if len(text) <= 4000:
        chunks = [text]
    else:
        # Split por dobles newlines, agrupando hasta no exceder 4000
        current = []
        size = 0
        for para in text.split("\n\n"):
            if size + len(para) + 2 > 4000 and current:
                chunks.append("\n\n".join(current))
                current = [para]
                size = len(para)
            else:
                current.append(para)
                size += len(para) + 2
        if current:
            chunks.append("\n\n".join(current))

    ok = True
    for i, chunk in enumerate(chunks):
        try:
            r = requests.post(
                url,
                json={
                    "chat_id":    ALLOWED_USER_ID,
                    "text":       chunk,
                    "parse_mode": "Markdown",
                },
                timeout=15,
            )
            if r.status_code != 200:
                # Reintentar sin parse_mode si Markdown rompe
                logger.warning("TG error %d, reintentando sin Markdown: %s",
                               r.status_code, r.text[:200])
                r = requests.post(
                    url,
                    json={"chat_id": ALLOWED_USER_ID, "text": chunk},
                    timeout=15,
                )
                r.raise_for_status()
            logger.info("chunk %d/%d enviado a Telegram (%d chars)",
                        i + 1, len(chunks), len(chunk))
        except Exception as e:
            logger.exception("error enviando chunk %d", i + 1)
            ok = False
    return ok


def run_daily() -> int:
    """Entry point del systemd timer. Returns exit code (0 OK, 1 fail)."""
    logger.info("=== Daily brief start · %s ===", datetime.now().isoformat())
    text = generate_daily_brief()
    success = send_to_telegram(text)
    logger.info("=== Daily brief end · success=%s ===", success)
    return 0 if success else 1


# ───────── Weekly ISA review (cron del lunes) ─────────

ISA_REVIEW_PROMPT_TEMPLATE = """Eres Mollo. Es lunes en la mañana de Adolfo. Genera un weekly review del siguiente ISA para que él decida qué accionar esta semana.

ISA actual:
---
{isa_content}
---

Estructura tu review así (markdown ligero, español, sin muletillas):

### 📊 Status — semana del {date}
Una línea: ¿el ISA está en track o no? Basado en `Criteria` vs `Verification`.

### ✅ Criteria cumplidos
Bullets de los ISCs que ya están verificados. Si no hay, di "ninguno aún".

### 🎯 Criteria pendientes priorizados
2-3 bullets de los ISCs más críticos por cerrar esta semana. Cada uno con la acción concreta de los próximos 7 días.

### ⚠️ Riesgos / blockers
1-2 bullets de qué amenaza el plan. Si nada, "sin blockers visibles".

### 🔄 Sugerencia de update al ISA
1-2 cambios que tendría sentido reflejar en `Decisions` o `Changelog` del ISA si las cosas evolucionaron. Si nada, "ISA sigue vigente".

### 🎬 Próxima acción única para hoy
UNA cosa accionable que mueva el aguja más. Verbo + objeto + entregable visible al final del día.

Cierra con: "Weekly ISA review · {date} · Mollo"
"""


def generate_isa_review(isa_id: str) -> str:
    """Lee el ISA via /pai/isa/<id> y pide a Mollo el weekly review."""
    try:
        r = requests.get(f"{BRAIN_URL}/pai/isa/{isa_id}", timeout=10)
        r.raise_for_status()
        isa_content = r.json().get("content", "")
        if not isa_content:
            return f"⚠️ ISA '{isa_id}' vacía o no existe."
    except Exception as e:
        logger.exception("error leyendo ISA %s", isa_id)
        return f"⚠️ No pude leer ISA '{isa_id}': {type(e).__name__}: {e}"

    prompt = ISA_REVIEW_PROMPT_TEMPLATE.format(
        isa_content=isa_content,
        date=datetime.now().strftime("%d/%m/%Y"),
    )
    try:
        r = requests.post(
            f"{BRAIN_URL}/chat/ask",
            json={"pregunta": prompt, "modo": "complejo",
                  "session_id": f"isa_review_{isa_id}", "usar_memoria": True},
            timeout=180,
        )
        r.raise_for_status()
        text = r.json().get("respuesta", "")
        if not text:
            raise RuntimeError("respuesta vacía")
        logger.info("ISA review generado · isa=%s · %d chars", isa_id, len(text))
        return text
    except Exception as e:
        logger.exception("error generando ISA review")
        return f"⚠️ Error generando review de ISA '{isa_id}': {type(e).__name__}: {e}"


def run_isa_review(isa_id: str) -> int:
    """Entry CLI del cron del lunes."""
    logger.info("=== ISA review start · isa=%s · %s ===", isa_id, datetime.now().isoformat())
    text = generate_isa_review(isa_id)
    header = f"📋 *Weekly ISA Review*\n_{isa_id}_\n\n"
    success = send_to_telegram(header + text)
    logger.info("=== ISA review end · success=%s ===", success)
    return 0 if success else 1


if __name__ == "__main__":
    # python -m briefing_service                                  → daily brief
    # python -m briefing_service isa-review <isa_id>              → weekly ISA review
    if len(sys.argv) >= 3 and sys.argv[1] == "isa-review":
        sys.exit(run_isa_review(sys.argv[2]))
    sys.exit(run_daily())

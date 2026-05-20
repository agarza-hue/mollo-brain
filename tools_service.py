"""Herramientas que Mollo puede ejecutar — web, VPS, n8n, memoria, divisas."""
import subprocess, json, os, difflib
import httpx
from contextvars import ContextVar
from config import N8N_URL, N8N_WEBHOOK_SECRET, BANXICO_TOKEN

# Buffer de eventos estructurados por request (para que stream_agent_* los emita
# al CLI sin contaminar lo que ve el LLM como resultado de la tool).
_tool_events: ContextVar[list | None] = ContextVar("mollo_tool_events", default=None)

def begin_tool_events() -> list[dict]:
    """Inicializa el buffer de eventos para la request actual. Llamar al inicio
    de stream_agent_*."""
    events: list[dict] = []
    _tool_events.set(events)
    return events

def drain_tool_events() -> list[dict]:
    """Devuelve y limpia los eventos acumulados (no-op si no hay buffer)."""
    lst = _tool_events.get()
    if not lst:
        return []
    out = list(lst)
    lst.clear()
    return out

def _push_tool_event(ev: dict) -> None:
    lst = _tool_events.get()
    if lst is not None:
        lst.append(ev)

# ── Definiciones para Claude ──────────────────────────────────────────────────

TOOLS = [
    {
        "name": "tipo_cambio",
        "description": (
            "Obtiene el tipo de cambio oficial USD/MXN en tiempo real desde Banxico. "
            "Úsalo para cualquier pregunta sobre el precio del dólar, peso mexicano, "
            "tipo de cambio, cotización del dólar hoy, o conversiones de moneda."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "monto": {
                    "type": "number",
                    "description": "Monto a convertir (opcional). Si se omite, devuelve solo la cotización.",
                },
                "direccion": {
                    "type": "string",
                    "description": "Dirección de conversión: 'usd_a_mxn' o 'mxn_a_usd'. Default: usd_a_mxn",
                },
            },
        },
    },
    {
        "name": "buscar_web",
        "description": (
            "Busca información actualizada en internet. "
            "Úsalo cuando necesites: noticias recientes, datos que no están en los documentos de Adolfo, "
            "o verificar algo en tiempo real. Para tipo de cambio USD/MXN usa la herramienta tipo_cambio."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Términos de búsqueda claros y específicos",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "estado_vps",
        "description": (
            "Obtiene el estado actual del servidor VPS: "
            "CPU, RAM, disco, contenedores Docker activos y servicios."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ejecutar_comando_vps",
        "description": (
            "Ejecuta un comando de diagnóstico o mantenimiento en el VPS. "
            "Comandos disponibles: "
            "docker_logs <nombre> — ver últimas líneas del contenedor, "
            "docker_restart <nombre> — reiniciar contenedor, "
            "docker_ps — listar contenedores, "
            "disk_clean — limpiar imágenes y volúmenes Docker sin uso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "comando": {
                    "type": "string",
                    "description": "Comando a ejecutar. Ejemplo: 'docker_logs mollo_brain' o 'docker_ps'",
                }
            },
            "required": ["comando"],
        },
    },
    {
        "name": "disparar_workflow_n8n",
        "description": (
            "Dispara un flujo de automatización en n8n. "
            "Workflows disponibles: "
            "reporte_semanal — genera y envía reporte ejecutivo, "
            "alerta_email — envía email de alerta a Adolfo, "
            "sync_docs — sincroniza documentos en Qdrant. "
            "Puedes pasar datos adicionales en el campo 'datos'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow": {
                    "type": "string",
                    "description": "Nombre del workflow: reporte_semanal | alerta_email | sync_docs",
                },
                "datos": {
                    "type": "object",
                    "description": "Datos para pasar al workflow (opcional)",
                },
            },
            "required": ["workflow"],
        },
    },
    {
        "name": "dropbox_listar",
        "description": (
            "Lista archivos y carpetas de Dropbox. "
            "Úsalo para ver qué documentos tiene Adolfo disponibles."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "carpeta": {
                    "type": "string",
                    "description": "Ruta de la carpeta. Usa '' o 'raiz' para la raíz. Ej: 'Documentos/Finanzas'",
                },
            },
        },
    },
    {
        "name": "dropbox_buscar",
        "description": (
            "Busca archivos en Dropbox por nombre o contenido. "
            "Úsalo cuando Adolfo mencione un archivo pero no sepa dónde está."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término de búsqueda: nombre de archivo, extensión, palabra clave",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "dropbox_analizar",
        "description": (
            "Descarga un archivo de Dropbox y lo analiza. "
            "Soporta PDF, DOCX, XLSX, TXT, CSV. "
            "Úsalo cuando Adolfo quiera que Mollo lea o analice un documento de su Dropbox."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ruta": {
                    "type": "string",
                    "description": "Ruta completa del archivo en Dropbox. Ej: 'Finanzas/reporte_q1.pdf'",
                },
                "instruccion": {
                    "type": "string",
                    "description": "Qué hacer con el archivo: resumir, extraer datos, revisar contratos, etc.",
                },
            },
            "required": ["ruta"],
        },
    },
    {
        "name": "dropbox_subir",
        "description": (
            "Sube un archivo local del VPS a Dropbox. "
            "Úsalo para guardar reportes, análisis o exportaciones en Dropbox."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ruta_local": {
                    "type": "string",
                    "description": "Ruta del archivo en el VPS. Ej: '/root/reportes/reporte.pdf'",
                },
                "destino": {
                    "type": "string",
                    "description": "Ruta destino en Dropbox. Ej: 'Reportes/reporte_mayo.pdf'",
                },
            },
            "required": ["ruta_local", "destino"],
        },
    },
    {
        "name": "leer_archivo",
        "description": (
            "Lee el contenido de un archivo del proyecto MolloAI. "
            "Úsalo ANTES de modificar cualquier archivo para ver el código actual. "
            "Rutas permitidas: /root/projects/mollo-web/ y /root/mollo_brain/"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ruta": {
                    "type": "string",
                    "description": "Ruta absoluta del archivo. Ej: /root/projects/mollo-web/app/chat-client.tsx",
                },
                "desde_linea": {
                    "type": "integer",
                    "description": "Línea desde la que empezar a leer (opcional, default 1)",
                },
                "hasta_linea": {
                    "type": "integer",
                    "description": "Línea hasta la que leer (opcional, default: todo el archivo)",
                },
            },
            "required": ["ruta"],
        },
    },
    {
        "name": "escribir_archivo",
        "description": (
            "Crea o sobreescribe un archivo del proyecto MolloAI con contenido nuevo. "
            "SIEMPRE lee el archivo primero con leer_archivo antes de modificarlo. "
            "Rutas permitidas: /root/projects/mollo-web/ y /root/mollo_brain/"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ruta": {
                    "type": "string",
                    "description": "Ruta absoluta del archivo a escribir",
                },
                "contenido": {
                    "type": "string",
                    "description": "Contenido completo del archivo",
                },
            },
            "required": ["ruta", "contenido"],
        },
    },
    {
        "name": "bash",
        "description": (
            "Ejecuta cualquier comando bash en el VPS de Adolfo. "
            "Úsalo para: compilar proyectos, reiniciar servicios, leer logs, "
            "instalar paquetes, manipular archivos, consultar bases de datos, "
            "operaciones git, inspeccionar procesos y contenedores Docker. "
            "Comandos útiles de deploy: "
            "'cd /root/projects/mollo-web && npm run build' para compilar frontend, "
            "'pm2 restart mollo-web' para reiniciar web, "
            "'pm2 logs mollo-web --lines 30 --nostream' para ver logs, "
            "'kill $(lsof -ti:8002) && nohup /root/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8002 --workers 2 > /tmp/brain.log 2>&1 &' para reiniciar brain, "
            "'docker compose -f /root/projects/juntas-app/docker-compose.yml restart app' para juntas-app."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "comando": {
                    "type": "string",
                    "description": "Comando bash completo a ejecutar en el VPS",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout en segundos (default 60, max 300). Usa 180 para npm build.",
                },
            },
            "required": ["comando"],
        },
    },
    {
        "name": "buscar_codigo",
        "description": (
            "Busca texto, funciones, variables o patrones en el código fuente del VPS. "
            "Úsalo para encontrar dónde está definida una función, qué archivos usan cierta variable, "
            "o localizar cualquier string en el codebase."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patron": {
                    "type": "string",
                    "description": "Texto o regex a buscar",
                },
                "directorio": {
                    "type": "string",
                    "description": "Directorio donde buscar. Default: /root/projects/mollo-web. Ej: /root/mollo_brain",
                },
                "extension": {
                    "type": "string",
                    "description": "Filtrar por extensión. Ej: ts, py, tsx (sin punto)",
                },
            },
            "required": ["patron"],
        },
    },
    {
        "name": "git",
        "description": (
            "Operaciones git en cualquier proyecto del VPS. "
            "Operaciones: status, diff, log, add, commit, push, branch, checkout."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operacion": {
                    "type": "string",
                    "description": "Operación: status | diff | log | add <archivo> | commit <mensaje> | push | branch | checkout <rama>",
                },
                "directorio": {
                    "type": "string",
                    "description": "Directorio del repo. Default: /root/projects/mollo-web",
                },
            },
            "required": ["operacion"],
        },
    },
    {
        "name": "ejecutar_dev",
        "description": (
            "Atajos rápidos para las operaciones de deploy más comunes. "
            "Comandos: npm_build, pm2_restart, pm2_logs, brain_restart, brain_logs, "
            "listar_app <subcarpeta>, git_status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "comando": {
                    "type": "string",
                    "description": "Atajo: npm_build | pm2_restart | pm2_logs | brain_restart | brain_logs | listar_app <sub> | git_status",
                },
            },
            "required": ["comando"],
        },
    },
    {
        "name": "guardar_contexto_negocio",
        "description": (
            "Guarda un dato importante sobre el negocio o situación de Adolfo "
            "para recordarlo en futuras conversaciones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "clave": {
                    "type": "string",
                    "description": "Identificador corto del dato. Ej: meta_ventas_q2, cliente_principal",
                },
                "valor": {
                    "type": "string",
                    "description": "Información a recordar",
                },
            },
            "required": ["clave", "valor"],
        },
    },
]

# ── Lista blanca de comandos VPS ──────────────────────────────────────────────

_VPS_COMMANDS: dict[str, str] = {
    "docker_ps":       "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'",
    "docker_logs":     "docker logs --tail 60 {arg}",
    "docker_restart":  "docker restart {arg}",
    "disk_clean":      "docker system prune -f",
}


# ── Lazy-load: selecciona subset de tools según la pregunta ───────────────────
#
# Mandar las 16 tools = 2,381 tok cada turno agente. Pero la mayoría de
# queries solo usan 1-2. Tier 1 va siempre (~785 tok core). Tier 2 se carga
# por keyword (~1,596 tok adicionales). Estrategia: ser GENEROSO en triggers
# — falso positivo cuesta tokens, falso negativo cuesta la respuesta entera
# (el agente no puede usar lo que no ve).

_TIER1 = {"bash", "leer_archivo", "escribir_archivo", "buscar_web", "estado_vps"}

# Cada grupo: lista de tools que se cargan juntas + keywords que las disparan.
# Las keywords se buscan como substring en `question.lower()`, así que pueden
# ser parciales ("git " incluye "git", "git push", etc.).
_TIER2_GROUPS = [
    {
        "tools": ["tipo_cambio"],
        "keywords": [
            "dólar", "dolar", "dolares", "dólares", "peso ", "pesos",
            " usd", " mxn", "tipo de cambio", "cotiz", "convierte",
            "convertir", "moneda", "banxico",
        ],
    },
    {
        "tools": ["ejecutar_comando_vps"],
        "keywords": [
            "vps", "servidor", "docker", "container", "contenedor",
            "logs ", "service ", "systemctl", "reinicia", "reiniciar",
            "estado del", "qué corre", "que corre",
        ],
    },
    {
        "tools": ["disparar_workflow_n8n"],
        "keywords": [
            "n8n", "workflow", "automatiza", "automatización",
            "reporte_semanal", "alerta_email", "sync_docs", "dispara",
        ],
    },
    {
        "tools": [
            "dropbox_listar", "dropbox_buscar",
            "dropbox_analizar", "dropbox_subir",
        ],
        "keywords": [
            "dropbox", "carpeta", "documentos en", "subir a", "descarga",
            "archivo en dropbox", "mis archivos", "mis documentos",
        ],
    },
    {
        "tools": ["buscar_codigo", "git", "ejecutar_dev"],
        "keywords": [
            "código", "codigo", "función ", "funcion ", "clase ",
            "git ", "commit", "push ", "pull ", "branch", "merge",
            "diff", "checkout", "rebase",
            "npm ", "yarn ", "build", "deploy", "compila", "compilar",
            "test", "pytest", "jest", "donde está", "donde se define",
            " bug ", "fix", "refactor",
        ],
    },
    {
        "tools": ["guardar_contexto_negocio"],
        "keywords": [
            "guarda", "guardar", "recuerda", "anota", "memoriza",
            "no olvides", "ten en cuenta", "para futuro",
        ],
    },
]


def select_tools(question: str) -> list[dict]:
    """Pick a subset of TOOLS based on keywords in the user's question.
    Always returns at least the Tier 1 set; adds Tier 2 groups whose keywords
    match. Order preserves the original TOOLS list (so prompt cache hits)."""
    q = (question or "").lower()
    selected: set[str] = set(_TIER1)
    for grp in _TIER2_GROUPS:
        if any(kw in q for kw in grp["keywords"]):
            selected.update(grp["tools"])
    return [t for t in TOOLS if t["name"] in selected]


# ── Ejecutores ────────────────────────────────────────────────────────────────

async def execute_tool(name: str, inputs: dict) -> str:
    try:
        if name == "leer_archivo":
            return _leer_archivo(inputs["ruta"], inputs.get("desde_linea"), inputs.get("hasta_linea"))
        if name == "escribir_archivo":
            return _escribir_archivo(inputs["ruta"], inputs["contenido"])
        if name == "bash":
            return _bash(inputs["comando"], inputs.get("timeout", 60))
        if name == "buscar_codigo":
            return _buscar_codigo(inputs["patron"], inputs.get("directorio"), inputs.get("extension"))
        if name == "git":
            return _git(inputs["operacion"], inputs.get("directorio"))
        if name == "ejecutar_dev":
            return _ejecutar_dev(inputs["comando"])
        if name == "dropbox_listar":
            return _dropbox_listar(inputs.get("carpeta", ""))
        if name == "dropbox_buscar":
            return _dropbox_buscar(inputs["query"])
        if name == "dropbox_analizar":
            return await _dropbox_analizar(inputs["ruta"], inputs.get("instruccion", ""))
        if name == "dropbox_subir":
            return _dropbox_subir(inputs["ruta_local"], inputs["destino"])
        if name == "tipo_cambio":
            return await _tipo_cambio(
                monto=inputs.get("monto"),
                direccion=inputs.get("direccion", "usd_a_mxn"),
            )
        if name == "buscar_web":
            return await _buscar_web(inputs["query"])
        if name == "estado_vps":
            return _estado_vps()
        if name == "ejecutar_comando_vps":
            return _ejecutar_comando_vps(inputs["comando"])
        if name == "disparar_workflow_n8n":
            return await _disparar_n8n(inputs["workflow"], inputs.get("datos", {}))
        if name == "guardar_contexto_negocio":
            return _guardar_contexto(inputs["clave"], inputs["valor"])
        return f"Herramienta '{name}' no implementada"
    except Exception as e:
        return f"Error ejecutando '{name}': {e}"


async def _tipo_cambio(monto: float | None = None, direccion: str = "usd_a_mxn") -> str:
    rate = None
    fuente = ""
    fecha = ""

    # ── Fuente 1: Banxico SIE API (oficial) ──────────────────────────────────
    if BANXICO_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/oportuno",
                    headers={"Bmx-Token": BANXICO_TOKEN, "Accept": "application/json"},
                )
                r.raise_for_status()
                data = r.json()
                dato = data["bmx"]["series"][0]["datos"][0]
                rate  = float(dato["dato"])
                fecha = dato["fecha"]
                fuente = "Banxico (oficial)"
        except Exception as e:
            fuente = f"Banxico error: {e}"

    # ── Fuente 2: open.er-api.com (fallback gratuito, actualiza cada 24h) ────
    if rate is None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get("https://open.er-api.com/v6/latest/USD")
                r.raise_for_status()
                data  = r.json()
                rate  = data["rates"]["MXN"]
                fecha = data.get("time_last_update_utc", "")
                fuente = "Open Exchange Rates (mercado)"
        except Exception as e:
            return f"No se pudo obtener el tipo de cambio. Banxico: {fuente} | Fallback error: {e}"

    # ── Formatear respuesta ───────────────────────────────────────────────────
    lines = [
        f"Tipo de cambio USD/MXN: **${rate:,.4f} MXN por dólar**",
        f"Fuente: {fuente}",
        f"Fecha/hora: {fecha}",
    ]

    if monto:
        if direccion == "usd_a_mxn":
            resultado = monto * rate
            lines.append(f"Conversión: ${monto:,.2f} USD = ${resultado:,.2f} MXN")
        else:
            resultado = monto / rate
            lines.append(f"Conversión: ${monto:,.2f} MXN = ${resultado:,.2f} USD")

    return "\n".join(lines)


async def _buscar_web(query: str) -> str:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=6, region="mx-es"))
        if not results:
            return "Sin resultados para esa búsqueda."
        lines = []
        for r in results:
            lines.append(f"**{r.get('title', '')}**\n{r.get('body', '')}\nFuente: {r.get('href', '')}")
        return "\n\n---\n\n".join(lines)
    except Exception as e:
        return f"Error en búsqueda web: {e}"


def _estado_vps() -> str:
    from routers.vps import vps_status
    status = vps_status()
    ram = status["ram"]
    cpu = status["cpu"]
    discos = status.get("discos", [])
    disco_raiz = next((d for d in discos if d["montado_en"] == "/"), {})
    contenedores = status.get("docker", [])
    servicios = status.get("servicios", {})

    lines = [
        f"Uptime: {status.get('uptime')}",
        f"CPU: {cpu.get('uso_pct')}% | Load: {cpu.get('load_1m')}/{cpu.get('load_5m')}/{cpu.get('load_15m')}",
        f"RAM: {ram.get('uso_pct')}% usado ({ram.get('disponible_mb')} MB libres de {ram.get('total_mb')} MB)",
        f"Disco /: {disco_raiz.get('uso_pct')}% ({disco_raiz.get('disponible')} libres)",
        f"Contenedores activos: {len(contenedores)}",
    ]
    if contenedores:
        lines.append("Docker: " + ", ".join(c["nombre"] for c in contenedores))
    lines.append("Servicios: " + " | ".join(f"{k}: {v}" for k, v in servicios.items()))
    return "\n".join(lines)


def _ejecutar_comando_vps(comando: str) -> str:
    parts = comando.strip().split(None, 1)
    cmd_key = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd_key not in _VPS_COMMANDS:
        return (
            f"Comando '{cmd_key}' no permitido. "
            f"Opciones: {', '.join(_VPS_COMMANDS.keys())}"
        )

    cmd_template = _VPS_COMMANDS[cmd_key]
    cmd = cmd_template.format(arg=arg) if "{arg}" in cmd_template else cmd_template

    try:
        output = subprocess.check_output(
            cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=30
        )
        return output.strip() or "(sin salida)"
    except subprocess.CalledProcessError as e:
        return f"Error (código {e.returncode}):\n{e.output.strip()}"
    except subprocess.TimeoutExpired:
        return "Timeout: el comando tardó más de 30 segundos"


async def _disparar_n8n(workflow: str, datos: dict) -> str:
    if not N8N_URL:
        return "N8N_URL no configurada en .env — agrega la URL del webhook de n8n"

    webhook_url = f"{N8N_URL.rstrip('/')}/webhook/{workflow}"
    headers = {}
    if N8N_WEBHOOK_SECRET:
        headers["X-Webhook-Secret"] = N8N_WEBHOOK_SECRET

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(webhook_url, json=datos or {}, headers=headers)
            if r.status_code == 200:
                return f"Workflow '{workflow}' disparado. Respuesta: {r.text[:200]}"
            return f"n8n respondió {r.status_code}: {r.text[:200]}"
    except httpx.ConnectError:
        return f"No se pudo conectar a n8n en {N8N_URL}. Verifica que esté corriendo."
    except Exception as e:
        return f"Error disparando workflow '{workflow}': {e}"


def _guardar_contexto(clave: str, valor: str) -> str:
    from memory_service import update_business_context
    update_business_context(clave, valor)
    return f"Guardado: {clave} = {valor}"


# ── Dropbox ───────────────────────────────────────────────────────────────────

def _dropbox_listar(carpeta: str) -> str:
    try:
        from dropbox_service import listar_archivos
        return listar_archivos(carpeta)
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Error Dropbox: {e}"


def _dropbox_buscar(query: str) -> str:
    try:
        from dropbox_service import buscar_archivos
        return buscar_archivos(query)
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Error Dropbox: {e}"


async def _dropbox_analizar(ruta: str, instruccion: str) -> str:
    try:
        from dropbox_service import descargar_texto
        from claude_service import analyze_document
        texto, nombre = descargar_texto(ruta)
        if texto.startswith("Error"):
            return texto
        if not texto.strip():
            return f"El archivo '{nombre}' no tiene texto extraíble."
        return analyze_document(texto, instruccion or f"Analiza este documento: {nombre}")
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Error analizando archivo: {e}"


_ALLOWED_ROOTS = (
    "/root/projects",
    "/root/mollo_brain",
    "/opt/mollo-gateway",
    "/root/strategy_os",
)

def _check_ruta(ruta: str) -> str:
    ruta = os.path.normpath(ruta)
    if not any(ruta.startswith(r) for r in _ALLOWED_ROOTS):
        raise PermissionError(f"Ruta no permitida: {ruta}")
    return ruta

def _bash(comando: str, timeout: int = 60) -> str:
    timeout = min(max(timeout, 5), 300)
    try:
        result = subprocess.run(
            comando, shell=True, text=True,
            capture_output=True, timeout=timeout,
            env={**os.environ, "TERM": "dumb"},
        )
        out = (result.stdout or "") + (result.stderr or "")
        out = out.strip()
        # Truncamos agresivo: cada iter del agentic loop replays todos los
        # tool_results, así que un 12K char dump se paga 3-4 veces. 4K chars
        # = ~1K tok suelen ser suficientes para errors/diffs útiles.
        if len(out) > 4000:
            out = out[-4000:]
            out = "[...salida truncada a las últimas 4000 chars]\n" + out
        if not out:
            out = f"(sin salida, código de salida: {result.returncode})"
        return out
    except subprocess.TimeoutExpired:
        return f"⏱ Timeout tras {timeout}s. Usa un timeout mayor o divide el comando."
    except Exception as e:
        return f"Error ejecutando bash: {e}"


def _buscar_codigo(patron: str, directorio: str | None = None, extension: str | None = None) -> str:
    base = directorio or "/root/projects/mollo-web"
    ext_flag = f"--include='*.{extension}'" if extension else ""
    cmd = (
        f"grep -rn {ext_flag} --color=never "
        f"-E '{patron}' "
        f"'{base}' "
        f"--exclude-dir=node_modules --exclude-dir=.next --exclude-dir=__pycache__ "
        f"--exclude-dir=.git 2>/dev/null | head -60"
    )
    try:
        out = subprocess.check_output(cmd, shell=True, text=True, timeout=15)
        return out.strip() or f"Sin coincidencias para '{patron}' en {base}"
    except subprocess.CalledProcessError:
        return f"Sin coincidencias para '{patron}' en {base}"
    except Exception as e:
        return f"Error buscando: {e}"


def _git(operacion: str, directorio: str | None = None) -> str:
    repo = directorio or "/root/projects/mollo-web"
    op   = operacion.strip()

    # Mapeo de operaciones seguras
    if op == "status":
        cmd = f"git -C '{repo}' status"
    elif op == "diff":
        cmd = f"git -C '{repo}' diff --stat HEAD 2>/dev/null || git -C '{repo}' diff"
    elif op == "log":
        cmd = f"git -C '{repo}' log --oneline -20"
    elif op == "branch":
        cmd = f"git -C '{repo}' branch -a"
    elif op.startswith("add "):
        archivo = op[4:].strip()
        cmd = f"git -C '{repo}' add '{archivo}'"
    elif op.startswith("commit "):
        msg = op[7:].strip()
        cmd = f"git -C '{repo}' commit -m '{msg}'"
    elif op == "push":
        cmd = f"git -C '{repo}' push"
    elif op.startswith("checkout "):
        rama = op[9:].strip()
        cmd = f"git -C '{repo}' checkout '{rama}'"
    else:
        cmd = f"git -C '{repo}' {op}"

    return _bash(cmd, timeout=30)


def _leer_archivo(ruta: str, desde: int | None = None, hasta: int | None = None) -> str:
    import os
    ruta = _check_ruta(ruta)
    if not os.path.exists(ruta):
        return f"Archivo no encontrado: {ruta}"
    with open(ruta, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    total = len(lines)
    start = max(0, (desde or 1) - 1)
    end   = hasta if hasta else total
    selected = lines[start:end]
    if len(selected) > 400:
        selected = selected[:400]
        truncated = f"\n[... truncado a 400 líneas de {total} totales]"
    else:
        truncated = ""
    numbered = [f"{start + i + 1:4d}  {l}" for i, l in enumerate(selected)]
    return f"```\n{''.join(numbered)}\n```{truncated}"

def _escribir_archivo(ruta: str, contenido: str) -> str:
    import os
    ruta = _check_ruta(ruta)
    is_new = not os.path.exists(ruta)
    old_content = ""
    if not is_new:
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                old_content = f.read()
        except Exception:
            old_content = ""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    lines = contenido.count("\n") + 1
    # Evento estructurado para el CLI (no afecta lo que ve el LLM)
    try:
        MAX_LINES = 200
        if is_new:
            new_lines = contenido.splitlines()
            ev = {
                "type": "write",
                "path": ruta,
                "is_new": True,
                "diff_lines": None,
                "new_preview": new_lines[:MAX_LINES],
                "added": len(new_lines),
                "removed": 0,
                "truncated": len(new_lines) > MAX_LINES,
            }
        else:
            diff = list(difflib.unified_diff(
                old_content.splitlines(), contenido.splitlines(),
                fromfile="a", tofile="b", n=3, lineterm=""
            ))
            added   = sum(1 for d in diff[2:] if d.startswith("+") and not d.startswith("+++"))
            removed = sum(1 for d in diff[2:] if d.startswith("-") and not d.startswith("---"))
            body = diff[2:]
            truncated = len(body) > MAX_LINES
            if truncated:
                body = body[:MAX_LINES]
            ev = {
                "type": "write",
                "path": ruta,
                "is_new": False,
                "diff_lines": body,
                "new_preview": None,
                "added": added,
                "removed": removed,
                "truncated": truncated,
            }
        _push_tool_event(ev)
    except Exception:
        pass
    return f"✅ Archivo escrito: {ruta} ({lines} líneas)"

_DEV_COMMANDS: dict[str, str] = {
    "npm_build":      "cd /root/projects/mollo-web && npm run build 2>&1",
    "pm2_restart":    "pm2 restart mollo-web && pm2 status mollo-web 2>&1",
    "pm2_logs":       "pm2 logs mollo-web --lines 30 --nostream 2>&1",
    "brain_restart":  (
        "kill $(lsof -ti:8002) 2>/dev/null; sleep 1; "
        "nohup /root/venv/bin/python -m uvicorn main:app "
        "--host 0.0.0.0 --port 8002 --workers 2 "
        "> /tmp/mollo_brain.log 2>&1 & sleep 3 && "
        "curl -s http://localhost:8002/health"
    ),
    "brain_logs":     "tail -60 /tmp/mollo_brain.log 2>&1",
    "git_status":     "cd /root/projects/mollo-web && git status 2>&1 || echo 'Sin git'",
}

def _ejecutar_dev(comando: str) -> str:
    parts   = comando.strip().split(None, 1)
    cmd_key = parts[0].lower()
    arg     = parts[1] if len(parts) > 1 else ""

    if cmd_key == "listar_app":
        base   = "/root/projects/mollo-web"
        folder = f"{base}/{arg.strip('/')}" if arg else base
        folder = os.path.normpath(folder)
        if not folder.startswith(base):
            return "Ruta no permitida"
        cmd = f"find {folder} -maxdepth 2 -not -path '*/node_modules/*' -not -path '*/.next/*' | sort"
        try:
            return subprocess.check_output(cmd, shell=True, text=True, timeout=15).strip()
        except Exception as e:
            return f"Error: {e}"

    if cmd_key not in _DEV_COMMANDS:
        return (
            f"Comando '{cmd_key}' no reconocido. "
            f"Disponibles: {', '.join(list(_DEV_COMMANDS.keys()) + ['listar_app <subcarpeta>'])}"
        )

    cmd = _DEV_COMMANDS[cmd_key]
    try:
        output = subprocess.check_output(
            cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=120
        )
        return output.strip()[-3000:] or "(sin salida)"
    except subprocess.CalledProcessError as e:
        return f"Error (código {e.returncode}):\n{e.output.strip()[-2000:]}"
    except subprocess.TimeoutExpired:
        return "Timeout: el comando tardó más de 120 segundos"

def _dropbox_subir(ruta_local: str, destino: str) -> str:
    try:
        from dropbox_service import subir_archivo
        return subir_archivo(ruta_local, destino)
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Error subiendo archivo: {e}"

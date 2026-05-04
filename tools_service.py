"""Herramientas que Mollo puede ejecutar — web, VPS, n8n, memoria, divisas."""
import subprocess, json
import httpx
from config import N8N_URL, N8N_WEBHOOK_SECRET, BANXICO_TOKEN

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


# ── Ejecutores ────────────────────────────────────────────────────────────────

async def execute_tool(name: str, inputs: dict) -> str:
    try:
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


def _dropbox_subir(ruta_local: str, destino: str) -> str:
    try:
        from dropbox_service import subir_archivo
        return subir_archivo(ruta_local, destino)
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Error subiendo archivo: {e}"

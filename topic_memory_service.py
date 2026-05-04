"""
Memoria por temas especializados de Mollo.
Cada tema mantiene un resumen vivo + hechos clave + pendientes.
GPT-4o-mini actualiza los temas en background después de cada conversación.
"""
import json
from datetime import datetime
from pathlib import Path
from config import MEMORY_FILE

TOPICS_FILE = str(Path(MEMORY_FILE).parent / "mollo_topics.json")

# ── Definición de temas ───────────────────────────────────────────────────────

TOPICS: dict[str, dict] = {
    "finanzas": {
        "nombre": "Finanzas",
        "descripcion": "Presupuesto, ingresos, gastos, flujo de caja, tipo de cambio, inversiones, costos",
        "keywords": ["presupuesto", "dinero", "ingreso", "gasto", "factura", "dólar", "peso",
                     "tipo de cambio", "financiero", "flujo", "caja", "inversión", "costo",
                     "precio", "pago", "cobro", "deuda", "utilidad", "margen", "conversión"],
    },
    "estrategia": {
        "nombre": "Estrategia",
        "descripcion": "Objetivos de negocio, OKRs, KPIs, planes de crecimiento, ventaja competitiva",
        "keywords": ["estrategia", "objetivo", "meta", "plan", "okr", "kpi", "competencia",
                     "mercado", "crecimiento", "visión", "misión", "diferenciador", "modelo de negocio"],
    },
    "ventas": {
        "nombre": "Ventas",
        "descripcion": "Clientes, pipeline, propuestas, contratos, revenue, cierre de ventas",
        "keywords": ["cliente", "venta", "propuesta", "pipeline", "revenue", "contrato",
                     "lead", "cierre", "comercial", "prospecto", "negociación", "oferta"],
    },
    "vps_infra": {
        "nombre": "VPS e Infraestructura",
        "descripcion": "Servidor VPS, Docker, servicios activos, configuración técnica, deployments",
        "keywords": ["vps", "servidor", "docker", "contenedor", "cpu", "ram", "disco",
                     "servicio", "puerto", "nginx", "deploy", "logs", "proceso", "memoria"],
    },
    "rrhh": {
        "nombre": "RRHH y Equipo",
        "descripcion": "Equipo, colaboradores, sueldos, contrataciones, evaluaciones, organización",
        "keywords": ["empleado", "equipo", "colaborador", "sueldo", "nómina", "contratación",
                     "rrhh", "recursos humanos", "vacaciones", "evaluación", "organigrama"],
    },
    "proyectos": {
        "nombre": "Proyectos",
        "descripcion": "Proyectos activos, tareas, deadlines, hitos, blockers, prioridades",
        "keywords": ["proyecto", "tarea", "deadline", "entrega", "milestone", "sprint",
                     "blocker", "prioridad", "pendiente", "avance", "desarrollo", "lanzamiento"],
    },
    "automatizacion": {
        "nombre": "Automatización e IA",
        "descripcion": "Mollo, n8n, workflows, integraciones, IA aplicada, bots",
        "keywords": ["automatización", "workflow", "n8n", "mollo", "bot", "integración",
                     "ia", "inteligencia artificial", "script", "api", "webhook"],
    },
    "iso9001": {
        "nombre": "ISO 9001 / Calidad",
        "descripcion": "Procesos de calidad, auditorías, procedimientos, certificaciones, normas",
        "keywords": ["iso", "calidad", "proceso", "auditoría", "procedimiento",
                     "norma", "certificación", "documentación", "registro", "no conformidad"],
    },
}


# ── Carga y guardado ──────────────────────────────────────────────────────────

def _load() -> dict:
    if not Path(TOPICS_FILE).exists():
        return {key: _empty_topic(key) for key in TOPICS}
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Asegurar que todos los temas existan aunque se añadan nuevos
    for key in TOPICS:
        if key not in data:
            data[key] = _empty_topic(key)
    return data


def _save(data: dict):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _empty_topic(key: str) -> dict:
    return {
        "resumen": "",
        "hechos_clave": [],
        "pendientes": [],
        "actualizado": None,
        "conversaciones_procesadas": 0,
    }


# ── Detección de temas ────────────────────────────────────────────────────────

def detect_topics(text: str) -> list[str]:
    """Detecta qué temas toca el texto usando keywords. Rápido y sin costo."""
    text_lower = text.lower()
    detected = []
    for key, topic in TOPICS.items():
        if any(kw in text_lower for kw in topic["keywords"]):
            detected.append(key)
    return detected or ["estrategia"]  # fallback genérico


# ── Lectura de memorias ───────────────────────────────────────────────────────

def get_topic_memories(topic_keys: list[str]) -> str:
    """Devuelve las memorias de los temas dados, listas para inyectar al prompt."""
    data = _load()
    parts = []
    for key in topic_keys:
        topic_data = data.get(key, {})
        if not topic_data.get("resumen"):
            continue
        nombre = TOPICS[key]["nombre"]
        lines = [f"### {nombre}"]
        lines.append(topic_data["resumen"])
        if topic_data.get("hechos_clave"):
            lines.append("Hechos clave:")
            for h in topic_data["hechos_clave"][-5:]:
                lines.append(f"  • {h}")
        if topic_data.get("pendientes"):
            lines.append("Pendientes/abiertos:")
            for p in topic_data["pendientes"]:
                lines.append(f"  ◦ {p}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def get_all_topic_memories() -> dict:
    return _load()


def get_topic_summary(key: str) -> dict:
    data = _load()
    return data.get(key, _empty_topic(key))


# ── Actualización de memorias (GPT-4o-mini) ───────────────────────────────────

def update_topic_memory(topic_key: str, user_msg: str, mollo_response: str):
    """
    Sintetiza la conversación con la memoria existente del tema.
    Usa GPT-4o-mini si hay créditos, Claude Haiku como fallback automático.
    """
    from openai_service import aux_json_call

    data = _load()
    current = data.get(topic_key, _empty_topic(topic_key))
    nombre = TOPICS[topic_key]["nombre"]

    resumen_actual      = current.get("resumen") or "(sin memoria previa)"
    hechos_actuales     = current.get("hechos_clave", [])
    pendientes_actuales = current.get("pendientes", [])

    prompt = f"""Eres el sistema de memoria de Mollo, asistente de Adolfo.

TEMA: {nombre}
DESCRIPCIÓN: {TOPICS[topic_key]['descripcion']}

MEMORIA ACTUAL:
Resumen: {resumen_actual}
Hechos clave: {json.dumps(hechos_actuales, ensure_ascii=False)}
Pendientes: {json.dumps(pendientes_actuales, ensure_ascii=False)}

NUEVA CONVERSACIÓN:
Adolfo: {user_msg}
Mollo: {mollo_response[:600]}

Actualiza la memoria integrando la nueva información. Responde SOLO con JSON:
{{
  "resumen": "Párrafo conciso (máx 4 oraciones) con lo más importante que Mollo sabe de este tema",
  "hechos_clave": ["hecho específico con fecha si aplica"],
  "pendientes": ["acción o decisión abierta sin resolver"]
}}

Reglas: resumen útil para retomar el tema desde cero. Hechos máx 8, pendientes máx 5. Elimina obsoletos. Si no hay nada nuevo, devuelve memoria sin cambios."""

    updated = aux_json_call(prompt, max_tokens=600)
    if not updated:
        return

    data[topic_key] = {
        "resumen":     updated.get("resumen", resumen_actual),
        "hechos_clave": updated.get("hechos_clave", hechos_actuales),
        "pendientes":  updated.get("pendientes", pendientes_actuales),
        "actualizado": datetime.now().isoformat(),
        "conversaciones_procesadas": current.get("conversaciones_procesadas", 0) + 1,
    }
    _save(data)


def update_topics_background(user_msg: str, mollo_response: str):
    """Detecta temas tocados y actualiza cada uno. Llamar en background."""
    topics = detect_topics(f"{user_msg} {mollo_response}")
    for key in topics:
        update_topic_memory(key, user_msg, mollo_response)


def clear_topic(key: str):
    """Resetea la memoria de un tema específico."""
    data = _load()
    if key in data:
        data[key] = _empty_topic(key)
        _save(data)


def manual_update_topic(key: str, resumen: str, hechos: list[str], pendientes: list[str]):
    """Actualización manual de un tema (para el router de memoria)."""
    data = _load()
    data[key] = {
        "resumen": resumen,
        "hechos_clave": hechos,
        "pendientes": pendientes,
        "actualizado": datetime.now().isoformat(),
        "conversaciones_procesadas": data.get(key, {}).get("conversaciones_procesadas", 0),
    }
    _save(data)

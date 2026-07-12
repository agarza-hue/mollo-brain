"""Code RAG via Mollo AI-OS router (F-3 federation).

Llama a /rag/query con el tenant del request para traer chunks de código
indexados por el tenant. Falla silenciosamente: si mollo-aios no responde
o la colección está vacía, devuelve "" y el chat continúa sin código RAG.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from config import MOLLO_AIOS_URL, MOLLO_AIOS_KEY, MOLLO_AIOS_TIMEOUT

log = logging.getLogger("code_rag_service")

# Colección por defecto para el código de un tenant.
# El tenant indexa su repo bajo este nombre con POST /rag/index.
_DEFAULT_CODE_COLL = os.environ.get("MOLLO_CODE_RAG_COLLECTION", "kb")


def fetch_code_context(
    query: str,
    tenant_slug: str,
    collection: str = _DEFAULT_CODE_COLL,
    k: int = 4,
) -> str:
    """Devuelve contexto de código RAG para el tenant; "" si vacío/error."""
    url = f"{MOLLO_AIOS_URL.rstrip('/')}/rag/query"
    payload = json.dumps({
        "query": query,
        "collection": collection,
        "tenant": tenant_slug,
        "k": k,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if MOLLO_AIOS_KEY:
        headers["Authorization"] = f"Bearer {MOLLO_AIOS_KEY}"

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=MOLLO_AIOS_TIMEOUT) as resp:
            raw = resp.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.debug("code_rag timeout/error for tenant=%s: %s", tenant_slug, e)
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    return str(data.get("context") or "")

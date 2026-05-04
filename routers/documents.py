"""Endpoints para gestión de documentos e importación de historial ChatGPT."""
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import os, tempfile

from document_service import save_document, process_document, list_documents, delete_document, extract_text
from qdrant_service import upsert_vectors, delete_by_source, collection_stats
from embeddings import get_embeddings_batch
from config import CATEGORIAS

router = APIRouter(prefix="/docs", tags=["Documentos"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    categoria: str = Form("general"),
):
    if categoria not in CATEGORIAS:
        raise HTTPException(400, f"Categoría inválida. Opciones: {CATEGORIAS}")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Archivo vacío")

    # Guardar en disco
    file_path = save_document(content, file.filename, categoria)

    # Extraer texto y chunks
    records = process_document(file_path, file.filename, categoria)
    if not records:
        raise HTTPException(422, "No se pudo extraer texto del documento")

    # Generar embeddings
    texts = [r["text"] for r in records]
    embeddings = await get_embeddings_batch(texts)

    # Guardar en Qdrant
    upsert_vectors(records, embeddings)

    return {
        "status": "ok",
        "archivo": file.filename,
        "categoria": categoria,
        "chunks_indexados": len(records),
        "ruta": file_path,
    }


@router.get("/list")
def get_documents():
    return {"documentos": list_documents()}


@router.delete("/{categoria}/{filename}")
def remove_document(categoria: str, filename: str):
    deleted_file = delete_document(filename, categoria)
    if deleted_file:
        delete_by_source(filename)
        return {"status": "ok", "eliminado": filename}
    raise HTTPException(404, "Documento no encontrado")


# ── Importación de historial ChatGPT ─────────────────────────────────────────

@router.post("/import/chatgpt")
async def import_chatgpt_history(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
):
    """
    Importa el export de ChatGPT (conversations.json o ZIP completo).
    Cómo obtenerlo: ChatGPT → Ajustes → Controles de datos → Exportar datos.
    """
    from chatgpt_importer import import_chatgpt
    import tempfile, os

    suffix = ".zip" if file.filename.endswith(".zip") else ".json"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = await import_chatgpt(tmp_path, verbose=False)
    finally:
        os.unlink(tmp_path)

    return {
        "status": "ok",
        "conversaciones_importadas": result["conversaciones"],
        "vectores_indexados":        result["chunks_indexados"],
        "coleccion":                 result["coleccion"],
        "mensaje": f"Historial de ChatGPT indexado. Mollo ahora puede buscar en {result['conversaciones']} conversaciones.",
    }


@router.post("/telegram")
async def upload_from_telegram(
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    """
    Recibe un documento enviado por Telegram:
    1. Detecta categoría automáticamente (GPT-4o-mini)
    2. Guarda localmente e indexa en Qdrant
    3. Sube a Dropbox en /Mollo/Documentos/{categoria}/YYYY-MM/
    4. Genera análisis con GPT-4o
    """
    from openai_service import aux_json_call
    from dropbox_service import subir_bytes
    from openai_brain import chat_openai, GPT_4O

    content = await file.read()
    if not content:
        raise HTTPException(400, "Archivo vacío")

    filename = file.filename or "documento"
    suffix   = Path(filename).suffix.lower()

    supported = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".csv", ".md"}
    if suffix not in supported:
        raise HTTPException(415, f"Formato '{suffix}' no soportado. Válidos: {', '.join(supported)}")

    # 1. Extraer texto (necesitamos temp file para document_service)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        texto = extract_text(tmp_path)
    except Exception:
        texto = ""
    finally:
        os.unlink(tmp_path)

    # 2. Detectar categoría
    categoria = _detectar_categoria(filename, caption, texto, aux_json_call)

    # 3. Guardar localmente e indexar en Qdrant
    file_path  = save_document(content, filename, categoria)
    records    = process_document(file_path, filename, categoria)
    chunks_count = 0
    if records:
        texts      = [r["text"] for r in records]
        embeddings = await get_embeddings_batch(texts)
        upsert_vectors(records, embeddings)
        chunks_count = len(records)

    # 4. Subir a Dropbox
    mes           = datetime.now().strftime("%Y-%m")
    ruta_dropbox  = f"Mollo/Documentos/{categoria}/{mes}/{filename}"
    try:
        dropbox_resultado = subir_bytes(content, filename, ruta_dropbox)
    except Exception as e:
        dropbox_resultado = f"Error Dropbox: {e}"

    # 5. Análisis con GPT-4o
    if texto.strip():
        prompt = (
            f"Analiza este documento empresarial '{filename}':\n\n"
            f"{texto[:4000]}\n\n"
            "Responde con:\n"
            "**Resumen ejecutivo** (2-3 líneas)\n"
            "**Puntos clave** (3-5 bullets)\n"
            "**Acción recomendada**"
        )
        try:
            analisis = chat_openai(prompt, model=GPT_4O)
        except Exception as e:
            analisis = f"No se pudo generar análisis: {e}"
    else:
        analisis = "No se pudo extraer texto del documento para análisis."

    return {
        "status":            "ok",
        "archivo":           filename,
        "categoria":         categoria,
        "ruta_dropbox":      f"/Mollo/Documentos/{categoria}/{mes}/{filename}",
        "chunks_indexados":  chunks_count,
        "dropbox_resultado": dropbox_resultado,
        "analisis":          analisis,
    }


def _detectar_categoria(filename: str, caption: str, texto: str, aux_json_call) -> str:
    # El usuario puede indicar la categoría en el caption directamente
    caption_lower = caption.lower()
    for cat in CATEGORIAS:
        if cat in caption_lower:
            return cat

    prompt = (
        f"Clasifica este documento empresarial en UNA de estas categorías:\n"
        f"{', '.join(CATEGORIAS)}\n\n"
        f"Archivo: {filename}\n"
        f"Caption del usuario: {caption or 'ninguno'}\n"
        f"Preview del contenido: {texto[:400] if texto else 'N/D'}\n\n"
        f'Responde SOLO JSON: {{"categoria": "CATEGORIA"}}'
    )
    result = aux_json_call(prompt, max_tokens=50)
    cat = result.get("categoria", "general").lower().strip()
    return cat if cat in CATEGORIAS else "general"


@router.get("/import/chatgpt/status")
def chatgpt_import_status():
    """Estado de la colección de historial ChatGPT."""
    stats = collection_stats()
    vectores = stats.get("chatgpt_vectores", 0)
    return {
        "vectores_indexados": vectores,
        "historial_disponible": vectores > 0,
        "mensaje": f"{vectores} fragmentos de ChatGPT indexados" if vectores > 0 else "Historial no importado aún",
    }

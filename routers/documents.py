"""Endpoints para gestión de documentos."""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
import os, tempfile

from document_service import save_document, process_document, list_documents, delete_document
from qdrant_service import upsert_vectors, delete_by_source
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

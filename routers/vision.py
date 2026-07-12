"""
NanoBanana Vision API — análisis de fotos y videos con Gemini.

Endpoints:
  POST /vision/analyze      → analiza un archivo subido
  POST /vision/scan-folder  → escanea carpeta local del VPS
  POST /vision/scan-dropbox → escanea carpeta de Dropbox
  GET  /vision/models       → modelos de visión disponibles
"""

import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import gemini_vision as gv

router = APIRouter(prefix="/vision", tags=["NanoBanana Vision"])

ALLOWED_MEDIA = gv.IMAGE_EXTS | gv.VIDEO_EXTS


# ─── Modelos Pydantic ────────────────────────────────────────────────────────

class ScanFolderRequest(BaseModel):
    folder: str
    recursive: bool = False
    model: str = gv.NANO_BANANA_MODEL
    extensions: list[str] | None = None


class ScanDropboxRequest(BaseModel):
    folder: str = "/"
    model: str = gv.NANO_BANANA_MODEL
    extensions: list[str] | None = None
    limit: int = 20


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/models")
def list_vision_models():
    return {
        "modelos": [
            {
                "id": gv.NANO_BANANA_MODEL,
                "nombre": "NanoBanana Pro",
                "descripcion": "Modelo especializado en visión de alta calidad",
                "soporta": ["foto", "video"],
            },
            {
                "id": gv.FLASH_FALLBACK,
                "nombre": "Gemini 2.5 Flash",
                "descripcion": "Fallback rápido y económico",
                "soporta": ["foto", "video"],
            },
        ]
    }


@router.post("/analyze")
async def analyze_file(
    file: UploadFile = File(...),
    model: str = Form(gv.NANO_BANANA_MODEL),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_MEDIA:
        raise HTTPException(
            400,
            f"Formato no soportado: '{ext}'. "
            f"Fotos: {sorted(gv.IMAGE_EXTS)} | Videos: {sorted(gv.VIDEO_EXTS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(400, "Archivo vacío")

    suffix = ext or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = gv.analyze_media(tmp_path, model=model)
        result["archivo"] = file.filename
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(500, f"Error en análisis: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/scan-folder")
def scan_folder(req: ScanFolderRequest):
    folder = Path(req.folder)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(404, f"Carpeta no encontrada: {req.folder}")

    exts = {e if e.startswith(".") else f".{e}" for e in req.extensions} if req.extensions else None
    results = gv.scan_folder(folder, recursive=req.recursive, model=req.model, extensions=exts)
    return {"carpeta": str(folder), "analizados": len(results), "resultados": results}


@router.post("/scan-dropbox")
def scan_dropbox(req: ScanDropboxRequest):
    exts = {e if e.startswith(".") else f".{e}" for e in req.extensions} if req.extensions else None
    results = gv.scan_dropbox(
        dropbox_folder=req.folder,
        model=req.model,
        extensions=exts,
        limit=req.limit,
    )
    return {"dropbox_folder": req.folder, "analizados": len(results), "resultados": results}

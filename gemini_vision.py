"""
NanoBanana Vision — análisis de fotos y videos con Google nano-banana-pro-preview.

Capacidades:
  - Fotos: JPEG, PNG, WebP, HEIC, GIF  (inline hasta 20MB)
  - Videos: MP4, MOV, AVI, MKV, WEBM   (Files API, hasta 2GB / 1h)
  - Escaneo de carpeta local o Dropbox
  - Salida estructurada: puntuación 1-10 + análisis por dimensión

Modelos:
  - nano-banana-pro-preview  → análisis visual principal (imagen + video)
  - gemini-2.5-flash         → fallback barato si NanoBanana no disponible
"""

import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from config import GEMINI_API_KEY

# ─── Clientes ────────────────────────────────────────────────────────────────

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


# ─── Constantes ──────────────────────────────────────────────────────────────

NANO_BANANA_MODEL = "models/nano-banana-pro-preview"
FLASH_FALLBACK    = "models/gemini-2.5-flash"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

IMAGE_INLINE_LIMIT = 20 * 1024 * 1024  # 20 MB

ANALYSIS_PROMPT = """Eres un experto en fotografía y producción audiovisual.
Analiza este archivo multimedia y devuelve ÚNICAMENTE un JSON válido con esta estructura exacta:

{
  "tipo": "foto" | "video",
  "puntuacion_global": <1-10, float>,
  "dimensiones": {
    "composicion":   { "puntuacion": <1-10>, "comentario": "<texto breve>" },
    "nitidez":       { "puntuacion": <1-10>, "comentario": "<texto breve>" },
    "iluminacion":   { "puntuacion": <1-10>, "comentario": "<texto breve>" },
    "colores":       { "puntuacion": <1-10>, "comentario": "<texto breve>" },
    "encuadre":      { "puntuacion": <1-10>, "comentario": "<texto breve>" }
  },
  "descripcion": "<descripción visual en 1-2 oraciones>",
  "sugerencias": ["<mejora 1>", "<mejora 2>"],
  "etiquetas": ["<tag1>", "<tag2>", "<tag3>"]
}

Para videos, agrega dentro de "dimensiones":
    "movimiento":    { "puntuacion": <1-10>, "comentario": "<texto breve>" },
    "audio":         { "puntuacion": <1-10>, "comentario": "<texto breve>" }

Responde SOLO el JSON, sin markdown ni explicaciones."""


# ─── Análisis de imagen (inline) ─────────────────────────────────────────────

def analyze_image(path: str | Path, model: str = NANO_BANANA_MODEL) -> dict[str, Any]:
    path = Path(path)
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    data = path.read_bytes()

    client = _get_client()
    resp = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=data, mime_type=mime),
            ANALYSIS_PROMPT,
        ],
    )
    raw = resp.text or "{}"
    result = _parse_json(raw)
    result["archivo"] = path.name
    result["modelo"]  = model
    result["tokens"]  = _extract_usage(resp)
    return result


# ─── Análisis de video (Files API) ───────────────────────────────────────────

def analyze_video(path: str | Path, model: str = NANO_BANANA_MODEL) -> dict[str, Any]:
    path   = Path(path)
    mime   = mimetypes.guess_type(str(path))[0] or "video/mp4"
    client = _get_client()

    uploaded = client.files.upload(file=path, config={"mime_type": mime})

    # Esperar procesamiento (estado ACTIVE)
    for _ in range(60):
        f = client.files.get(name=uploaded.name)
        state = str(getattr(f, "state", "")).upper()
        if "ACTIVE" in state:
            break
        if "FAILED" in state:
            raise RuntimeError(f"Gemini Files procesamiento fallido: {path.name}")
        time.sleep(2)

    file_part = types.Part.from_uri(file_uri=f.uri, mime_type=mime)
    resp = client.models.generate_content(
        model=model,
        contents=[file_part, ANALYSIS_PROMPT],
    )

    # Limpiar archivo remoto
    try:
        client.files.delete(name=f.name)
    except Exception:
        pass

    raw = resp.text or "{}"
    result = _parse_json(raw)
    result["archivo"] = path.name
    result["modelo"]  = model
    result["tokens"]  = _extract_usage(resp)
    return result


# ─── Análisis genérico (foto o video por extensión) ──────────────────────────

def analyze_media(path: str | Path, model: str = NANO_BANANA_MODEL) -> dict[str, Any]:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in VIDEO_EXTS:
        return analyze_video(p, model)
    return analyze_image(p, model)


# ─── Escaneo de carpeta local ────────────────────────────────────────────────

def scan_folder(
    folder: str | Path,
    recursive: bool = False,
    model: str = NANO_BANANA_MODEL,
    extensions: set[str] | None = None,
) -> list[dict[str, Any]]:
    folder = Path(folder)
    exts   = extensions or MEDIA_EXTS
    pattern = "**/*" if recursive else "*"
    files   = [f for f in folder.glob(pattern) if f.is_file() and f.suffix.lower() in exts]

    results = []
    for f in sorted(files):
        try:
            r = analyze_media(f, model)
            r["ruta"] = str(f)
            results.append(r)
        except Exception as e:
            results.append({"archivo": f.name, "ruta": str(f), "error": str(e)})
    return results


# ─── Escaneo de Dropbox ──────────────────────────────────────────────────────

def scan_dropbox(
    dropbox_folder: str = "/",
    model: str = NANO_BANANA_MODEL,
    extensions: set[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    from dropbox_service import get_client as get_dropbox
    import dropbox as dbx_sdk
    import tempfile

    exts    = extensions or IMAGE_EXTS  # por defecto solo fotos en Dropbox (más rápido)
    client  = get_dropbox()
    results = []

    try:
        listing = client.files_list_folder(dropbox_folder)
    except Exception as e:
        return [{"error": f"Dropbox listing falló: {e}"}]

    entries = listing.entries
    while listing.has_more and len(entries) < limit:
        listing = client.files_list_folder_continue(listing.cursor)
        entries.extend(listing.entries)

    media_entries = [
        e for e in entries
        if isinstance(e, dbx_sdk.files.FileMetadata)
        and Path(e.name).suffix.lower() in exts
    ][:limit]

    for entry in media_entries:
        try:
            _, resp = client.files_download(entry.path_lower)
            suffix  = Path(entry.name).suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name

            r = analyze_image(tmp_path, model)
            r["archivo"]       = entry.name
            r["dropbox_path"]  = entry.path_lower
            r["dropbox_size"]  = entry.size
            results.append(r)
        except Exception as e:
            results.append({"archivo": entry.name, "dropbox_path": entry.path_lower, "error": str(e)})
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return results


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict:
    text = text.strip()
    # Quitar posibles bloques ```json ... ```
    if text.startswith("```"):
        lines = text.splitlines()
        text  = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _extract_usage(resp) -> dict:
    meta    = getattr(resp, "usage_metadata", None)
    in_tok  = getattr(meta, "prompt_token_count", 0) or 0
    out_tok = getattr(meta, "candidates_token_count", 0) or 0
    cached  = getattr(meta, "cached_content_token_count", 0) or 0
    return {
        "input":  max(0, in_tok - cached),
        "output": out_tok,
        "cached": cached,
    }

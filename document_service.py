"""Procesamiento de documentos: PDF, Word, Excel, TXT → chunks."""
import os, uuid, shutil
from pathlib import Path
from typing import Optional

import pdfplumber
import docx
import openpyxl

from config import DOCS_PATH


CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


def extract_text_pdf(path: str) -> str:
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
    return "\n".join(pages)


def extract_text_docx(path: str) -> str:
    doc = docx.Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text_xlsx(path: str) -> str:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = []
    for sheet in wb.worksheets:
        rows.append(f"[Hoja: {sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            line = " | ".join(str(c) for c in row if c is not None)
            if line.strip():
                rows.append(line)
    return "\n".join(rows)


def extract_text_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def extract_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return extract_text_pdf(path)
    elif ext in (".docx", ".doc"):
        return extract_text_docx(path)
    elif ext in (".xlsx", ".xls"):
        return extract_text_xlsx(path)
    else:
        return extract_text_txt(path)


def save_document(file_bytes: bytes, filename: str, categoria: str) -> str:
    """Guarda el archivo en la carpeta correspondiente y devuelve la ruta."""
    dest_dir = Path(DOCS_PATH) / categoria
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    with open(dest, "wb") as f:
        f.write(file_bytes)
    return str(dest)


def process_document(file_path: str, filename: str, categoria: str) -> list[dict]:
    """Extrae texto, parte en chunks y devuelve lista de registros para Qdrant."""
    text = extract_text(file_path)
    chunks = _chunk_text(text)
    records = []
    for i, chunk in enumerate(chunks):
        records.append({
            "id": str(uuid.uuid4()),
            "text": chunk,
            "payload": {
                "source": filename,
                "categoria": categoria,
                "chunk": i,
                "total_chunks": len(chunks),
                "file_path": file_path,
            }
        })
    return records


def list_documents() -> list[dict]:
    """Lista todos los documentos almacenados con su categoría."""
    docs = []
    base = Path(DOCS_PATH)
    for cat_dir in base.iterdir():
        if cat_dir.is_dir():
            for f in cat_dir.iterdir():
                if f.is_file():
                    docs.append({
                        "nombre": f.name,
                        "categoria": cat_dir.name,
                        "tamaño_kb": round(f.stat().st_size / 1024, 1),
                        "ruta": str(f)
                    })
    return docs


def delete_document(filename: str, categoria: str) -> bool:
    path = Path(DOCS_PATH) / categoria / filename
    if path.exists():
        path.unlink()
        return True
    return False

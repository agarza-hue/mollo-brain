"""
Mollo Brain — API de inteligencia empresarial con RAG
Puerto: 8002
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from qdrant_service import ensure_collection, ensure_memory_collection, collection_stats
from routers import documents, chat, memory, vps


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_collection()
    ensure_memory_collection()
    yield


app = FastAPI(
    title="Mollo Brain API",
    description="Inteligencia empresarial de Mollo — RAG + Memoria + Claude",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(vps.router)


@app.get("/")
def root():
    return {"status": "Mollo está vivo", "version": "1.0.0"}


@app.get("/health")
def health():
    stats = collection_stats()
    return {
        "status": "ok",
        "qdrant": stats,
        "message": "Mollo Brain operativo",
    }


if __name__ == "__main__":
    import uvicorn
    from config import PORT
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)

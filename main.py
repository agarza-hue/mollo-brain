"""
Mollo Brain — API de inteligencia empresarial con RAG
Puerto: 8002
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

from qdrant_service import ensure_collection, ensure_memory_collection, ensure_chatgpt_collection, collection_stats
from routers import documents, chat, memory, vps, tasks, costs, limits, tenants
from routers import auth_router, convs_router, workspaces, events
from routers import billing, molloia_billing, claude_ai_usage, graph
from routers import pai
from routers import vision


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_collection()
    ensure_memory_collection()
    ensure_chatgpt_collection()
    yield


app = FastAPI(
    title="Mollo Brain API",
    description="Inteligencia empresarial de Mollo — RAG + Memoria + Claude",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

import os as _os
_EXTRA_ORIGINS = [o.strip() for o in _os.environ.get("CORS_EXTRA_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    # M-14: orígenes explícitos — Tailscale VPS + WSL + localhost (cualquier puerto)
    allow_origins=_EXTRA_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|100\.100\.86\.65|100\.76\.21\.73)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(convs_router.router)
app.include_router(workspaces.router)
app.include_router(events.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(vps.router)
app.include_router(tasks.router)
app.include_router(costs.router)
app.include_router(limits.router)
app.include_router(tenants.router)
app.include_router(billing.router)
app.include_router(molloia_billing.router)
app.include_router(claude_ai_usage.router)
app.include_router(graph.router)
app.include_router(pai.router)
app.include_router(vision.router)


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

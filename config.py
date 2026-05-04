import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
QDRANT_HOST       = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_COLLECTION        = os.getenv("QDRANT_COLLECTION", "mollo_empresa")
QDRANT_MEMORY_COLLECTION = os.getenv("QDRANT_MEMORY_COLLECTION", "mollo_memoria")
OLLAMA_HOST       = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL       = os.getenv("EMBED_MODEL", "nomic-embed-text")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
DOCS_PATH         = os.getenv("DOCS_PATH", "/root/mollo_docs")
MEMORY_FILE       = os.getenv("MEMORY_FILE", "/root/mollo_brain/mollo_memory.json")
PORT              = int(os.getenv("PORT", 8002))

CATEGORIAS = [
    "financiero", "estrategia", "rrhh", "ventas",
    "operaciones", "general", "iso9001", "contratos"
]

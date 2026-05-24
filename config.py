import os
from dotenv import load_dotenv

load_dotenv(override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
QDRANT_HOST       = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_COLLECTION        = os.getenv("QDRANT_COLLECTION", "mollo_empresa")
QDRANT_MEMORY_COLLECTION = os.getenv("QDRANT_MEMORY_COLLECTION", "mollo_memoria")
# Aislamiento de datos por usuario (MolloIA). OFF por defecto → comportamiento
# idéntico al actual (todos comparten las colecciones legacy de arriba). ON →
# cada usuario MolloIA tiene sus propias colecciones; el owner sigue mapeado a
# las legacy para preservar sus datos.
PER_USER_ISOLATION       = os.getenv("PER_USER_ISOLATION", "false").lower() == "true"
OWNER_USER_ID            = os.getenv("OWNER_USER_ID", "")
OLLAMA_HOST       = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b")
EMBED_MODEL       = os.getenv("EMBED_MODEL", "nomic-embed-text")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL_AUX  = os.getenv("OPENAI_MODEL_AUX", "gpt-4o-mini")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY")
LLAMA8B_MODEL     = os.getenv("LLAMA8B_MODEL", "llama-3.1-8b-instant")
LLAMA70B_MODEL    = os.getenv("LLAMA70B_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY          = os.getenv("GEMINI_API_KEY")
GEMINI_FLASH_LITE_MODEL = os.getenv("GEMINI_FLASH_LITE_MODEL", "gemini-2.5-flash-lite")
GEMINI_PRO_MODEL        = os.getenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")
DOCS_PATH         = os.getenv("DOCS_PATH", "/root/mollo_docs")
MEMORY_FILE       = os.getenv("MEMORY_FILE", "/root/mollo_brain/mollo_memory.json")
PORT              = int(os.getenv("PORT", 8002))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
N8N_URL            = os.getenv("N8N_URL", "")
N8N_WEBHOOK_SECRET = os.getenv("N8N_WEBHOOK_SECRET", "")
BANXICO_TOKEN      = os.getenv("BANXICO_TOKEN", "")
DROPBOX_APP_KEY      = os.getenv("DROPBOX_APP_KEY", "")
DROPBOX_APP_SECRET   = os.getenv("DROPBOX_APP_SECRET", "")
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN", "")

CATEGORIAS = [
    "financiero", "estrategia", "rrhh", "ventas",
    "operaciones", "general", "iso9001", "contratos"
]

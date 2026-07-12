import asyncio
import httpx
from config import EMBED_HOST, EMBED_MODEL


async def get_embedding(text: str) -> list[float]:
    """Genera embedding usando nomic-embed-text via Ollama.

    Trunca a 8000 chars (~2k tokens) antes de enviar — nomic-embed-text
    soporta hasta 8192 tokens pero el server local choca con prompts muy
    largos. La parte ofuscada va al pregunta del RAG, no a la respuesta,
    así que truncar es seguro para retrieval (los primeros 8K capturan el
    intent del query)."""
    # Ollama embedding server (nomic-embed-text) tira 500 ≥~6k chars.
    # 4000 chars ≈ 1k tokens cubre query intent sin tirar el endpoint.
    truncated = text[:4000]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{EMBED_HOST}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": truncated}
        )
        r.raise_for_status()
        return r.json()["embedding"]


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    return list(await asyncio.gather(*[get_embedding(t) for t in texts]))

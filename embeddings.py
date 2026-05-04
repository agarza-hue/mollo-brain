import asyncio
import httpx
from config import OLLAMA_HOST, EMBED_MODEL


async def get_embedding(text: str) -> list[float]:
    """Genera embedding usando nomic-embed-text via Ollama."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text}
        )
        r.raise_for_status()
        return r.json()["embedding"]


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    return list(await asyncio.gather(*[get_embedding(t) for t in texts]))

"""
Endpoint /graph/data — devuelve nodes + edges del knowledge graph de Mollo.

Construye el grafo de conexiones semánticas automáticas entre todo lo
indexado en Qdrant: vault Obsidian, readwise, docs empresa, memoria de
conversaciones. Los edges NO son links manuales `[[wiki]]` como Obsidian —
son similitud coseno entre embeddings (Ollama nomic-embed-text).

Performance: para N chunks, hacemos N queries a Qdrant pidiendo top-K
similares. N≈480 hoy, K=5 → 2400 edges max. ~3-5s para construir el
grafo completo (mayoritariamente latencia de Qdrant). Cacheado in-memory
con TTL 1h, refresh manual via ?refresh=true.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import time
import logging

from qdrant_service import client as qclient
from config import QDRANT_COLLECTION, QDRANT_MEMORY_COLLECTION

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["Graph"])

# Cache in-memory
_cache: dict = {"data": None, "ts": 0.0, "params_key": ""}
TTL_SEC = 3600  # 1h

# Mapeo categoria → color (Catppuccin Mocha)
CATEGORIA_COLOR = {
    "vault":      "#cba6f7",  # mauve
    "readwise":   "#fab387",  # peach
    "general":    "#89dceb",  # sky
    "estrategia": "#f9e2af",  # yellow
    "financiero": "#a6e3a1",  # green
    "rrhh":       "#f5c2e7",  # pink
    "ventas":     "#94e2d5",  # teal
    "operaciones":"#89b4fa",  # blue
    "iso9001":    "#b4befe",  # lavender
    "contratos":  "#eba0ac",  # maroon
    "memoria":    "#f38ba8",  # red (chat history)
    "claude_code":"#f5e0dc",  # rosewater
    "external":   "#7f849c",  # overlay1 — ruido
    "default":    "#a6adc8",  # subtext0
}


def _color_for(cat: str | None) -> str:
    return CATEGORIA_COLOR.get(cat or "default", CATEGORIA_COLOR["default"])


def _build_graph(top_k_per_node: int = 5, min_score: float = 0.6,
                 max_nodes: int = 800) -> dict:
    """Construye nodes + edges scrolleando todo Qdrant + queries top-K por nodo."""
    t0 = time.monotonic()

    # ── Step 1: scroll todos los puntos de ambas colecciones ──
    all_points: list = []
    for collection in (QDRANT_COLLECTION, QDRANT_MEMORY_COLLECTION):
        next_offset = None
        collected = 0
        while True:
            try:
                pts, next_offset = qclient.scroll(
                    collection_name=collection,
                    limit=200,
                    offset=next_offset,
                    with_payload=True,
                    with_vectors=True,
                )
            except Exception as e:
                logger.warning("scroll fail %s: %s", collection, e)
                break
            for p in pts:
                p_meta = {
                    "id":         str(p.id),
                    "collection": collection,
                    "vector":     p.vector,
                    "payload":    p.payload or {},
                }
                all_points.append(p_meta)
                collected += 1
                if collected >= max_nodes:
                    break
            if next_offset is None or collected >= max_nodes:
                break

    logger.info("graph: scrolled %d points totales en %.2fs",
                len(all_points), time.monotonic() - t0)

    # ── Step 2: dedup por source + chunk para no inflar (1 nodo por chunk) ──
    # Usamos qdrant point id como clave única — ya está dedupeado.

    # ── Step 3: construir nodes ──
    nodes = []
    seen_ids = set()
    for p in all_points:
        nid = p["id"]
        if nid in seen_ids:
            continue
        seen_ids.add(nid)
        pl = p["payload"]
        # Source label: el filename cuando es archivo, primera línea cuando es chunk de chat
        src = pl.get("source") or pl.get("filename") or pl.get("title") or ""
        if src and "/" in src:
            short = src.rsplit("/", 1)[-1]
        else:
            short = src
        # Fallback para chunks sin source (memoria de chats típicamente).
        # Schema memoria: {usuario, mollo, mollo_summary, fecha, session_id}.
        # Schema RAG empresa: {source, text, categoria, ...}.
        if not short or short.strip() == "":
            candidates = [
                pl.get("text"),                    # docs RAG empresa
                pl.get("mollo_summary"),           # memoria de chats — preferir summary
                pl.get("usuario"),                 # memoria — pregunta del user
                pl.get("mollo"),                   # memoria — respuesta de Mollo
            ]
            for c in candidates:
                if c and isinstance(c, str) and c.strip():
                    txt = c.strip().replace("\n", " ")
                    short = (txt[:55] + "…") if len(txt) > 55 else txt
                    break
        if not short:
            short = "(sin título)"

        # Preview también soporta el schema dual
        preview_candidates = [
            pl.get("text"),
            pl.get("mollo_summary"),
            (pl.get("usuario") or "") + ("\n\nMollo: " + (pl.get("mollo") or "") if pl.get("mollo") else ""),
        ]
        preview = ""
        for c in preview_candidates:
            if c and isinstance(c, str) and c.strip():
                preview = c[:300]
                break

        cat = pl.get("categoria") or ("memoria" if p["collection"] == QDRANT_MEMORY_COLLECTION else "default")
        nodes.append({
            "id":        nid,
            "label":     short[:80],
            "categoria": cat,
            "color":     _color_for(cat),
            "source":    src or (f"chat:{pl.get('fecha','')[:10]}" if cat == "memoria" else ""),
            "preview":   preview,
            "collection": p["collection"],
        })

    # ── Step 4: edges via top-K similar para cada nodo ──
    # Por performance: solo computamos edges para nodos con vector, y deduplicamos
    # (a→b == b→a, mantener solo una dirección por par)
    edges_set = set()  # (id_a, id_b, score) con id_a < id_b
    for p in all_points:
        if not p["vector"]:
            continue
        try:
            res = qclient.query_points(
                collection_name=p["collection"],
                query=p["vector"],
                limit=top_k_per_node + 1,  # +1 porque el primero suele ser sí mismo
                with_payload=False,
            )
            results = res.points
        except Exception as e:
            logger.warning("query_points falló para %s: %s", p["id"], e)
            continue
        for r in results:
            tid = str(r.id)
            if tid == p["id"]:
                continue
            if r.score < min_score:
                continue
            # Dedup: par ordenado por id
            a, b = sorted([p["id"], tid])
            edges_set.add((a, b, round(float(r.score), 3)))

    # Deduplicar manteniendo el score más alto si aparece el mismo par dos veces
    edges_dict: dict = {}
    for a, b, s in edges_set:
        key = (a, b)
        if key not in edges_dict or s > edges_dict[key]:
            edges_dict[key] = s
    edges = [{"source": a, "target": b, "weight": s} for (a, b), s in edges_dict.items()]

    elapsed = time.monotonic() - t0
    logger.info("graph: %d nodes, %d edges en %.2fs", len(nodes), len(edges), elapsed)

    interpretation = _interpret(nodes, edges)

    return {
        "nodes":          nodes,
        "edges":          edges,
        "node_count":     len(nodes),
        "edge_count":     len(edges),
        "build_time_sec": round(elapsed, 2),
        "params":         {"top_k_per_node": top_k_per_node, "min_score": min_score, "max_nodes": max_nodes},
        "interpretation": interpretation,
    }


def _interpret(nodes: list, edges: list) -> dict:
    """Stats simples + recomendaciones rule-based para el panel del frontend.
    NO usa LLM — esto se llama en cada cache miss y debe ser instantáneo."""
    from collections import Counter, defaultdict
    if not nodes:
        return {
            "summary": "Cerebro vacío. Empieza a indexar contenido (vault, readwise, docs).",
            "stats": {}, "recommendations": [],
        }

    # ── Distribución por categoria ──
    cat_counts = Counter(n["categoria"] for n in nodes)
    total = len(nodes)
    dominante = cat_counts.most_common(1)[0]

    # ── Degree por nodo (cuántos edges toca) ──
    degree: dict = defaultdict(int)
    for e in edges:
        s = e["source"] if isinstance(e["source"], str) else e["source"].get("id")
        t = e["target"] if isinstance(e["target"], str) else e["target"].get("id")
        if s: degree[s] += 1
        if t: degree[t] += 1

    nodes_by_id = {n["id"]: n for n in nodes}

    # Top 3 nodos más conectados (hubs)
    top_hubs = sorted(degree.items(), key=lambda x: -x[1])[:3]
    hubs = []
    for nid, deg in top_hubs:
        n = nodes_by_id.get(nid)
        if n:
            hubs.append({
                "label":     n["label"],
                "categoria": n["categoria"],
                "degree":    deg,
                "source":    n.get("source", ""),
            })

    # Nodos huérfanos (degree 0)
    orphans = sum(1 for n in nodes if degree[n["id"]] == 0)

    # Densidad
    max_possible_edges = (total * (total - 1)) // 2
    density = (len(edges) / max_possible_edges * 100) if max_possible_edges else 0

    # ── Recomendaciones rule-based ──
    recs = []
    # 1. Categoría dominante > 70% → sesgo
    pct_dominante = (dominante[1] / total) * 100
    if pct_dominante > 70:
        recs.append({
            "type": "warning",
            "msg":  f"`{dominante[0]}` representa el {pct_dominante:.0f}% del cerebro. Diversifica las fuentes (vault, readwise, docs)."
        })

    # 2. Vault sub-representado
    vault_count = cat_counts.get("vault", 0)
    if vault_count < 5:
        recs.append({
            "type": "tip",
            "msg":  f"Solo {vault_count} chunks del vault Obsidian. Captura más notas en `inbox/`, `ideas/`, `notes/` para enriquecer el grafo."
        })

    # 3. Readwise sub-utilizado
    rw_count = cat_counts.get("readwise", 0)
    if rw_count < 10:
        recs.append({
            "type": "tip",
            "msg":  f"Solo {rw_count} highlights de Readwise. Empieza a guardar artículos y resaltar pasajes — cada highlight conecta tu memoria a literatura externa."
        })

    # 4. Huérfanos altos
    pct_orphans = (orphans / total) * 100
    if pct_orphans > 20:
        recs.append({
            "type": "warning",
            "msg":  f"{orphans} nodos ({pct_orphans:.0f}%) están aislados — sin conexiones semánticas. Probable: contenido off-topic o duplicados degradados. Sube `min_score` para filtrar."
        })

    # 5. Densidad muy alta
    if density > 5:
        recs.append({
            "type": "info",
            "msg":  f"Densidad alta ({density:.1f}%): grafo tupido. Sube `min_score` a 0.85+ para ver clusters reales."
        })
    elif density < 0.3:
        recs.append({
            "type": "info",
            "msg":  f"Densidad baja ({density:.2f}%): grafo disperso. Baja `min_score` para ver más conexiones débiles."
        })

    # 6. Hub principal — qué dice de tu thinking
    if hubs:
        top = hubs[0]
        recs.append({
            "type": "insight",
            "msg":  f"Tu hub principal es **{top['label']}** ({top['categoria']}, {top['degree']} conexiones). Es el centro de gravedad de tu thinking actual."
        })

    # ── Summary natural ──
    cats_resumen = ", ".join(f"{c[0]} ({c[1]})" for c in cat_counts.most_common(4))
    summary = (
        f"{total} nodos en {len(cat_counts)} categorías ({cats_resumen}). "
        f"{len(edges)} conexiones semánticas, densidad {density:.2f}%. "
        f"{orphans} huérfanos."
    )

    return {
        "summary":     summary,
        "stats": {
            "total_nodes":     total,
            "total_edges":     len(edges),
            "density_pct":     round(density, 3),
            "orphans":         orphans,
            "categorias":      dict(cat_counts.most_common()),
            "dominante_cat":   dominante[0],
            "dominante_pct":   round(pct_dominante, 1),
        },
        "hubs":            hubs,
        "recommendations": recs,
    }


@router.get("/data")
def get_graph(top_k_per_node: int = 5, min_score: float = 0.6,
              max_nodes: int = 800, refresh: bool = False):
    """Devuelve el grafo completo (cached 1h). ?refresh=true fuerza rebuild."""
    params_key = f"{top_k_per_node}|{min_score}|{max_nodes}"
    now = time.monotonic()
    if (not refresh
        and _cache["data"] is not None
        and _cache["params_key"] == params_key
        and (now - _cache["ts"]) < TTL_SEC):
        cached = dict(_cache["data"])
        cached["from_cache"] = True
        cached["cache_age_sec"] = round(now - _cache["ts"], 1)
        return cached

    data = _build_graph(top_k_per_node, min_score, max_nodes)
    _cache["data"] = data
    _cache["ts"] = now
    _cache["params_key"] = params_key
    data["from_cache"] = False
    return data


@router.get("/stats")
def graph_stats():
    """Stats rápidas sin construir el grafo completo."""
    stats: dict = {"collections": {}}
    for col in (QDRANT_COLLECTION, QDRANT_MEMORY_COLLECTION):
        try:
            info = qclient.get_collection(col)
            stats["collections"][col] = {
                "vectors": getattr(info, "points_count", 0) or 0,
                "indexed_vectors": getattr(info, "indexed_vectors_count", 0) or 0,
            }
        except Exception as e:
            stats["collections"][col] = {"error": str(e)[:100]}
    if _cache["data"]:
        stats["cache"] = {
            "node_count":    _cache["data"]["node_count"],
            "edge_count":    _cache["data"]["edge_count"],
            "age_sec":       round(time.monotonic() - _cache["ts"], 1),
            "params":        _cache["data"]["params"],
        }
    return stats

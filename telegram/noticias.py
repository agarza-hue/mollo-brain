import os
from datetime import datetime
import feedparser
import anthropic
from dotenv import load_dotenv

load_dotenv("/opt/mollo-telegram/.env")

claude_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

FEEDS = {
    "USA": {
        "NYT":        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "CNN":        "http://rss.cnn.com/rss/edition.rss",
        "WSJ":        "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    },
    "Mexico": {
        "El Universal": "https://www.eluniversal.com.mx/arc/outboundfeeds/rss/?outputType=xml",
        "Reforma":      "https://www.reforma.com/rss/portada.xml",
        "El Pais MX":   "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada",
        "Expansion":    "https://expansion.mx/rss",
    },
}

TITULARES_POR_FUENTE = 5


def _fetch_titulares(url):
    try:
        d = feedparser.parse(url)
        items = []
        for entry in d.entries[:TITULARES_POR_FUENTE]:
            titulo = entry.get("title", "").strip()
            if titulo:
                items.append(f"- {titulo}")
        return items
    except Exception:
        return []


def obtener_titulares_raw() -> str:
    """Devuelve todos los titulares en texto plano, agrupados por país y fuente."""
    partes = []
    for pais, fuentes in FEEDS.items():
        bloque = [f"\n=== {pais} ==="]
        for nombre, url in fuentes.items():
            titulares = _fetch_titulares(url)
            if titulares:
                bloque.append(f"\n{nombre}:")
                bloque.extend(titulares)
        partes.append("\n".join(bloque))
    return "\n".join(partes)


def briefing_noticias() -> str:
    """Obtiene titulares y pide a Claude un briefing ejecutivo en español."""
    raw = obtener_titulares_raw()
    if not raw.strip():
        return "No se pudieron obtener noticias en este momento. Verifica conectividad."

    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    try:
        r = claude_client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=1800,
            system=(
                "Eres Mollo, asistente ejecutivo de Adolfo. "
                "Recibes titulares de los principales diarios de USA y México. "
                "Elabora un briefing ejecutivo en español con las noticias más relevantes del momento. "
                "Formato:\n"
                "USA (3-4 noticias clave):\n"
                "• [hecho en 1 línea] — [impacto o contexto breve]\n\n"
                "MEXICO (3-4 noticias clave):\n"
                "• [hecho en 1 línea] — [impacto o contexto breve]\n\n"
                "Al final, 1 línea: el tema más relevante del día en tu opinión. "
                "Sin relleno, sin saludos, directo al grano."
            ),
            messages=[{
                "role": "user",
                "content": f"Titulares del {fecha}:\n{raw}"
            }]
        )
        return r.content[0].text.strip()
    except Exception as e:
        # Si falla la IA, devuelve los titulares crudos para que al menos haya algo útil
        return f"Briefing sin análisis (error IA: {e})\n\n{raw}"

#!/usr/bin/env python3
"""Mollo CLI — interfaz de terminal estilo Claude."""
import sys
import httpx
from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from rich.rule import Rule
from rich.padding import Padding

BRAIN_URL = "http://localhost:8002"
console = Console(highlight=False)

HEADER = """[bold]Mollo[/bold]  [dim]asistente ejecutivo[/dim]

  [dim]/docs[/dim]   listar documentos       [dim]/memory[/dim]  ver memoria
  [dim]/vps[/dim]    estado del servidor      [dim]/bye[/dim]     salir
"""

MOLLO_COLOR = "bold cyan"
USER_COLOR  = "bold white"
DIM         = "dim"


def header():
    console.print()
    console.print(Rule(style="dim"))
    console.print(Padding(HEADER, (0, 2)))
    console.print(Rule(style="dim"))
    console.print()


def ask_stream(pregunta: str, categoria: str | None = None) -> str:
    """Llama al endpoint de streaming y renderiza en tiempo real con rich.Live."""
    collected: list[str] = []

    console.print()
    with Live(console=console, refresh_per_second=15, vertical_overflow="visible") as live:
        with httpx.stream(
            "POST",
            f"{BRAIN_URL}/chat/stream",
            json={"pregunta": pregunta, "categoria": categoria, "usar_memoria": True},
            timeout=120,
        ) as r:
            r.raise_for_status()
            for chunk in r.iter_text():
                collected.append(chunk)
                live.update(Padding(Markdown("".join(collected)), (0, 2)))

    console.print()
    return "".join(collected)


def cmd_docs():
    with console.status("[dim]cargando documentos…[/dim]", spinner="dots"):
        r = httpx.get(f"{BRAIN_URL}/docs/list", timeout=15)
        r.raise_for_status()
        docs = r.json().get("documentos", [])

    if not docs:
        console.print("  [dim]Sin documentos indexados.[/dim]\n")
        return

    console.print()
    for d in docs:
        console.print(f"  [cyan]{d['nombre']}[/cyan]  [dim]{d['categoria']} · {d['tamaño_kb']} KB[/dim]")
    console.print()


def cmd_memory():
    with console.status("[dim]cargando memoria…[/dim]", spinner="dots"):
        r = httpx.get(f"{BRAIN_URL}/memory/", timeout=15)
        r.raise_for_status()
        data = r.json()

    convs = data.get("conversaciones", [])
    learns = data.get("aprendizajes", [])

    console.print()
    console.print(f"  [dim]Conversaciones guardadas:[/dim] {len(convs)}")
    console.print(f"  [dim]Aprendizajes acumulados:[/dim]  {len(learns)}")

    if learns:
        console.print()
        console.print("  [dim]Últimos aprendizajes:[/dim]")
        for l in learns[-5:]:
            console.print(f"    [cyan]·[/cyan] [dim]{l['tema']}[/dim] — {l['insight']}")
    console.print()


def cmd_vps():
    with console.status("[dim]analizando VPS…[/dim]", spinner="dots"):
        r = httpx.post(
            f"{BRAIN_URL}/vps/ask",
            json={"pregunta": "Dame un resumen ejecutivo del estado actual del VPS"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()

    console.print()
    console.print(Padding(Markdown(data["respuesta"]), (0, 2)))
    console.print()


def print_mollo(respuesta: str, fuentes: list):
    console.print()
    console.print(Padding(Markdown(respuesta), (0, 2)))

    if fuentes:
        srcs = "  ".join(
            f"[dim]{f['archivo']} ({f['relevancia']})[/dim]"
            for f in fuentes[:3]
        )
        console.print()
        console.print(f"  [dim]fuentes · {srcs}[/dim]")

    console.print()


def print_error(msg: str):
    console.print(f"\n  [red]✗[/red] [dim]{msg}[/dim]\n")


def run():
    header()

    session_categoria: str | None = None

    while True:
        try:
            raw = console.input("[bold white]>[/bold white] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n  [dim]Hasta luego.[/dim]\n")
            sys.exit(0)

        if not raw:
            continue

        # Comandos internos
        if raw in ("/bye", "/exit", "/quit", "exit", "quit"):
            console.print("\n  [dim]Hasta luego.[/dim]\n")
            sys.exit(0)

        if raw == "/docs":
            cmd_docs()
            continue

        if raw == "/memory":
            cmd_memory()
            continue

        if raw == "/vps":
            cmd_vps()
            continue

        if raw.startswith("/cat "):
            session_categoria = raw.split(" ", 1)[1].strip() or None
            console.print(f"\n  [dim]Categoría activa: {session_categoria}[/dim]\n")
            continue

        if raw == "/cat":
            session_categoria = None
            console.print("\n  [dim]Categoría eliminada — búsqueda global.[/dim]\n")
            continue

        # Pregunta a Mollo
        try:
            ask_stream(raw, categoria=session_categoria)
        except httpx.ConnectError:
            print_error(f"No se puede conectar a Mollo Brain en {BRAIN_URL} — ¿está corriendo?")
        except httpx.HTTPStatusError as e:
            print_error(f"Error {e.response.status_code}: {e.response.text[:120]}")
        except Exception as e:
            print_error(str(e))


if __name__ == "__main__":
    run()

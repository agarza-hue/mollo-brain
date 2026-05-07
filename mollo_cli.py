#!/usr/bin/env python3
"""Mollo CLI — terminal operacional estilo Claude Code."""
import sys
import os
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule

VERSION   = "2.0.0"
BRAIN_URL = "http://localhost:8002"
MOLLO_DIR = Path.home() / ".mollo"
MOLLO_DIR.mkdir(exist_ok=True)
STATE_FILE = MOLLO_DIR / "state.json"
TASKS_FILE = MOLLO_DIR / "tasks.json"
LOG_FILE   = MOLLO_DIR / "op_log.jsonl"

console = Console(highlight=False)

MODEL_STYLE = {
    "simple":   ("GPT-4o-mini",    "dim"),
    "medio":    ("GPT-4o",         "cyan"),
    "complejo": ("Claude Sonnet",  "magenta"),
    "agente":   ("GPT-4o + tools", "yellow"),
}

COST_PER_QUERY = {
    "simple":   0.0003,
    "medio":    0.0040,
    "complejo": 0.0070,
    "agente":   0.0100,
}

_TOOL_TRIGGERS = [
    "busca", "búsca", "buscar", "internet", "web", "cotiza",
    "ejecuta", "reinicia", "estado del vps", "vps", "envía", "enviar",
    "manda", "workflow", "n8n", "logs", "docker", "ahora mismo",
    "convierte", "dólar", "dolares", "dólares", "peso", "pesos",
    "mxn", "usd", "tipo de cambio", "dropbox", "archivo", "descarga",
    "sube", "subir", "pdf", "excel", "word", "analiza el archivo",
]
_COMPLEX_TRIGGERS = [
    "estrategia", "analiza", "compara", "propón", "diseña", "plan",
    "iso 9001", "auditoría", "reestructura", "modelo de negocio",
    "ventaja competitiva", "okr", "roadmap", "due diligence",
    "por qué", "qué haría", "cómo mejorar", "qué opinas",
]

_CHATBOT_NOISE = [
    "¡claro!", "claro que sí", "por supuesto", "¡con gusto!",
    "con mucho gusto", "¡entendido!", "entendido,", "¡perfecto!",
    "perfecto,", "¡excelente!", "excelente,", "¡genial!", "genial,",
    "me alegra que preguntes", "es una excelente pregunta",
    "¿hay algo más en que pueda ayudarte",
    "¿en qué más puedo ayudarte",
    "espero que esto te haya sido útil",
    "con base en lo que me compartes",
    "basándome en la información",
    "basado en el contexto",
]


# ── State ──────────────────────────────────────────────────────────────────

@dataclass
class MolloState:
    workspace: str = "default"
    active_task: int | None = None
    modo: str | None = None
    ctx: bool = True
    categoria: str | None = None
    session_id: str = ""
    stats: dict = field(default_factory=lambda: {k: 0 for k in MODEL_STYLE})
    history: list = field(default_factory=list)
    last_query: str = ""
    last_modo: str | None = None

    def save(self):
        STATE_FILE.write_text(json.dumps({
            "workspace":   self.workspace,
            "active_task": self.active_task,
            "modo":        self.modo,
            "ctx":         self.ctx,
            "categoria":   self.categoria,
        }))

    @classmethod
    def load(cls) -> "MolloState":
        s = cls()
        s.session_id = os.urandom(4).hex()
        if STATE_FILE.exists():
            try:
                d = json.loads(STATE_FILE.read_text())
                s.workspace   = d.get("workspace", "default")
                s.active_task = d.get("active_task")
                s.modo        = d.get("modo")
                s.ctx         = d.get("ctx", True)
                s.categoria   = d.get("categoria")
            except Exception:
                pass
        return s


# ── Tasks (local) ──────────────────────────────────────────────────────────

def _load_tasks(ws: str) -> list[dict]:
    if TASKS_FILE.exists():
        try:
            return json.loads(TASKS_FILE.read_text()).get(ws, [])
        except Exception:
            pass
    return []


def _save_tasks(ws: str, tasks: list[dict]):
    data: dict = {}
    if TASKS_FILE.exists():
        try:
            data = json.loads(TASKS_FILE.read_text())
        except Exception:
            pass
    data[ws] = tasks
    TASKS_FILE.write_text(json.dumps(data, indent=2))


def _next_id(tasks: list[dict]) -> int:
    return max((t["id"] for t in tasks), default=0) + 1


# ── Op log ─────────────────────────────────────────────────────────────────

def _log_op(model: str, tokens_est: int, duration_ms: int, query: str):
    entry = {
        "ts":          datetime.now().isoformat(timespec="seconds"),
        "model":       model,
        "tokens_est":  tokens_est,
        "duration_ms": duration_ms,
        "query":       query[:60],
    }
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Noise filter ───────────────────────────────────────────────────────────

def _strip_noise(text: str) -> str:
    lines = text.split("\n")
    out = []
    for line in lines:
        low = line.strip().lower()
        if any(low.startswith(n) for n in _CHATBOT_NOISE):
            continue
        out.append(line)
    return "\n".join(out).strip()


# ── Prompt ─────────────────────────────────────────────────────────────────

def _prompt(s: MolloState) -> str:
    task_part = f" task:{s.active_task}" if s.active_task else ""
    return f"[dim]\\[mollo][/dim] [bold]ws:{s.workspace}[/bold]{task_part} [dim]>[/dim] "


# ── Workspace digest ───────────────────────────────────────────────────────

def workspace_digest(s: MolloState):
    tasks   = _load_tasks(s.workspace)
    pending = sum(1 for t in tasks if t["status"] == "pending")
    blocked = sum(1 for t in tasks if t["status"] == "blocked")
    done    = sum(1 for t in tasks if t["status"] == "done")

    model_label = MODEL_STYLE[s.modo][0] if s.modo else "auto-routing"
    ctx_label   = "RAG + memoria activa" if s.ctx else "sin contexto"

    console.print()
    console.print(Rule(f"[dim]workspace: {s.workspace}[/dim]", style="dim"))
    t = Table(box=None, padding=(0, 2), show_header=False)
    t.add_column("k", style="dim", min_width=10)
    t.add_column("v")
    t.add_row("tasks",   f"[green]{pending} pending[/green]  [red]{blocked} blocked[/red]  [dim]{done} done[/dim]")
    t.add_row("context", ctx_label)
    t.add_row("model",   f"[cyan]{model_label}[/cyan]")
    if s.active_task:
        match = next((tt for tt in tasks if tt["id"] == s.active_task), None)
        if match:
            t.add_row("active task", f"[bold]#{s.active_task}[/bold] {match['title'][:40]}")
    console.print(t)
    console.print(Rule(style="dim"))
    console.print()


# ── Streaming ask ──────────────────────────────────────────────────────────

def ask_stream(query: str, s: MolloState):
    t0 = time.monotonic()
    payload = {
        "pregunta":     query,
        "categoria":    s.categoria,
        "top_k":        5,
        "session_id":   s.session_id,
        "usar_memoria": s.ctx,
        "modo":         s.modo,
    }

    detected_modo = s.modo or "medio"
    tokens: list[str] = []

    with httpx.stream("POST", f"{BRAIN_URL}/chat/stream", json=payload, timeout=120) as r:
        r.raise_for_status()
        for chunk in r.iter_text():
            if not chunk:
                continue
            if chunk.startswith("\x02"):
                meta  = chunk[1:].strip()
                parts = meta.split(":", 1)
                detected_modo = parts[0] if parts else detected_modo
                continue
            tokens.append(chunk)
            print(chunk, end="", flush=True)

    print()
    full = _strip_noise("".join(tokens))

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    label, style = MODEL_STYLE.get(detected_modo, (detected_modo, "dim"))
    console.print(f"\n[{style}]{label}[/{style}] [dim]{elapsed_ms}ms[/dim]")
    console.print()

    s.stats[detected_modo] = s.stats.get(detected_modo, 0) + 1
    s.last_query = query
    s.last_modo  = detected_modo
    s.history.append((query, full, detected_modo))

    est_tokens = len(query.split()) * 2 + len(full.split())
    _log_op(label, est_tokens, elapsed_ms, query)


# ── API helpers ────────────────────────────────────────────────────────────

def _brain_get(path: str) -> dict:
    r = httpx.get(f"{BRAIN_URL}{path}", timeout=15)
    r.raise_for_status()
    return r.json()


def _brain_post(path: str, body: dict) -> dict:
    r = httpx.post(f"{BRAIN_URL}{path}", json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def _error(msg: str):
    console.print(f"[red][error][/red] {msg}")


# ── Commands ───────────────────────────────────────────────────────────────

def cmd_task(args: str, s: MolloState):
    parts = args.strip().split(None, 1)
    sub   = parts[0].lower() if parts else "ls"
    rest  = parts[1] if len(parts) > 1 else ""
    tasks = _load_tasks(s.workspace)

    STATUS_COLOR = {"pending": "green", "blocked": "red", "done": "dim", "ready": "cyan"}

    if sub == "ls":
        if not tasks:
            console.print("[dim]  sin tasks.[/dim]\n"); return
        t = Table(box=None, padding=(0, 1), show_header=True, header_style="dim")
        t.add_column("ID",     justify="right", min_width=3)
        t.add_column("STATUS", min_width=9)
        t.add_column("TITLE")
        for task in tasks:
            color = STATUS_COLOR.get(task["status"], "white")
            t.add_row(str(task["id"]), f"[{color}]{task['status']}[/{color}]", task["title"])
        console.print()
        console.print(t)
        console.print()

    elif sub == "add":
        if not rest:
            _error("uso: task add <título>"); return
        new = {"id": _next_id(tasks), "title": rest, "status": "pending",
               "created": datetime.now().isoformat(timespec="seconds")}
        tasks.append(new)
        _save_tasks(s.workspace, tasks)
        console.print(f"-> task #{new['id']} added: {rest}\n")

    elif sub == "run":
        try:
            tid = int(rest)
        except ValueError:
            _error("uso: task run <id>"); return
        match = next((t for t in tasks if t["id"] == tid), None)
        if not match:
            _error(f"task #{tid} not found"); return
        s.active_task = tid
        s.save()
        console.print(f"-> task #{tid} → ACTIVE\n")
        ask_stream(f"Trabaja en esta tarea: {match['title']}", s)

    elif sub == "done":
        try:
            tid = int(rest)
        except ValueError:
            _error("uso: task done <id>"); return
        for task in tasks:
            if task["id"] == tid:
                task["status"] = "done"
                _save_tasks(s.workspace, tasks)
                if s.active_task == tid:
                    s.active_task = None
                    s.save()
                console.print(f"-> task #{tid}: pending -> done\n"); return
        _error(f"task #{tid} not found")

    elif sub == "block":
        parts2 = rest.split(None, 1)
        try:
            tid = int(parts2[0])
        except (ValueError, IndexError):
            _error("uso: task block <id> [razón]"); return
        reason = parts2[1] if len(parts2) > 1 else ""
        for task in tasks:
            if task["id"] == tid:
                task["status"] = "blocked"
                if reason:
                    task["blocked_reason"] = reason
                _save_tasks(s.workspace, tasks)
                console.print(f"-> task #{tid}: pending -> blocked\n"); return
        _error(f"task #{tid} not found")

    elif sub == "show":
        try:
            tid = int(rest)
        except ValueError:
            _error("uso: task show <id>"); return
        match = next((t for t in tasks if t["id"] == tid), None)
        if not match:
            _error(f"task #{tid} not found"); return
        for k, v in match.items():
            console.print(f"  [dim]{k:<14}[/dim] {v}")
        console.print()

    else:
        console.print("[dim]  task ls | add <title> | run <id> | done <id> | block <id> | show <id>[/dim]\n")


def cmd_ws(args: str, s: MolloState):
    parts = args.strip().split(None, 1)
    sub   = parts[0].lower() if parts else "status"
    rest  = parts[1] if len(parts) > 1 else ""

    if sub == "ls":
        all_ws = [s.workspace]
        if TASKS_FILE.exists():
            try:
                all_ws = list(json.loads(TASKS_FILE.read_text()).keys()) or [s.workspace]
            except Exception:
                pass
        for ws in all_ws:
            marker = "[bold]→[/bold]" if ws == s.workspace else " "
            console.print(f"  {marker} {ws}")
        console.print()

    elif sub == "use":
        if not rest:
            _error("uso: ws use <nombre>"); return
        old = s.workspace
        s.workspace   = rest
        s.active_task = None
        s.save()
        console.print(f"-> workspace: {old} -> {rest}\n")
        workspace_digest(s)

    else:
        workspace_digest(s)


def cmd_memory(args: str):
    parts = args.strip().split(None, 1)
    sub   = parts[0].lower() if parts else "ls"
    rest  = parts[1] if len(parts) > 1 else ""

    if sub == "ls":
        try:
            data = _brain_get("/memory/topics")
            if not data:
                console.print("[dim]  sin memoria por temas.[/dim]\n"); return
            for topic, summary in data.items():
                console.print(f"  [cyan]{topic}[/cyan]  [dim]{str(summary)[:80]}[/dim]")
            console.print()
        except Exception as e:
            _error(str(e))

    elif sub == "search":
        if not rest:
            _error("uso: memory search <query>"); return
        try:
            data = _brain_post("/memory/search", {"query": rest, "top_k": 5})
            results = data.get("results", [])
            if not results:
                console.print("[dim]  sin resultados.[/dim]\n"); return
            for r in results:
                date = r.get("date", "")[:10]
                text = r.get("text", "")[:100]
                console.print(f"  [dim]{date}[/dim]  {text}")
            console.print()
        except Exception as e:
            _error(str(e))

    else:
        console.print("[dim]  memory ls | search <query>[/dim]\n")


def cmd_docs():
    try:
        data = _brain_get("/docs/list")
        docs = data if isinstance(data, list) else data.get("documents", [])
        if not docs:
            console.print("[dim]  sin documentos indexados.[/dim]\n"); return
        t = Table(box=None, padding=(0, 1), show_header=True, header_style="dim")
        t.add_column("ARCHIVO",   min_width=30)
        t.add_column("CATEGORÍA")
        for doc in docs[:20]:
            t.add_row(
                str(doc.get("source", doc.get("filename", "?")))[:40],
                str(doc.get("categoria", "-")),
            )
        console.print()
        console.print(t)
        if len(docs) > 20:
            console.print(f"  [dim]... y {len(docs)-20} más[/dim]")
        console.print()
    except Exception as e:
        _error(str(e))


def cmd_vps():
    try:
        data = _brain_get("/vps/resumen")
        console.print()
        console.print(Panel(str(data.get("resumen", data))[:1000],
                            title="VPS", border_style="dim", padding=(0, 1)))
        console.print()
    except Exception as e:
        _error(str(e))


def cmd_modo(arg: str, s: MolloState):
    if not arg:
        t = Table(box=None, padding=(0, 2), show_header=False)
        t.add_column("nivel")
        t.add_column("modelo")
        for key, (label, style) in MODEL_STYLE.items():
            marker = "[bold]→[/bold]" if s.modo == key else " "
            t.add_row(f"{marker} [bold]{key}[/bold]", f"[{style}]{label}[/{style}]")
        auto_marker = "[bold]→[/bold]" if s.modo is None else " "
        t.add_row(f"{auto_marker} [bold]auto[/bold]", "[dim]routing inteligente[/dim]")
        console.print()
        console.print(t)
        console.print()
        return
    arg = arg.lower()
    valid = {"auto": None, **{k: k for k in MODEL_STYLE}}
    if arg not in valid:
        _error(f"nivel inválido. Usa: {', '.join(valid)}"); return
    s.modo = valid[arg]
    s.save()
    if s.modo:
        label, style = MODEL_STYLE[s.modo]
        console.print(f"-> model: [{style}]{label}[/{style}]\n")
    else:
        console.print("-> model: [dim]auto-routing[/dim]\n")


def cmd_ctx(s: MolloState):
    s.ctx = not s.ctx
    s.save()
    console.print(f"-> context: {'[green]ON[/green]' if s.ctx else '[red]OFF[/red]'}\n")


def cmd_stats(s: MolloState):
    # Datos reales desde Brain
    try:
        data     = _brain_get("/costs/summary")
        lifetime = data.get("lifetime", {})
        by_model = data.get("by_model", [])
        daily    = data.get("last_7_days", [])

        console.print()
        console.print(Rule("[dim]costos reales — lifetime[/dim]", style="dim"))

        # Por modelo
        if by_model:
            t = Table(box=None, padding=(0, 2), show_header=True, header_style="dim")
            t.add_column("MODELO",   min_width=16)
            t.add_column("MODO",     min_width=9)
            t.add_column("QUERIES",  justify="right")
            t.add_column("TOKENS",   justify="right")
            t.add_column("REAL",     justify="right")
            t.add_column("BASE",     justify="right", style="dim")
            t.add_column("AHORRO",   justify="right", style="green")

            MODEL_COLOR = {
                "gpt-4o-mini": "dim",
                "gpt-4o":      "cyan",
                "claude-sonnet-4-6": "magenta",
                "claude-haiku-4-5":  "blue",
            }
            for row in by_model:
                color = MODEL_COLOR.get(row["model"], "white")
                tokens = (row.get("input_tokens", 0) or 0) + (row.get("output_tokens", 0) or 0)
                t.add_row(
                    f"[{color}]{row['model']}[/{color}]",
                    row.get("modo", "-"),
                    str(row.get("queries", 0)),
                    f"{tokens:,}",
                    f"${(row.get('actual_cost') or 0):.4f}",
                    f"${(row.get('baseline_cost') or 0):.4f}",
                    f"${(row.get('savings') or 0):.4f}",
                )
            console.print(t)

        # Totales lifetime
        q      = lifetime.get("queries", 0) or 0
        actual = lifetime.get("actual_cost", 0) or 0
        base   = lifetime.get("baseline_cost", 0) or 0
        saved  = lifetime.get("savings", 0) or 0
        pct    = lifetime.get("savings_pct", 0) or 0
        tokens = (lifetime.get("input_tokens", 0) or 0) + (lifetime.get("output_tokens", 0) or 0)
        console.print()
        console.print(f"  [bold]{q}[/bold] queries · [bold]{tokens:,}[/bold] tokens")
        console.print(f"  real: [bold]${actual:.4f}[/bold]  baseline(all-claude): [dim]${base:.4f}[/dim]")
        console.print(f"  ahorro: [green bold]+${saved:.4f}[/green bold]  ([green]{pct:.0f}%[/green])")

        # Últimos 7 días
        if daily:
            console.print()
            console.print(Rule("[dim]últimos 7 días[/dim]", style="dim"))
            td = Table(box=None, padding=(0, 2), show_header=True, header_style="dim")
            td.add_column("DÍA",     min_width=12)
            td.add_column("QUERIES", justify="right")
            td.add_column("TOKENS",  justify="right")
            td.add_column("REAL",    justify="right")
            td.add_column("AHORRO",  justify="right", style="green")
            for row in daily:
                t_day = (row.get("total_tokens") or 0)
                td.add_row(
                    row.get("day", ""),
                    str(row.get("queries", 0)),
                    f"{t_day:,}",
                    f"${(row.get('actual_cost') or 0):.4f}",
                    f"${(row.get('savings') or 0):.4f}",
                )
            console.print(td)

        console.print()

    except Exception as e:
        # Fallback a estimaciones de sesión si Brain no responde
        _error(f"no se pudo obtener datos del brain: {e}")
        total_q = sum(s.stats.values())
        if not total_q:
            console.print("[dim]  sin queries en esta sesión.[/dim]\n"); return
        total_cost    = sum(s.stats.get(k, 0) * COST_PER_QUERY.get(k, 0) for k in s.stats)
        baseline_cost = total_q * COST_PER_QUERY["complejo"]
        ahorro        = baseline_cost - total_cost
        pct           = (ahorro / baseline_cost * 100) if baseline_cost > 0 else 0
        console.print(f"  [dim](estimado)[/dim] {total_q} queries · ${total_cost:.4f} real · ahorro ${ahorro:.4f} ({pct:.0f}%)\n")


def cmd_why(s: MolloState):
    if not s.last_query:
        console.print("[dim]  sin queries en esta sesión.[/dim]\n"); return
    q_low  = s.last_query.lower()
    modo   = s.last_modo or "?"
    label, style = MODEL_STYLE.get(modo, (modo, "dim"))
    console.print(f"\n  query: [dim]{s.last_query[:80]}[/dim]")
    console.print(f"  model: [{style}]{label}[/{style}]")
    if s.modo:
        console.print(f"  reason: modo fijado manualmente a [bold]{s.modo}[/bold]")
    elif modo == "agente":
        matched = [x for x in _TOOL_TRIGGERS if x in q_low]
        console.print(f"  reason: tool trigger → [yellow]{matched[0] if matched else '?'}[/yellow]")
    elif modo == "complejo":
        matched = [x for x in _COMPLEX_TRIGGERS if x in q_low]
        console.print(f"  reason: complexity trigger → [magenta]{matched[0] if matched else '?'}[/magenta]")
    elif modo == "simple":
        console.print(f"  reason: {len(s.last_query.split())} words ≤ 12 → GPT-4o-mini")
    else:
        console.print(f"  reason: {len(s.last_query.split())} words, no triggers → GPT-4o")
    console.print()


def cmd_log():
    if not LOG_FILE.exists():
        console.print("[dim]  sin operaciones registradas.[/dim]\n"); return
    lines = LOG_FILE.read_text().strip().split("\n")
    recent = lines[-20:]
    t = Table(box=None, padding=(0, 1), show_header=True, header_style="dim")
    t.add_column("TIME",  min_width=8)
    t.add_column("MODEL", min_width=14)
    t.add_column("MS",    justify="right", min_width=6)
    t.add_column("QUERY")
    for line in recent:
        try:
            e = json.loads(line)
            t.add_row(e["ts"][-8:], e["model"][:14], str(e["duration_ms"]), e["query"][:50])
        except Exception:
            pass
    console.print()
    console.print(t)
    console.print()


def cmd_export(s: MolloState):
    if not s.history:
        console.print("[dim]  sin historial para exportar.[/dim]\n"); return
    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    filepath = Path.home() / f"mollo_sesion_{ts}.md"
    lines    = [f"# Sesión Mollo — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    for i, (pregunta, respuesta, modo) in enumerate(s.history, 1):
        label, _ = MODEL_STYLE.get(modo, (modo, "dim"))
        lines.append(f"## {i}. {pregunta}\n*{label}*\n{respuesta}\n")
    filepath.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"-> exported: {filepath}  ({len(s.history)} turns)\n")


def cmd_help():
    t = Table(box=None, padding=(0, 2), show_header=False)
    t.add_column("cmd",  style="bold cyan", min_width=26)
    t.add_column("desc", style="dim")
    for row in [
        ("task ls",                  "tablero de tareas"),
        ("task add <título>",        "crear tarea"),
        ("task run <id>",            "ejecutar tarea (Mollo la trabaja)"),
        ("task done <id>",           "marcar completada"),
        ("task block <id> [razón]",  "marcar bloqueada"),
        ("ws ls",                    "listar workspaces"),
        ("ws use <nombre>",          "cambiar workspace"),
        ("ws status",                "digest del workspace"),
        ("memory ls",                "ver memoria por temas"),
        ("memory search <query>",    "buscar en memoria semántica"),
        ("/docs",                    "documentos indexados"),
        ("/vps",                     "estado del VPS"),
        ("/modo [nivel]",            "ver o cambiar modelo"),
        ("/ctx",                     "toggle RAG + memoria"),
        ("/why",                     "explicar último routing"),
        ("/stats  | cost",           "costos de sesión"),
        ("/log",                     "log de operaciones"),
        ("/export",                  "exportar sesión a .md"),
        ("/bye",                     "salir"),
    ]:
        t.add_row(*row)
    console.print()
    console.print(t)
    console.print()


# ── Main REPL ──────────────────────────────────────────────────────────────

def run():
    s = MolloState.load()
    workspace_digest(s)

    while True:
        try:
            raw = console.input(_prompt(s)).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]done[/dim]\n")
            sys.exit(0)

        if not raw:
            continue

        parts = raw.split()
        verb  = parts[0].lower()
        rest  = " ".join(parts[1:])

        # Verb-noun commands (sin slash)
        if verb == "task":
            cmd_task(rest, s); continue

        if verb in ("ws", "workspace"):
            cmd_ws(rest, s); continue

        if verb in ("memory", "mem"):
            cmd_memory(rest); continue

        if verb == "cost":
            cmd_stats(s); continue

        if verb == "log":
            cmd_log(); continue

        if verb == "run" and rest:
            try:
                ask_stream(rest, s)
            except httpx.ConnectError:
                _error("gateway:502 — FastAPI unreachable at :8002\n  -> check: systemctl status mollo-brain")
            continue

        # Slash commands
        cmd = verb
        arg = rest

        if cmd in ("/bye", "/exit", "/quit", "exit", "quit"):
            console.print("[dim]done[/dim]\n"); sys.exit(0)

        if cmd == "/docs":       cmd_docs(); continue
        if cmd == "/memory":     cmd_memory(arg); continue
        if cmd == "/vps":        cmd_vps(); continue
        if cmd == "/modo":       cmd_modo(arg, s); continue
        if cmd == "/ctx":        cmd_ctx(s); continue
        if cmd in ("/stats", "/cost"): cmd_stats(s); continue
        if cmd == "/why":        cmd_why(s); continue
        if cmd == "/log":        cmd_log(); continue
        if cmd == "/export":     cmd_export(s); continue
        if cmd in ("/help", "/?", "/h"): cmd_help(); continue
        if cmd == "/ws":         cmd_ws(arg, s); continue
        if cmd == "/task":       cmd_task(arg, s); continue
        if cmd == "/cat":
            if arg:
                s.categoria = arg
                console.print(f"-> categoria: {s.categoria}\n")
            else:
                s.categoria = None
                console.print("-> categoria: [dim]global[/dim]\n")
            continue

        # Free-form query → Brain
        try:
            ask_stream(raw, s)
        except httpx.ConnectError:
            _error("gateway:502 — FastAPI unreachable at :8002\n  -> check: systemctl status mollo-brain")
        except httpx.HTTPStatusError as e:
            _error(f"http:{e.response.status_code} — {e.response.text[:120]}")
        except Exception as e:
            _error(str(e))


if __name__ == "__main__":
    run()

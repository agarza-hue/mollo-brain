"""
Backend: Codex CLI ejecutado como subprocess (modo `codex exec`).

Routea al tier `codex` del chat router. Es un agente con tools (filesystem,
exec, edits) que opera sobre `workdir`. Útil para tareas que necesitan VER el
proyecto pero NO requieren criterio arquitectónico fuerte:
  - refactors mecánicos en archivos reales
  - scaffolding multi-archivo
  - migraciones de sintaxis
  - batch edits con patrón claro

A diferencia de los otros tiers de Mollo, codex_brain NO consume contexto
inyectado (doc_context/memory/etc.) porque Codex lee los archivos él mismo.
Tampoco devuelve token counts confiables — el costo se imputa a la cuenta
OpenAI vinculada a Codex, no a Mollo.
"""
from __future__ import annotations

import asyncio
import os
import time

DEFAULT_MODEL = os.getenv("CODEX_MODEL")  # None = usa el default de codex CLI
DEFAULT_SANDBOX = os.getenv("CODEX_SANDBOX", "workspace-write")
DEFAULT_TIMEOUT_SECS = int(os.getenv("CODEX_TIMEOUT_SECS", "600"))


async def run_codex(
    pregunta: str,
    workdir: str | None = None,
    model: str | None = None,
    sandbox: str | None = None,
    timeout: int | None = None,
) -> tuple[str, dict]:
    """Ejecuta `codex exec` no-interactivo. Devuelve (output, usage).

    `usage` trae model/duration_ms/exit_code pero input_tokens/output_tokens
    en 0 — Codex CLI no expone counts en stdout de forma estable. El costo
    real se factura en la cuenta OpenAI del usuario, fuera del cost_service
    de Mollo.
    """
    if not pregunta or not pregunta.strip():
        raise ValueError("codex_brain: prompt vacío")

    workdir = workdir or os.getcwd()
    model = model or DEFAULT_MODEL  # puede quedar None → usamos el default de codex
    sandbox = sandbox or DEFAULT_SANDBOX
    timeout = timeout or DEFAULT_TIMEOUT_SECS

    if not os.path.isdir(workdir):
        raise ValueError(f"codex_brain: workdir no existe: {workdir}")

    cmd = [
        "codex", "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "-s", sandbox,
        "-C", workdir,
    ]
    if model:
        cmd.extend(["-m", model])
    cmd.append(pregunta)

    t0 = time.monotonic()
    # Codex debe usar el login de ChatGPT (cuota Plus), NO la OPENAI_API_KEY
    # que mollo_brain hereda de su EnvironmentFile. Si la heredara, el CLI
    # revertiría a auth_mode=apikey y cobraría tokens de API. La quitamos
    # solo para este subprocess; mollo_brain la conserva para sus tiers GPT-4o.
    codex_env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=codex_env,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        raise RuntimeError(
            f"codex_brain: timeout tras {timeout}s "
            f"(workdir={workdir}, model={model})"
        )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    stdout = stdout_b.decode("utf-8", errors="replace").strip()
    stderr = stderr_b.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        detail = (stderr or stdout)[:800] or "(sin stderr)"
        raise RuntimeError(
            f"codex_brain: exit {proc.returncode} — {detail}"
        )

    usage = {
        "model": f"codex/{model or 'default'}",
        "duration_ms": elapsed_ms,
        "exit_code": proc.returncode,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
    }
    return stdout, usage


async def stream_codex(
    pregunta: str,
    workdir: str | None = None,
    model: str | None = None,
    sandbox: str | None = None,
    timeout: int | None = None,
):
    """Stream-compatible wrapper. Codex exec no streamea token-by-token en
    formato útil, así que ejecutamos y emitimos el output completo como un
    único chunk. Mantiene la firma esperada por _stream() del chat router.
    """
    text, _ = await run_codex(
        pregunta, workdir=workdir, model=model,
        sandbox=sandbox, timeout=timeout,
    )
    yield text

"""
Mollo Task Engine — ejecución determinista de workflows con rollback y dry-run.
SQLite-backed. Soporta: http_request, llm_call, condition, shell (whitelist).
"""
import json
import sqlite3
import hashlib
import asyncio
import httpx
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, field_validator

from openai_service import classify_complexity

DB_PATH = Path.home() / ".mollo" / "tasks.db"
DB_PATH.parent.mkdir(exist_ok=True)

BRAIN_URL = "http://127.0.0.1:8002"

SHELL_WHITELIST = {
    "df -h /", "free -h", "uptime", "docker ps",
    "systemctl status mollo-brain --no-pager",
    "systemctl status mollo-telegram --no-pager",
}

FORBIDDEN_PATTERNS = ["rm ", "chmod ", "curl | bash", "pip install", "git push", "DROP ", "TRUNCATE "]


# ── Schema ─────────────────────────────────────────────────────────────────

class StepValidator(BaseModel):
    check: str
    on_fail: str = "abort"
    max_retries: int = 1


class StepRollback(BaseModel):
    type: str = "log_only"
    message: str = ""
    snapshot_key: str = ""


class TaskStep(BaseModel):
    step_id: str
    type: str
    depends_on: list[str] = []
    config: dict[str, Any] = {}
    validators: dict[str, list[StepValidator]] = {}
    output_key: str = ""
    rollback: Optional[StepRollback] = None


class TaskWorkspace(BaseModel):
    id: str = "default_ws"
    persist: bool = False
    ttl_hours: int = 24


class TaskDefinition(BaseModel):
    task_id: str
    version: str = "1.0.0"
    description: str = ""
    dry_run: bool = False
    workspace: TaskWorkspace = TaskWorkspace()
    context: dict[str, Any] = {}
    steps: list[TaskStep]
    on_failure: str = "rollback_all"
    timeout_seconds: int = 120

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, steps):
        for step in steps:
            for pattern in FORBIDDEN_PATTERNS:
                cfg_str = json.dumps(step.config)
                if pattern.lower() in cfg_str.lower():
                    raise ValueError(f"Forbidden pattern '{pattern}' in step {step.step_id}")
        return steps


class TaskRunRequest(BaseModel):
    task_id: str
    context: dict[str, Any] = {}
    dry_run: bool = False


class TaskResult(BaseModel):
    run_id: str
    task_id: str
    status: str  # success | failed | rolled_back | dry_run
    steps_completed: int
    outputs: dict[str, Any]
    log: list[str]
    error: Optional[str] = None


# ── SQLite ─────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS task_definitions (
            task_id TEXT PRIMARY KEY,
            definition JSON NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            dry_run INTEGER DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            result JSON
        );
        CREATE TABLE IF NOT EXISTS workspaces (
            ws_id TEXT PRIMARY KEY,
            task_id TEXT,
            run_id TEXT,
            state JSON NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            expires_at TEXT
        );
        """)


init_db()


@contextmanager
def get_db():
    conn = _get_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── WorkspaceManager ───────────────────────────────────────────────────────

class WorkspaceManager:
    def __init__(self, ws_id: str, run_id: str):
        self.ws_id = ws_id
        self.run_id = run_id
        self._state: dict[str, Any] = {}
        self._snapshots: list[dict] = []

    def load(self):
        with get_db() as db:
            row = db.execute(
                "SELECT state FROM workspaces WHERE ws_id=? AND run_id=?",
                (self.ws_id, self.run_id)
            ).fetchone()
            if row:
                self._state = json.loads(row["state"])

    def save(self):
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO workspaces(ws_id, run_id, state, created_at) VALUES(?,?,?,?)",
                (self.ws_id, self.run_id, json.dumps(self._state), now)
            )

    def snapshot(self):
        self._snapshots.append(json.dumps(self._state))

    def restore_snapshot(self):
        if self._snapshots:
            self._state = json.loads(self._snapshots[-1])
            self.save()

    def set(self, key: str, value: Any):
        self._state[key] = value

    def get(self, key: str, default=None):
        return self._state.get(key, default)


# ── Template resolution ────────────────────────────────────────────────────

def _resolve(value: Any, ctx: dict, ws: WorkspaceManager) -> Any:
    if not isinstance(value, str):
        return value
    import re

    def replacer(m):
        expr = m.group(1).strip()
        if expr.startswith("context.params."):
            key = expr[len("context.params."):]
            return str(ctx.get("params", {}).get(key, m.group(0)))
        if expr.startswith("context."):
            key = expr[len("context."):]
            return str(ctx.get(key, m.group(0)))
        if expr.startswith("steps."):
            parts = expr.split(".")
            if len(parts) >= 3:
                step_key = ".".join(parts[1:])
                val = ws.get(step_key)
                return str(val) if val is not None else m.group(0)
        env_val = __import__("os").environ.get(expr)
        if env_val:
            return env_val
        return m.group(0)

    return re.sub(r"\{\{(.+?)\}\}", replacer, value)


def _resolve_dict(d: dict, ctx: dict, ws: WorkspaceManager) -> dict:
    return {k: _resolve(v, ctx, ws) for k, v in d.items()}


# ── StepExecutor ───────────────────────────────────────────────────────────

class StepExecutor:
    def __init__(self, dry_run: bool, ws: WorkspaceManager, log: list[str]):
        self.dry_run = dry_run
        self.ws = ws
        self.log = log

    def _append(self, msg: str):
        self.log.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")

    async def execute(self, step: TaskStep, ctx: dict) -> Any:
        cfg = _resolve_dict(step.config, ctx, self.ws)
        t = step.type

        if t == "http_request":
            return await self._http(cfg)
        if t == "llm_call":
            return await self._llm(cfg)
        if t == "condition":
            return self._condition(cfg, ctx)
        if t == "shell":
            return self._shell(cfg)
        if t == "email":
            return self._email_dry(cfg) if self.dry_run else self._email_stub(cfg)
        if t == "db_query":
            return {"rows": [], "note": "db_query not wired — return empty"}
        if t == "db_write":
            if self.dry_run:
                self._append(f"  [DRY] would db_write to {cfg.get('table')}")
                return {"dry": True}
            return {"status": "ok", "note": "db_write stub"}

        raise ValueError(f"Unknown step type: {t}")

    async def _http(self, cfg: dict) -> Any:
        method = cfg.get("method", "GET").upper()
        url = cfg.get("url", "")
        if self.dry_run and method != "GET":
            self._append(f"  [DRY] would {method} {url}")
            return {"dry": True, "url": url, "method": method}

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(
                method, url,
                params=cfg.get("params"),
                json=cfg.get("body"),
                headers={"X-Client": "mollo-task-engine"},
            )
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"text": r.text[:2000]}

    async def _llm(self, cfg: dict) -> Any:
        model_key = cfg.get("model", "simple")
        prompt = cfg.get("prompt_template", "") or cfg.get("prompt", "")
        input_text = cfg.get("input", "")
        max_tokens = int(cfg.get("max_tokens", 300))
        full_prompt = f"{prompt}\n\n{input_text}" if input_text else prompt

        if self.dry_run:
            self._append(f"  [DRY] would call llm model={model_key} max_tokens={max_tokens}")
            return {"dry": True, "output": f"[DRY] llm response for: {full_prompt[:80]}"}

        from openai_brain import chat_openai, GPT_MINI, GPT_4O
        from claude_service import chat_with_rag

        complexity = classify_complexity(full_prompt)
        if model_key == "gpt-4o-mini" or complexity == "simple":
            result = chat_openai(pregunta=full_prompt, doc_context="", memory_context="",
                                 business_context="", learnings_context="", topic_memory="",
                                 model=GPT_MINI)
        elif model_key in ("gpt-4o", "gpt4o") or complexity in ("medio", "agente"):
            result = chat_openai(pregunta=full_prompt, doc_context="", memory_context="",
                                 business_context="", learnings_context="", topic_memory="",
                                 model=GPT_4O)
        else:
            result = chat_with_rag(pregunta=full_prompt, doc_context="", memory_context="",
                                   business_context="", learnings_context="", topic_memory="")
        return {"output": result}

    def _condition(self, cfg: dict, ctx: dict) -> Any:
        expr = cfg.get("expression", "True")
        try:
            result = bool(eval(expr, {"__builtins__": {}}, {"context": ctx, "dry_run": self.dry_run}))
        except Exception:
            result = False
        self._append(f"  condition '{expr}' → {result}")
        return {"result": result}

    def _shell(self, cfg: dict) -> Any:
        cmd = cfg.get("command", "")
        if cmd not in SHELL_WHITELIST:
            raise PermissionError(f"shell command not in whitelist: {cmd!r}")
        if self.dry_run:
            self._append(f"  [DRY] would run: {cmd}")
            return {"dry": True, "command": cmd}
        import subprocess
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return {"stdout": r.stdout[:2000], "returncode": r.returncode}

    def _email_stub(self, cfg: dict) -> Any:
        self._append(f"  email → {cfg.get('to')} subject={cfg.get('subject', '')[:40]}")
        return {"message_id": f"stub_{hashlib.md5(str(cfg).encode()).hexdigest()[:8]}"}

    def _email_dry(self, cfg: dict) -> Any:
        self._append(f"  [DRY] would send email to {cfg.get('to')} | {cfg.get('subject', '')[:40]}")
        return {"dry": True, "message_id": "dry_stub"}


# ── Validators ─────────────────────────────────────────────────────────────

def _run_validators(validators: list[StepValidator], output: Any, dry_run: bool) -> tuple[bool, str]:
    for v in validators:
        expr = v.check
        try:
            ok = bool(eval(expr, {"__builtins__": {}}, {
                "output": output, "response": output,
                "len": len, "dry_run": dry_run,
            }))
        except Exception as e:
            ok = False
            expr = f"{expr} → eval error: {e}"
        if not ok:
            return False, f"validator failed: {expr} on_fail={v.on_fail}"
    return True, ""


# ── TaskEngine ─────────────────────────────────────────────────────────────

class TaskEngine:
    @staticmethod
    def _topo_sort(steps: list[TaskStep]) -> list[TaskStep]:
        by_id = {s.step_id: s for s in steps}
        visited, order = set(), []

        def visit(sid):
            if sid in visited:
                return
            visited.add(sid)
            for dep in by_id[sid].depends_on:
                visit(dep)
            order.append(by_id[sid])

        for s in steps:
            visit(s.step_id)
        return order

    @classmethod
    async def execute(cls, task: TaskDefinition, extra_ctx: dict, dry_run: bool = False) -> TaskResult:
        run_id = hashlib.md5(
            f"{task.task_id}{datetime.now().isoformat()}".encode()
        ).hexdigest()[:12]

        effective_dry = task.dry_run or dry_run
        ctx = {**task.context, **extra_ctx}

        # Insert an in-flight row so /tasks/runs/all/recent reflects running tasks.
        # The INSERT OR REPLACE at the end of this method overwrites with the final result.
        started_at = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO task_runs(run_id,task_id,status,dry_run,started_at,finished_at,result) VALUES(?,?,?,?,?,?,?)",
                (run_id, task.task_id, "running", int(effective_dry), started_at, None, None)
            )

        ws = WorkspaceManager(task.workspace.id, run_id)
        ws.snapshot()

        log: list[str] = [f"run_id={run_id} task={task.task_id} dry={effective_dry}"]
        outputs: dict[str, Any] = {}
        executor = StepExecutor(effective_dry, ws, log)

        plan = cls._topo_sort(task.steps)
        log.append(f"plan: {[s.step_id for s in plan]}")

        rollback_ops: list[tuple[TaskStep, Any]] = []
        steps_done = 0
        error_msg = None

        try:
            for step in plan:
                log.append(f"→ {step.step_id} [{step.type}]")

                pre_validators = step.validators.get("pre", [])
                ok, reason = _run_validators(pre_validators, None, effective_dry)
                if not ok:
                    action = pre_validators[0].on_fail if pre_validators else "abort"
                    if action == "skip":
                        log.append(f"  skip: {reason}")
                        outputs[step.output_key or step.step_id] = {"skipped": True}
                        continue
                    raise RuntimeError(f"pre-validator: {reason}")

                retries = 0
                max_retries = max((v.max_retries for v in step.validators.get("post", [])), default=1)
                output = None
                while retries <= max_retries:
                    output = await executor.execute(step, ctx)
                    post_validators = step.validators.get("post", [])
                    ok, reason = _run_validators(post_validators, output, effective_dry)
                    if ok:
                        break
                    retries += 1
                    if retries > max_retries:
                        action = post_validators[0].on_fail if post_validators else "abort"
                        if action == "continue":
                            log.append(f"  post-validator failed, continuing: {reason}")
                            break
                        raise RuntimeError(f"post-validator: {reason}")
                    log.append(f"  retry {retries}/{max_retries}: {reason}")

                key = step.output_key or step.step_id
                outputs[key] = output
                ws.set(f"{step.step_id}.output.{key}", output)

                if step.rollback:
                    rollback_ops.append((step, ws._snapshots[-1] if ws._snapshots else None))

                steps_done += 1
                log.append(f"  ✓ {step.step_id}")

        except Exception as e:
            error_msg = str(e)
            log.append(f"[ERROR] {error_msg}")

            if task.on_failure == "rollback_all":
                log.append("rolling back...")
                ws.restore_snapshot()
                status = "rolled_back"
            else:
                status = "failed"
        else:
            status = "dry_run" if effective_dry else "success"

        if task.workspace.persist and not effective_dry:
            ws.save()

        result = TaskResult(
            run_id=run_id,
            task_id=task.task_id,
            status=status,
            steps_completed=steps_done,
            outputs=outputs,
            log=log,
            error=error_msg,
        )

        finished_at = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO task_runs(run_id,task_id,status,dry_run,started_at,finished_at,result) VALUES(?,?,?,?,?,?,?)",
                (run_id, task.task_id, status, int(effective_dry), started_at, finished_at, result.model_dump_json())
            )

        return result


# ── Task registry ──────────────────────────────────────────────────────────

def register_task(task: TaskDefinition):
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO task_definitions(task_id, definition, created_at) VALUES(?,?,?)",
            (task.task_id, task.model_dump_json(), datetime.now(timezone.utc).isoformat())
        )


def get_task_definition(task_id: str) -> Optional[TaskDefinition]:
    with get_db() as db:
        row = db.execute(
            "SELECT definition FROM task_definitions WHERE task_id=?", (task_id,)
        ).fetchone()
        if row:
            return TaskDefinition.model_validate_json(row["definition"])
    return None


def list_task_definitions() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT task_id, created_at FROM task_definitions ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_run(run_id: str) -> Optional[dict]:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM task_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def list_runs(task_id: str = None, limit: int = 20) -> list[dict]:
    with get_db() as db:
        if task_id:
            rows = db.execute(
                "SELECT run_id, task_id, status, dry_run, started_at FROM task_runs WHERE task_id=? ORDER BY started_at DESC LIMIT ?",
                (task_id, limit)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT run_id, task_id, status, dry_run, started_at FROM task_runs ORDER BY started_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

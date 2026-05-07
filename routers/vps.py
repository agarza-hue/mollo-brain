"""Monitor de estado del VPS — CPU, RAM, disco, red, contenedores."""
import subprocess, shutil, os, time
from fastapi import APIRouter
from datetime import datetime

router = APIRouter(prefix="/vps", tags=["VPS Monitor"])


def _run(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def get_cpu() -> dict:
    load = _run("cat /proc/loadavg").split()
    cores = int(_run("nproc") or 1)
    cpu_pct = _run("top -bn1 | grep 'Cpu(s)' | awk '{print $2+$4}'")
    return {
        "uso_pct": float(cpu_pct) if cpu_pct else None,
        "load_1m": float(load[0]) if load else None,
        "load_5m": float(load[1]) if len(load) > 1 else None,
        "load_15m": float(load[2]) if len(load) > 2 else None,
        "nucleos": cores,
    }


def get_ram() -> dict:
    raw = _run("free -m")
    lines = raw.split("\n")
    for line in lines:
        if line.startswith("Mem:"):
            parts = line.split()
            total = int(parts[1])
            used  = int(parts[2])
            free  = int(parts[3])
            available = int(parts[6]) if len(parts) > 6 else free
            return {
                "total_mb": total,
                "usado_mb": used,
                "libre_mb": free,
                "disponible_mb": available,
                "uso_pct": round(used / total * 100, 1),
            }
    return {}


def get_swap() -> dict:
    raw = _run("free -m")
    for line in raw.split("\n"):
        if line.startswith("Swap:"):
            parts = line.split()
            total = int(parts[1])
            used  = int(parts[2])
            return {
                "total_mb": total,
                "usado_mb": used,
                "uso_pct": round(used / total * 100, 1) if total > 0 else 0,
            }
    return {}


def get_discos() -> list[dict]:
    raw = _run("df -h --output=source,size,used,avail,pcent,target | tail -n +2")
    discos = []
    for line in raw.split("\n"):
        parts = line.split()
        if len(parts) >= 6 and parts[0].startswith("/dev/"):
            uso_str = parts[4].replace("%", "")
            discos.append({
                "dispositivo": parts[0],
                "tamaño": parts[1],
                "usado": parts[2],
                "disponible": parts[3],
                "uso_pct": int(uso_str) if uso_str.isdigit() else 0,
                "montado_en": parts[5],
            })
    return discos


def get_docker() -> list[dict]:
    raw = _run("docker ps --format '{{.Names}}|{{.Status}}|{{.Image}}|{{.Ports}}'")
    containers = []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            containers.append({
                "nombre": parts[0],
                "estado": parts[1],
                "imagen": parts[2],
                "puertos": parts[3] if len(parts) > 3 else "",
            })
    return containers


def get_procesos_top() -> list[dict]:
    raw = _run("ps aux --sort=-%cpu | head -8 | tail -7")
    procs = []
    for line in raw.split("\n"):
        parts = line.split(None, 10)
        if len(parts) >= 11:
            procs.append({
                "usuario": parts[0],
                "pid": parts[1],
                "cpu_pct": parts[2],
                "mem_pct": parts[3],
                "comando": parts[10][:60],
            })
    return procs


def get_red() -> dict:
    rx = _run("cat /sys/class/net/$(ip route | grep default | awk '{print $5}')/statistics/rx_bytes 2>/dev/null || echo 0")
    tx = _run("cat /sys/class/net/$(ip route | grep default | awk '{print $5}')/statistics/tx_bytes 2>/dev/null || echo 0")
    iface = _run("ip route | grep default | awk '{print $5}'")
    return {
        "interfaz": iface,
        "rx_gb": round(int(rx or 0) / 1e9, 2),
        "tx_gb": round(int(tx or 0) / 1e9, 2),
    }


def get_uptime() -> str:
    return _run("uptime -p")


def get_servicios() -> dict:
    checks = {
        "mollo_brain": "http://localhost:8002/health",
        "strategy_os": "http://localhost:8001/api/docs",
        "qdrant":      "http://localhost:6333/healthz",
        "n8n":         "http://localhost:5678/healthz",
    }
    result = {}
    for nombre, url in checks.items():
        code = _run(f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 2 {url}")
        result[nombre] = "✅ OK" if code == "200" else f"⚠️ {code or 'sin respuesta'}"
    return result


@router.get("/status")
def vps_status():
    """Snapshot completo del estado del VPS."""
    return {
        "timestamp": datetime.now().isoformat(),
        "uptime": get_uptime(),
        "cpu": get_cpu(),
        "ram": get_ram(),
        "swap": get_swap(),
        "discos": get_discos(),
        "docker": get_docker(),
        "red": get_red(),
        "servicios": get_servicios(),
        "procesos_top_cpu": get_procesos_top(),
    }


@router.post("/ask")
async def vps_ask(body: dict = None):
    """Pregunta a Mollo sobre el estado del VPS con análisis ejecutivo de Claude."""
    import json
    from claude_service import chat_with_rag

    pregunta = (body or {}).get("pregunta", "Dame un análisis ejecutivo completo del estado del VPS")
    status = vps_status()

    contexto = f"""ESTADO ACTUAL DEL VPS (capturado ahora mismo):

UPTIME: {status['uptime']}

CPU:
- Uso actual: {status['cpu'].get('uso_pct')}%
- Load avg 1/5/15 min: {status['cpu'].get('load_1m')} / {status['cpu'].get('load_5m')} / {status['cpu'].get('load_15m')}
- Núcleos: {status['cpu'].get('nucleos')}

RAM:
- Total: {status['ram'].get('total_mb')} MB
- Usado: {status['ram'].get('usado_mb')} MB ({status['ram'].get('uso_pct')}%)
- Disponible: {status['ram'].get('disponible_mb')} MB

SWAP:
- Total: {status['swap'].get('total_mb')} MB
- Usado: {status['swap'].get('usado_mb')} MB ({status['swap'].get('uso_pct')}%)

DISCOS:
{json.dumps(status['discos'], ensure_ascii=False, indent=2)}

CONTENEDORES DOCKER:
{json.dumps(status['docker'], ensure_ascii=False, indent=2)}

SERVICIOS:
{json.dumps(status['servicios'], ensure_ascii=False, indent=2)}

RED:
- Interfaz: {status['red'].get('interfaz')}
- Descarga total: {status['red'].get('rx_gb')} GB
- Subida total: {status['red'].get('tx_gb')} GB

PROCESOS TOP CPU:
{json.dumps(status['procesos_top_cpu'], ensure_ascii=False, indent=2)}
"""

    texto, usage = chat_with_rag(
        pregunta=pregunta,
        doc_context=contexto,
    )

    try:
        import cost_service
        cost_service.record(
            model=usage.get("model", "claude-sonnet-4-6"),
            modo="vps_ask",
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            query_preview=pregunta,
            topic="vps_infra",
        )
    except Exception:
        pass

    return {"respuesta": texto, "datos_raw": status}


@router.get("/resumen")
def vps_resumen():
    """Resumen ejecutivo rápido del VPS."""
    ram = get_ram()
    discos = get_discos()
    disco_raiz = next((d for d in discos if d["montado_en"] == "/"), {})

    alertas = []
    if ram.get("uso_pct", 0) > 85:
        alertas.append(f"🔴 RAM crítica: {ram['uso_pct']}% usado")
    elif ram.get("uso_pct", 0) > 70:
        alertas.append(f"🟡 RAM alta: {ram['uso_pct']}% usado")

    if disco_raiz.get("uso_pct", 0) > 85:
        alertas.append(f"🔴 Disco crítico: {disco_raiz['uso_pct']}% usado")
    elif disco_raiz.get("uso_pct", 0) > 70:
        alertas.append(f"🟡 Disco alto: {disco_raiz['uso_pct']}% usado")

    cpu = get_cpu()
    if cpu.get("uso_pct", 0) > 80:
        alertas.append(f"🔴 CPU alta: {cpu['uso_pct']}%")

    return {
        "timestamp": datetime.now().isoformat(),
        "uptime": get_uptime(),
        "ram_uso_pct": ram.get("uso_pct"),
        "ram_disponible_mb": ram.get("disponible_mb"),
        "disco_uso_pct": disco_raiz.get("uso_pct"),
        "disco_disponible": disco_raiz.get("disponible"),
        "cpu_uso_pct": cpu.get("uso_pct"),
        "contenedores_activos": len(get_docker()),
        "alertas": alertas if alertas else ["✅ Todo en orden"],
    }

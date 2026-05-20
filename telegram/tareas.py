import os
import time
import subprocess
import requests
from dotenv import load_dotenv
from openai import OpenAI
import anthropic
from noticias import briefing_noticias

load_dotenv("/opt/mollo-telegram/.env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
claude_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Umbrales para alertas (%)
UMBRAL_CPU  = 80
UMBRAL_RAM  = 85
UMBRAL_DISK = 85
COOLDOWN    = 1800  # segundos entre alertas del mismo tipo (30 min)

_ultimo_alerta = {"cpu": 0, "ram": 0, "disk": 0}
_ultimo_reporte_diario = ""
_ultimo_briefing_noticias = ""


def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": ALLOWED_USER_ID, "text": msg[:4000]}, timeout=15)
    except Exception as e:
        print(f"[send_telegram error] {e}")


def run_cmd(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return (r.stdout or r.stderr).strip()
    except Exception as e:
        return str(e)


def get_metricas():
    cpu_raw  = run_cmd("top -bn1 | grep 'Cpu'")
    mem_raw  = run_cmd("free -h")
    disk_raw = run_cmd("df -h /")

    # CPU %  (parsea el % idle y lo resta de 100)
    cpu_pct = 0.0
    try:
        for part in cpu_raw.replace(",", " ").split():
            if part.replace(".", "", 1).isdigit():
                # busca el valor antes de "id"
                pass
        # formato: "Cpu(s):  4.3 us,  1.2 sy, ..., 93.1 id, ..."
        for segment in cpu_raw.split(","):
            if "id" in segment:
                idle = float(segment.strip().split()[0])
                cpu_pct = round(100.0 - idle, 1)
                break
    except Exception:
        pass

    # RAM %
    mem_pct = 0.0
    try:
        mem_num = run_cmd("free -m")
        for line in mem_num.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                total, used = int(parts[1]), int(parts[2])
                mem_pct = round((used / total) * 100, 1)
                break
    except Exception:
        pass

    # Disco %
    disk_pct = 0.0
    try:
        for line in disk_raw.splitlines():
            if line.strip().endswith("/"):
                for part in line.split():
                    if part.endswith("%"):
                        disk_pct = float(part.rstrip("%"))
                        break
    except Exception:
        pass

    return {
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "disk_pct": disk_pct,
        "cpu_raw": cpu_raw,
        "mem_raw": mem_raw,
        "disk_raw": disk_raw,
    }


def analizar_con_ia(reporte):
    """Analiza con Claude; si falla, intenta con OpenAI; si falla, devuelve el error real."""
    try:
        r = claude_client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=600,
            system=(
                "Eres analista de operaciones de servidores Linux. "
                "Analiza el reporte y responde en 3 puntos breves en español: "
                "1) Estado general, 2) Riesgos detectados, 3) Acciones recomendadas. "
                "Si todo está bien, di 'Sin riesgos detectados.' en una línea."
            ),
            messages=[{"role": "user", "content": reporte}]
        )
        return r.content[0].text.strip()
    except Exception as e_claude:
        try:
            r = openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "Analista de servidores Linux. Responde en español con: estado, riesgos y acciones."},
                    {"role": "user", "content": reporte}
                ]
            )
            return r.choices[0].message.content.strip()
        except Exception as e_openai:
            return f"No se pudo analizar con IA.\nClaude: {e_claude}\nOpenAI: {e_openai}"


def revisar_vps():
    """Envía alerta solo cuando un umbral es superado y respetando cooldown."""
    m = get_metricas()
    ahora = time.time()
    alertas = []

    if m["cpu_pct"] > UMBRAL_CPU and (ahora - _ultimo_alerta["cpu"]) > COOLDOWN:
        alertas.append(f"CPU: {m['cpu_pct']}%  (umbral {UMBRAL_CPU}%)")
        _ultimo_alerta["cpu"] = ahora

    if m["mem_pct"] > UMBRAL_RAM and (ahora - _ultimo_alerta["ram"]) > COOLDOWN:
        alertas.append(f"RAM: {m['mem_pct']}%  (umbral {UMBRAL_RAM}%)")
        _ultimo_alerta["ram"] = ahora

    if m["disk_pct"] > UMBRAL_DISK and (ahora - _ultimo_alerta["disk"]) > COOLDOWN:
        alertas.append(f"Disco: {m['disk_pct']}%  (umbral {UMBRAL_DISK}%)")
        _ultimo_alerta["disk"] = ahora

    if not alertas:
        return  # todo bien, no manda nada

    detalle = (
        f"CPU:   {m['cpu_raw']}\n"
        f"RAM:   {m['mem_raw']}\n"
        f"Disco: {m['disk_raw']}"
    )
    analisis = analizar_con_ia(f"Alertas activas:\n{chr(10).join(alertas)}\n\nDetalle:\n{detalle}")
    send_telegram(
        f"ALERTA VPS\n"
        f"{chr(10).join(alertas)}\n\n"
        f"{analisis}"
    )


def reporte_diario():
    """Reporte de las 9am con análisis completo."""
    m = get_metricas()
    resumen = (
        f"CPU: {m['cpu_pct']}% | RAM: {m['mem_pct']}% | Disco: {m['disk_pct']}%\n\n"
        f"RAM detalle:\n{m['mem_raw']}\n\n"
        f"Disco detalle:\n{m['disk_raw']}"
    )
    analisis = analizar_con_ia(f"Reporte matutino del VPS:\n{resumen}")
    send_telegram(f"Reporte diario VPS\n{resumen}\n\nAnalisis:\n{analisis}")


def main():
    global _ultimo_reporte_diario, _ultimo_briefing_noticias
    send_telegram("Mollo autonomo iniciado. Monitoreando VPS cada 5 min.")

    while True:
        try:
            revisar_vps()

            hora_actual = time.strftime("%H")
            fecha_hora  = time.strftime("%Y-%m-%d-%H")

            # Briefing de noticias a las 7am
            if hora_actual == "07" and fecha_hora != _ultimo_briefing_noticias:
                send_telegram("Buenos dias. Preparando briefing de noticias...")
                briefing = briefing_noticias()
                send_telegram(f"Noticias del dia\n\n{briefing}")
                _ultimo_briefing_noticias = fecha_hora

            # Reporte VPS a las 9am
            if hora_actual == "09" and fecha_hora != _ultimo_reporte_diario:
                reporte_diario()
                _ultimo_reporte_diario = fecha_hora

        except Exception as e:
            send_telegram(f"Error en tareas autonomas: {e}")

        time.sleep(300)


if __name__ == "__main__":
    main()

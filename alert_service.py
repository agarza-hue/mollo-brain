"""Envío de alertas por email vía Resend HTTP API."""
import os
import urllib.request
import urllib.error
import json

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL     = os.getenv("ALERT_FROM_EMAIL", "SinergyOS <alertas@sinergy.io>")


def _send(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print("[alert_service] RESEND_API_KEY no configurada — email no enviado")
        return False
    try:
        payload = json.dumps({
            "from":    FROM_EMAIL,
            "to":      [to],
            "subject": subject,
            "html":    html,
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as e:
        print(f"[alert_service] Resend error {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"[alert_service] Error enviando email a {to}: {e}")
    return False


def send_usage_alert_80(tenant_name: str, email: str, plan: str,
                        req_used: int, req_limit: int, slug: str) -> bool:
    pct       = round(req_used / req_limit * 100, 1) if req_limit else 0
    remaining = req_limit - req_used
    subject   = f"⚠️ {tenant_name}: has usado el {pct}% de tus requests"
    html = f"""
<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;background:#0f0f0f;color:#e5e5e5;border-radius:12px;overflow:hidden">
  <div style="background:#7c3aed;padding:24px 32px">
    <h1 style="margin:0;font-size:20px;color:#fff">SinergyOS — Alerta de uso</h1>
  </div>
  <div style="padding:32px">
    <p style="margin:0 0 16px">Hola <strong>{tenant_name}</strong>,</p>
    <p style="margin:0 0 24px;color:#a3a3a3">
      Has alcanzado el <strong style="color:#f59e0b">{pct}%</strong> de tu límite mensual.
    </p>
    <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:20px;margin-bottom:24px">
      <table style="width:100%;border-collapse:collapse">
        <tr>
          <td style="color:#737373;padding:4px 0;font-size:13px">Plan</td>
          <td style="text-align:right;font-weight:600;text-transform:capitalize">{plan}</td>
        </tr>
        <tr>
          <td style="color:#737373;padding:4px 0;font-size:13px">Requests usados</td>
          <td style="text-align:right;font-weight:600;color:#f59e0b">{req_used:,} / {req_limit:,}</td>
        </tr>
        <tr>
          <td style="color:#737373;padding:4px 0;font-size:13px">Requests restantes</td>
          <td style="text-align:right;font-weight:600;color:#34d399">{remaining:,}</td>
        </tr>
      </table>
    </div>
    <p style="margin:0 0 24px;color:#a3a3a3;font-size:14px">
      Cuando llegues al 100% tus requests serán bloqueadas hasta el próximo ciclo.
      Considera actualizar tu plan para evitar interrupciones.
    </p>
    <a href="mailto:agarza@bion-business.com?subject=Upgrade plan - {slug}"
       style="display:inline-block;background:#7c3aed;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
      Contactar para upgrade →
    </a>
  </div>
  <div style="padding:16px 32px;border-top:1px solid #1a1a1a;font-size:11px;color:#525252">
    SinergyOS — Agentes IA para tu empresa
  </div>
</div>"""
    return _send(email, subject, html)


def send_limit_reached(tenant_name: str, email: str, plan: str,
                       req_limit: int, slug: str) -> bool:
    subject = f"🚫 {tenant_name}: límite mensual alcanzado"
    html = f"""
<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;background:#0f0f0f;color:#e5e5e5;border-radius:12px;overflow:hidden">
  <div style="background:#dc2626;padding:24px 32px">
    <h1 style="margin:0;font-size:20px;color:#fff">SinergyOS — Límite alcanzado</h1>
  </div>
  <div style="padding:32px">
    <p style="margin:0 0 16px">Hola <strong>{tenant_name}</strong>,</p>
    <p style="margin:0 0 24px;color:#a3a3a3">
      Has alcanzado tu límite de <strong style="color:#f87171">{req_limit:,} requests</strong>
      del plan <strong>{plan}</strong>. Tus consultas están siendo bloqueadas.
    </p>
    <a href="mailto:agarza@bion-business.com?subject=Upgrade urgente - {slug}"
       style="display:inline-block;background:#dc2626;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
      Contactar para upgrade →
    </a>
  </div>
  <div style="padding:16px 32px;border-top:1px solid #1a1a1a;font-size:11px;color:#525252">
    SinergyOS — Agentes IA para tu empresa
  </div>
</div>"""
    return _send(email, subject, html)

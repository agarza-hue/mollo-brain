"""Envío de alertas por email vía Resend HTTP API."""
import os
import urllib.request
import urllib.error
import json

RESEND_API_KEY    = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL        = os.getenv("ALERT_FROM_EMAIL", "SinergyOS <alertas@sinergy.io>")
MOLLOIA_FROM_EMAIL = os.getenv("MOLLOIA_FROM_EMAIL", FROM_EMAIL)


def _send(to: str, subject: str, html: str, from_email: str | None = None) -> bool:
    if not RESEND_API_KEY:
        print("[alert_service] RESEND_API_KEY no configurada — email no enviado")
        return False
    try:
        payload = json.dumps({
            "from":    from_email or FROM_EMAIL,
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
                # Cloudflare bloquea Python-urllib default UA con código 1010
                "User-Agent":    "mollo_brain/1.0 (+https://app.mollo-ai.com)",
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


def send_molloia_welcome(email: str, plan: str, register_url: str,
                         amount_paid_usd: float | None = None,
                         quantity: int = 1) -> bool:
    """Email de bienvenida tras pago confirmado en MolloIA Pro/Team.
    El register_url ya viene prefilled con email + plan para auto-link de la
    suscripción huérfana cuando el usuario complete su registro."""
    plan_label  = {"pro": "Pro", "team": "Team"}.get(plan, plan.capitalize())
    monthly_str = f"${amount_paid_usd:.2f} USD" if amount_paid_usd else ""
    seats_str   = f" · {quantity} asientos" if quantity > 1 else ""
    subject     = f"Bienvenido a MolloIA {plan_label} — Activa tu cuenta"

    html = f"""
<div style="font-family:'Inter',Arial,sans-serif;max-width:560px;margin:0 auto;background:#F4F0E9;color:#1A1915;border-radius:14px;overflow:hidden">
  <div style="background:#D97757;padding:32px 32px 28px">
    <h1 style="margin:0;font-family:'Fraunces',Georgia,serif;font-weight:500;font-size:28px;color:#fff;letter-spacing:-0.01em">
      Bienvenido a MolloIA
    </h1>
    <p style="margin:8px 0 0;font-size:15px;color:rgba(255,255,255,0.9)">
      Tu plan {plan_label} está activo.
    </p>
  </div>

  <div style="padding:32px">
    <p style="margin:0 0 20px;font-size:15px;line-height:1.6">
      Hola, gracias por sumarte. Tu pago se procesó correctamente y ya tienes acceso al plan
      <strong>MolloIA {plan_label}</strong>{seats_str}.
    </p>

    <div style="background:#EDE6D8;border:1px solid rgba(26,25,21,0.08);border-radius:10px;padding:18px 20px;margin:0 0 24px">
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <tr>
          <td style="color:#3D3B36;padding:4px 0">Plan</td>
          <td style="text-align:right;font-weight:600">MolloIA {plan_label}</td>
        </tr>
        <tr>
          <td style="color:#3D3B36;padding:4px 0">Pago confirmado</td>
          <td style="text-align:right;font-weight:600;color:#1f7a4a">{monthly_str or 'OK'}</td>
        </tr>
        <tr>
          <td style="color:#3D3B36;padding:4px 0">Email cuenta</td>
          <td style="text-align:right;font-weight:600">{email}</td>
        </tr>
      </table>
    </div>

    <p style="margin:0 0 8px;font-size:15px;font-weight:600">Último paso — define tu contraseña:</p>
    <p style="margin:0 0 20px;font-size:14px;color:#3D3B36;line-height:1.5">
      Por seguridad no creamos automáticamente tu contraseña. Crea la tuya con un click — el botón
      de abajo te lleva a la página de registro con tu email ya pre-llenado.
    </p>

    <a href="{register_url}"
       style="display:inline-block;background:#D97757;color:#fff;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px;letter-spacing:-0.005em">
      Activar mi cuenta →
    </a>

    <p style="margin:24px 0 0;font-size:12px;color:#3D3B36;line-height:1.5">
      Si el botón no funciona copia este link en tu navegador:<br>
      <span style="color:#D97757;word-break:break-all">{register_url}</span>
    </p>

    <hr style="border:none;border-top:1px solid rgba(26,25,21,0.08);margin:28px 0 20px">

    <p style="margin:0 0 6px;font-size:13px;color:#3D3B36">¿Algo no funcionó?</p>
    <p style="margin:0;font-size:13px;color:#3D3B36">
      Escríbenos a <a href="mailto:soporte@mollo-ai.com" style="color:#D97757;font-weight:500">soporte@mollo-ai.com</a> —
      te respondemos personalmente.
    </p>
  </div>

  <div style="padding:18px 32px;background:#1A1915;color:rgba(244,240,233,0.6);font-size:11px">
    MolloIA · Hecho en Monterrey · <a href="https://landing.mollo-ai.com/terminos" style="color:rgba(244,240,233,0.7);text-decoration:none">Términos</a> · <a href="https://landing.mollo-ai.com/privacidad" style="color:rgba(244,240,233,0.7);text-decoration:none">Privacidad</a>
  </div>
</div>"""
    return _send(email, subject, html, from_email=MOLLOIA_FROM_EMAIL)


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

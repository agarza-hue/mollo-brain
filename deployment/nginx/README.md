# Nginx config snapshot — juntas_nginx

Snapshot del archivo `/root/projects/juntas-app/nginx/default.conf` que vive
bind-mounted en el container `juntas_nginx`. Sirve TLS + reverse proxy para
**todos los dominios** del VPS (no solo Mollo).

Versionado aquí en `mollo-brain/deployment/nginx/` para reproducibilidad,
porque `juntas-app/` no está bajo git.

## Servidores configurados

| Server name | Backend | Propósito |
|---|---|---|
| `app.mollo-ai.com` | mollo-os :3006 (default `/`) | Frontend rebrand nuevo |
| `app.mollo-ai.com/mollo/*` | mollo-web :3001 | Chat + dashboard legacy (paleta Mocha) |
| `app.mollo-ai.com/billing/*` | mollo_brain :8002 | Stripe checkout/webhook |
| `landing.mollo-ai.com` | static `/var/www/mollo-ai/landing/` | Landing brand v2 |
| `media.bion-business.com` | mcp-bridge :3456 | MCP bridge SinergyOS |
| Default (`server_name _`) | mollo-web :3001 vía path `/mollo` | Acceso directo via IP |

## Bind-mount inode gotcha

El archivo está bind-mounted como SINGLE FILE (no directorio):
```
/root/projects/juntas-app/nginx/default.conf → /etc/nginx/conf.d/default.conf
```

Editar con herramientas que hacen atomic rename (Edit tool, vim default,
muchos editores) **rompe el bind-mount** porque cambia el inode. El
container queda viendo el inode viejo (vacío después del rename).

**Workaround obligatorio tras editar:**
```bash
docker restart juntas_nginx
```
NO sirve `nginx -s reload` ni `docker exec ... nginx -s reload` — el
container ya no ve los cambios. Hay que restart full.

Validar siempre antes de restart:
```bash
docker exec juntas_nginx nginx -t
```

## Re-instalación

Si necesitas reproducir el setup en otro VPS:
```bash
# 1. Copiar el config al path del juntas-app
mkdir -p /root/projects/juntas-app/nginx
cp /root/mollo_brain/deployment/nginx/juntas_nginx.conf \
   /root/projects/juntas-app/nginx/default.conf

# 2. Asegurar que /etc/letsencrypt tenga los certs reales
# (los paths ssl_certificate apuntan a /etc/letsencrypt/live/<domain>/)

# 3. Levantar el container desde docker-compose de juntas-app

# 4. Verificar
docker exec juntas_nginx nginx -t
docker logs juntas_nginx --tail 30
```

## Gotchas históricos registrados

- **`/mollo/*` rutea a mollo-web (3001), no a mollo-os (3006)** — porque
  mollo-os (rebrand nuevo) todavía no tiene dashboard de costos integrado.
  Cuando mollo-os incorpore dashboard propio, este routeo se puede simplificar.
- **`mollo-ai.com` apex (sin app/landing/www)** apunta a AWS, fuera de este VPS.
- **error_page 502 503 504 → /__maintenance.html** intercepta downtimes de
  mollo_brain o Next.js dev y muestra página suave brand-aligned con
  auto-refresh (alineado con la mejora hardening 2026-05-09).

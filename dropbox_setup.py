#!/usr/bin/env python3
"""
Setup de autenticación Dropbox para Mollo.
Ejecutar UNA VEZ para obtener el refresh token.

Pasos previos:
  1. Ve a https://www.dropbox.com/developers/apps
  2. Crea una app → Scoped Access → Full Dropbox (o App Folder)
  3. En Permissions activa: files.content.read, files.content.write, files.metadata.read
  4. Copia App Key y App Secret
  5. Ejecuta: python dropbox_setup.py
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv, set_key

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)


def main():
    print("=" * 55)
    print("  Configuración de Dropbox para Mollo")
    print("=" * 55)
    print()
    print("1. Ve a https://www.dropbox.com/developers/apps")
    print("2. Crea una app con 'Full Dropbox' access")
    print("3. En la pestaña Permissions activa:")
    print("     files.content.read")
    print("     files.content.write")
    print("     files.metadata.read")
    print("4. Vuelve a Settings y copia App key y App secret")
    print()

    app_key    = input("App Key:    ").strip()
    app_secret = input("App Secret: ").strip()

    if not app_key or not app_secret:
        print("Error: necesitas App Key y App Secret.")
        sys.exit(1)

    import dropbox
    from dropbox import DropboxOAuth2FlowNoRedirect

    auth_flow = DropboxOAuth2FlowNoRedirect(
        app_key, app_secret,
        token_access_type="offline",   # offline = refresh token de larga duración
    )

    auth_url = auth_flow.start()
    print()
    print("Abre esta URL en tu navegador y autoriza la app:")
    print()
    print(f"  {auth_url}")
    print()
    auth_code = input("Pega aquí el código de autorización: ").strip()

    try:
        oauth_result = auth_flow.finish(auth_code)
    except Exception as e:
        print(f"Error al completar autorización: {e}")
        sys.exit(1)

    # Guardar en .env
    set_key(str(ENV_FILE), "DROPBOX_APP_KEY",       app_key)
    set_key(str(ENV_FILE), "DROPBOX_APP_SECRET",    app_secret)
    set_key(str(ENV_FILE), "DROPBOX_REFRESH_TOKEN", oauth_result.refresh_token)

    # Verificar
    dbx = dropbox.Dropbox(
        app_key=app_key,
        app_secret=app_secret,
        oauth2_refresh_token=oauth_result.refresh_token,
    )
    account = dbx.users_get_current_account()
    print()
    print(f"Conectado como: {account.name.display_name} ({account.email})")
    print()
    print("Tokens guardados en .env")
    print("Reinicia Mollo: bash start.sh")


if __name__ == "__main__":
    main()

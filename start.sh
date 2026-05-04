#!/bin/bash
set -e

cd /root/mollo_brain

# Mollo Brain API
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "telegram_bot.py"  2>/dev/null || true
sleep 1

nohup /root/venv/bin/python -m uvicorn main:app \
  --host 0.0.0.0 --port 8002 \
  >> /var/log/mollo_brain.log 2>&1 &
echo "Mollo Brain  arrancado en :8002 (PID: $!)"

sleep 2  # espera a que el Brain esté listo antes de que el bot intente conectarse

nohup /root/venv/bin/python telegram_bot.py \
  >> /var/log/mollo_telegram.log 2>&1 &
echo "Mollo Telegram Bot arrancado (PID: $!)"

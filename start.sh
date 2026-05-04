#!/bin/bash
pkill -f "mollo_brain" 2>/dev/null
sleep 1
cd /root/mollo_brain
nohup /root/venv/bin/python -m uvicorn main:app \
  --host 0.0.0.0 --port 8002 \
  >> /var/log/mollo_brain.log 2>&1 &
echo "Mollo Brain iniciado en puerto 8002 (PID: $!)"

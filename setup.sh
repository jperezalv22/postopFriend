#!/usr/bin/env bash
# postopFriend — preparación en un comando (Linux / macOS)
#
#   bash setup.sh
#
# Equivalente a setup.ps1. Ver ahí los comentarios.

set -euo pipefail
inicio=$(date +%s)

echo ""
echo "postopFriend - preparacion del entorno"
echo "------------------------------------------------------------"

command -v python3 >/dev/null || { echo "No se encontro python3"; exit 1; }
echo "[1/5] Python $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"

if [ ! -d .venv ]; then
  echo "[2/5] Creando entorno virtual..."
  python3 -m venv .venv
else
  echo "[2/5] Entorno virtual ya existe"
fi
PY=".venv/bin/python"

echo "[3/5] Instalando dependencias (~180 MB, 1-3 min)..."
"$PY" -m pip install --upgrade pip --quiet --disable-pip-version-check
"$PY" -m pip install -r requirements.txt --quiet --disable-pip-version-check

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[4/5] Creado .env - PEGUE SU GROQ_API_KEY ahi"
  echo "      Consigala gratis en https://console.groq.com/keys"
else
  echo "[4/5] .env ya existe"
fi

echo "[5/5] Descargando el modelo de embeddings (~220 MB, solo la primera vez)..."
"$PY" scripts/precalentar.py

echo "------------------------------------------------------------"
echo "Listo en $(( $(date +%s) - inicio )) s"
echo ""
"$PY" scripts/doctor.py
echo ""
echo "Para arrancar:  .venv/bin/uvicorn app.main:app"
echo "Luego abra:     http://127.0.0.1:8000"
echo ""

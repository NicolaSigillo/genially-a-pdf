#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/2] Creando entorno virtual de Python..."
  python3 -m venv .venv
  echo "[2/2] Instalando dependencias (solo la primera vez)..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "Iniciando Genially a PDF..."
.venv/bin/python app.py

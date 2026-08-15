#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  init.command
#  Prepara todo lo necesario (Python, .env, dependencias) y arranca
#  el panel de automatización del Juzgado de Faltas para el SIGI.
# ─────────────────────────────────────────────────────────────

# Ir a la carpeta donde está este script (raíz del proyecto)
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Panel de Automatización — Juzgado de Faltas    ║"
echo "║   SIGI - Municipalidad de Villa María            ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Verificar que Python3 esté instalado ──────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ Python3 no está instalado."
    echo "   Descargalo desde: https://www.python.org/downloads/"
    echo ""
    read -p "Presioná Enter para cerrar..."
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "✓ $PYTHON_VERSION encontrado."

# ── 2. Verificar que existe el .env ───────────────────────────
if [ ! -f "backend/.env" ]; then
    if [ -f "backend/.env.ejemplo" ]; then
        echo ""
        echo "⚠  No se encontró el archivo backend/.env con tus credenciales."
        echo "   Copiando backend/.env.ejemplo como backend/.env..."
        cp backend/.env.ejemplo backend/.env
        echo ""
        echo "   ➜  Abrí backend/.env con cualquier editor de texto,"
        echo "      completá usuario y contraseña, y volvé a ejecutar este script."
        echo ""
        open backend/.env 2>/dev/null || echo "   (abrilo manualmente con TextEdit o VS Code)"
        read -p "Presioná Enter para cerrar..."
        exit 1
    else
        echo ""
        echo "❌ No se encontró backend/.env ni backend/.env.ejemplo."
        echo "   Asegurate de estar en la carpeta correcta del proyecto."
        read -p "Presioná Enter para cerrar..."
        exit 1
    fi
fi

echo "✓ Archivo backend/.env encontrado."

# ── 3. Crear carpetas de datos necesarias (fuera de backend/) ─
mkdir -p datos/sesiones datos/logs datos/maestro \
         datos/descargas/pagos datos/descargas/cache_actas_cruce datos/descargas/causas
echo "✓ Carpetas de sesiones, logs y descargas listas (en datos/)."

if [ ! -f "datos/maestro/total_causas_sigemi.txt" ]; then
    echo ""
    echo "⚠  No se encontró datos/maestro/total_causas_sigemi.txt (el maestro de"
    echo "   causas de SIGEMI). El cruce de multas viejas no va a poder llenar"
    echo "   CAUSA_NUMERO sin ese archivo. Pedilo y ponelo ahí antes de usar"
    echo "   el proceso de pagos en serio."
fi

# ── 4. Instalar dependencias si faltan ────────────────────────
echo ""
echo "Verificando dependencias..."

pip3 install -r backend/requirements.txt -q --disable-pip-version-check 2>&1 \
    | grep -v "already satisfied" | grep -v "^$" || true

if ! python3 -c "from playwright.sync_api import sync_playwright" &>/dev/null; then
    echo "Instalando Playwright..."
    pip3 install playwright -q
fi

if ! python3 -m playwright install chromium --with-deps &>/dev/null 2>&1; then
    echo "Instalando navegador Chromium para Playwright..."
    python3 -m playwright install chromium
fi

echo "✓ Dependencias listas."

# ── 5. Arrancar el panel ──────────────────────────────────────
# A partir de acá, TODO (validar sesiones, mostrar el menú, correr el
# proceso de pagos) lo hace "python3 -m backend.main". Este mismo comando
# es el que hay que usar en cualquier ejecución siguiente: no hace falta
# volver a pasar por init.command salvo que cambien las dependencias.
echo ""
echo "✓ Todo listo. Iniciando el panel..."
echo ""
echo "  Para detener el panel: elegí la opción 0 (Salir), o Ctrl + C."
echo ""
echo "────────────────────────────────────────────────────"
echo ""

python3 -m backend.main

echo ""
echo "Panel detenido."

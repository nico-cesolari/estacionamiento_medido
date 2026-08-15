#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  instalar_servicio.command
#  Instala el Automático (actualizar pagos) como un servicio de verdad de
#  macOS (launchd):
#    • arranca solo al iniciar sesión (RunAtLoad),
#    • se reinicia solo si se cae por lo que sea (KeepAlive),
#    • sigue corriendo en segundo plano aunque cierres esta terminal,
#    • evita que la Mac se duerma mientras está corriendo (caffeinate).
#
#  Se puede correr las veces que haga falta (por ejemplo, si moviste la
#  carpeta del proyecto): vuelve a generar la configuración con las rutas
#  actuales y reinicia el servicio.
#
#  El panel manual (init.command) sigue funcionando igual que siempre y es
#  seguro usarlo aunque el servicio esté corriendo: por dentro se coordinan
#  para no pisarse (ver backend/orquestador/trabajador.py, lock de archivo).
# ─────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"
PROYECTO_DIR="$(pwd)"
ETIQUETA="ar.gob.villamaria.juzgadofaltas.automatico"
PLIST_DESTINO="$HOME/Library/LaunchAgents/${ETIQUETA}.plist"

echo "════════════════════════════════════════════════════════"
echo "  Instalando el Automático (pagos) como servicio de macOS"
echo "════════════════════════════════════════════════════════"
echo "Proyecto: $PROYECTO_DIR"
echo ""

if [ ! -f "$PROYECTO_DIR/backend/.env" ]; then
    echo "❌ No encuentro backend/.env."
    echo "   Corré primero init.command al menos una vez (para configurar"
    echo "   usuario/contraseña y las dependencias) y volvé a intentar."
    read -p "Presioná Enter para cerrar esta ventana..."
    exit 1
fi

PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "❌ No encuentro python3 instalado."
    echo "   Corré primero init.command, que se encarga de instalar todo lo necesario."
    read -p "Presioná Enter para cerrar esta ventana..."
    exit 1
fi
echo "python3 encontrado en: $PYTHON_BIN"

CAFFEINATE_BIN="$(command -v caffeinate || echo /usr/bin/caffeinate)"

mkdir -p "$PROYECTO_DIR/datos/logs/servicio"
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_DESTINO" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${ETIQUETA}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${CAFFEINATE_BIN}</string>
        <string>-is</string>
        <string>${PYTHON_BIN}</string>
        <string>-m</string>
        <string>backend.main</string>
        <string>--servicio</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROYECTO_DIR}</string>

    <!-- Arranca solo apenas se carga (al iniciar sesión, o ahora mismo al instalar) -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Si el proceso termina por lo que sea, launchd lo vuelve a arrancar solo -->
    <key>KeepAlive</key>
    <true/>

    <!-- Mínimo 60s entre reinicios, para no martillar el sitio si algo
         está mal de fondo (credenciales vencidas, etc.) -->
    <key>ThrottleInterval</key>
    <integer>60</integer>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>StandardOutPath</key>
    <string>${PROYECTO_DIR}/datos/logs/servicio/salida.log</string>

    <key>StandardErrorPath</key>
    <string>${PROYECTO_DIR}/datos/logs/servicio/errores.log</string>
</dict>
</plist>
PLIST

echo "✅ Configuración generada en:"
echo "   $PLIST_DESTINO"
echo ""

# Si ya estaba instalado de una vez anterior, lo descargamos primero para
# que tome la configuración nueva (rutas, etc.) sin quedar duplicado.
launchctl unload "$PLIST_DESTINO" 2>/dev/null || true

launchctl load -w "$PLIST_DESTINO"

echo "✅ Servicio instalado y arrancado."
echo ""
echo "A partir de ahora corre solo, en segundo plano, aunque cierres esta terminal:"
echo "  • Actualizar pagos: cada 1 hora"
echo "  • Limpieza de sesiones guardadas: todos los días a las 00:00"
echo "  • Si reiniciás la Mac o volvés a iniciar sesión, arranca solo"
echo "  • Si el proceso se cae por lo que sea, se reinicia solo"
echo ""
echo "⚠ Importante: esto arranca cuando VOS iniciás sesión en la Mac, no antes."
echo "  Si la Mac se reinicia y queda en la pantalla de login sin que nadie"
echo "  entre, el servicio no arranca hasta que alguien inicie sesión."
echo ""
echo "Para ver el estado o los últimos logs: estado_servicio.command"
echo "Para desinstalarlo: desinstalar_servicio.command"
echo ""
read -p "Presioná Enter para cerrar esta ventana..."

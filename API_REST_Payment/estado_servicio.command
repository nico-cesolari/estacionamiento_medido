#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  estado_servicio.command
#  Muestra si el servicio está instalado/corriendo y las últimas líneas
#  de sus logs, para chequear rápido "¿corrió esto?" sin tener que andar
#  buscando archivos a mano.
# ─────────────────────────────────────────────────────────────

cd "$(dirname "$0")"
PROYECTO_DIR="$(pwd)"
ETIQUETA="ar.gob.villamaria.juzgadofaltas.automatico"
PLIST="$HOME/Library/LaunchAgents/${ETIQUETA}.plist"

echo "════════════════════════════════════════════════════════"
echo "  Estado del servicio del Automático"
echo "════════════════════════════════════════════════════════"

if [ ! -f "$PLIST" ]; then
    echo "❌ No está instalado. Corré instalar_servicio.command para activarlo."
    echo ""
    read -p "Presioná Enter para cerrar esta ventana..."
    exit 0
fi

echo ""
echo "— launchctl (si aparece una línea de abajo, está cargado) —"
launchctl list | grep "$ETIQUETA" || echo "(no aparece cargado; probá reinstalarlo con instalar_servicio.command)"

echo ""
echo "— Últimas líneas del log del automático (qué corrió, cuándo, reintentos) —"
# Los logs están organizados por día: datos/logs/AAAA-MM-DD/automatico.log
ULTIMO_LOG_AUTO=$(ls -t "$PROYECTO_DIR"/datos/logs/*/automatico.log 2>/dev/null | head -n1)
if [ -n "$ULTIMO_LOG_AUTO" ]; then
    echo "(archivo: ${ULTIMO_LOG_AUTO#$PROYECTO_DIR/})"
    tail -n 30 "$ULTIMO_LOG_AUTO"
else
    echo "(todavía no hay logs del automático; puede ser recién instalado)"
fi

echo ""
echo "— Últimas líneas de salida general del servicio —"
tail -n 20 "$PROYECTO_DIR/datos/logs/servicio/salida.log" 2>/dev/null || echo "(sin datos todavía)"

echo ""
echo "— Últimas líneas de errores del servicio (si el proceso se cayó feo) —"
tail -n 20 "$PROYECTO_DIR/datos/logs/servicio/errores.log" 2>/dev/null || echo "(sin errores registrados, buena señal)"

echo ""
echo "Tip: el detalle de cada corrida puntual de pagos está en"
echo "     datos/logs/AAAA-MM-DD/pagos_HHMMSS.log (solo se conserva si"
echo "     esa corrida llegó a subir un archivo al SEMyT)."
echo ""
read -p "Presioná Enter para cerrar esta ventana..."

#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  reanudar_servicio.command
#  Retoma un servicio pausado con pausar_servicio.command. Usa la misma
#  configuración que ya estaba instalada, no genera nada nuevo.
# ─────────────────────────────────────────────────────────────

ETIQUETA="ar.gob.villamaria.juzgadofaltas.automatico"
PLIST="$HOME/Library/LaunchAgents/${ETIQUETA}.plist"

echo "════════════════════════════════════════════════════════"
echo "  Reanudando el servicio del Automático"
echo "════════════════════════════════════════════════════════"

if [ ! -f "$PLIST" ]; then
    echo "❌ No hay ninguna configuración guardada (no se encontró $PLIST)."
    echo "   Corré instalar_servicio.command en vez de este."
    read -p "Presioná Enter para cerrar esta ventana..."
    exit 1
fi

launchctl load -w "$PLIST"

echo "✅ Servicio reanudado."
echo "   • Actualizar pagos: cada 1 hora"
echo "   • Pagos + multas vencidas: todos los días a la 1 AM"
echo ""
echo "Para revisar que arrancó bien: estado_servicio.command"
echo ""
read -p "Presioná Enter para cerrar esta ventana..."

#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  pausar_servicio.command
#  Frena el servicio YA, sin desinstalarlo: la configuración queda
#  guardada, pero no va a volver a arrancar solo (ni ahora ni en el
#  próximo inicio de sesión) hasta que corras reanudar_servicio.command.
#
#  Diferencia con desinstalar_servicio.command: ese borra la configuración
#  del todo (para sacarlo por completo del sistema). Este solo lo pausa.
# ─────────────────────────────────────────────────────────────

ETIQUETA="ar.gob.villamaria.juzgadofaltas.automatico"
PLIST="$HOME/Library/LaunchAgents/${ETIQUETA}.plist"

echo "════════════════════════════════════════════════════════"
echo "  Pausando el servicio del Automático"
echo "════════════════════════════════════════════════════════"

if [ ! -f "$PLIST" ]; then
    echo "ℹ No hay ningún servicio instalado (no se encontró $PLIST)."
    echo "  No hay nada que pausar."
    read -p "Presioná Enter para cerrar esta ventana..."
    exit 0
fi

launchctl unload "$PLIST" 2>/dev/null || true

echo "✅ Servicio pausado. Ya no está corriendo."
echo "   No se va a reactivar solo ni ahora ni la próxima vez que inicies sesión."
echo ""
echo "Para retomarlo: doble clic en reanudar_servicio.command"
echo "(La configuración sigue guardada; no hace falta reinstalar nada.)"
echo ""
read -p "Presioná Enter para cerrar esta ventana..."

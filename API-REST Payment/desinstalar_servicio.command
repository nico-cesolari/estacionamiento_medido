#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  desinstalar_servicio.command
#  Detiene y desinstala el servicio de launchd DE INMEDIATO: mata el
#  proceso con kill -9 antes de desregistrarlo, en vez de esperar a que
#  se apague solo (launchctl unload -w espera a que el proceso termine
#  por las suyas, y este intenta cancelar prolijamente cualquier
#  ejecución en curso, lo que puede tardar varios segundos y a veces
#  deja tracebacks de cleanup a medio terminar). El panel manual
#  (init.command) no se ve afectado: sigue funcionando exactamente igual.
# ─────────────────────────────────────────────────────────────

ETIQUETA="ar.gob.villamaria.juzgadofaltas.automatico"
PLIST="$HOME/Library/LaunchAgents/${ETIQUETA}.plist"

echo "════════════════════════════════════════════════════════"
echo "  Desinstalando el servicio del Automático"
echo "════════════════════════════════════════════════════════"

if [ ! -f "$PLIST" ]; then
    echo "ℹ No hay ningún servicio instalado (no se encontró $PLIST)."
    read -p "Presioná Enter para cerrar esta ventana..."
    exit 0
fi

# Matar el proceso YA (si está corriendo), sin esperar a que se apague
# solo. "launchctl list" imprime "PID  ÚltimoExitStatus  Etiqueta"; si el
# primer campo es un número, el servicio está corriendo con ese PID.
PID_ACTUAL=$(launchctl list | awk -v etiqueta="$ETIQUETA" '$3 == etiqueta {print $1}')

if [[ "$PID_ACTUAL" =~ ^[0-9]+$ ]]; then
    echo "🛑 Matando el proceso actual (PID $PID_ACTUAL) y todos sus hijos al instante..."

    # kill -9 solo mata al PID indicado, no a sus hijos (caffeinate -> python
    # -> workers de Playwright). Buscamos todo el árbol de descendientes
    # antes de matar, para no dejar nada huérfano corriendo en segundo plano.
    _hijos_de() {
        ps -o pid=,ppid= -ax | awk -v padre="$1" '$2 == padre {print $1}'
    }

    _arbol_completo() {
        local pid="$1"
        echo "$pid"
        for hijo in $(_hijos_de "$pid"); do
            _arbol_completo "$hijo"
        done
    }

    for pid in $(_arbol_completo "$PID_ACTUAL"); do
        kill -9 "$pid" 2>/dev/null || true
    done
fi

launchctl unload -w "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

echo "✅ Servicio detenido (al instante) y desinstalado."
echo "   El panel manual (init.command) sigue funcionando igual que siempre."
echo ""
read -p "Presioná Enter para cerrar esta ventana..."

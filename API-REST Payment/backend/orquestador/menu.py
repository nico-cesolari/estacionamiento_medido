# orquestador/menu.py
# -----------------------------------------------------------------------------
# El menú principal del panel. Muestra la opción fija de pagos, y agrega
# dinámicamente una opción "Cancelar {lo que esté corriendo}" por cada
# ejecución activa.
#
# El modo "Automático" YA NO se controla desde acá: corre únicamente al
# arrancar el proceso con "python3 -m backend.main --servicio" (lo que usa
# el servicio de launchd). El panel interactivo (init.command) es solo
# para disparar acciones puntuales a mano.
# -----------------------------------------------------------------------------

OPCIONES_BASE = {
    "1": ("pagos", "Actualizar pagos"),
}


class Menu:
    def __init__(self, gestor, consola):
        self.gestor = gestor
        self.consola = consola

    def ejecutar(self):
        while True:
            texto_menu, codigos_cancelar = self._construir_menu()
            self.consola.imprimir(texto_menu)
            opcion = self.consola.preguntar("\nElegí una opción > ").strip().lower()

            if self.consola.consumir_volver_pendiente():
                continue

            if opcion in ("0", "salir"):
                if self._salir():
                    return
            elif opcion in OPCIONES_BASE:
                self._manejar_opcion(opcion)
            elif opcion in codigos_cancelar:
                self._manejar_cancelacion(codigos_cancelar[opcion])
            elif opcion == "":
                continue
            else:
                self.consola.imprimir("❌ Opción inválida. Elegí uno de los números de la lista.")

    # --- construcción del menú -----------------------------------------

    def _construir_menu(self):
        lineas = [
            "",
            "┌──────────────────────────────────────────────────────────┐",
            "│              Panel de Automatización — SIGI              │",
            "└──────────────────────────────────────────────────────────┘",
        ]

        for clave, (tipo, etiqueta) in OPCIONES_BASE.items():
            estado = "  [EN EJECUCIÓN...]" if self.gestor.esta_en_ejecucion(tipo) else ""
            lineas.append(f"  {clave}) {etiqueta}{estado}")

        codigos_cancelar = {}
        atajos_cancelar = []
        contador = 1

        for id_ejec, _tipo, etiqueta, origen in self.gestor.listar_en_ejecucion():
            codigo = f"c{contador}"
            contador += 1
            codigos_cancelar[codigo] = ("ejecucion", id_ejec)
            codigos_cancelar[f"cancelar {_tipo}"] = ("ejecucion", id_ejec)
            atajos_cancelar.append(f"cancelar {_tipo}")
            sufijo = " (disparado por el automático)" if origen == "automático" else ""
            lineas.append(f"  {codigo}) Cancelar {etiqueta}{sufijo}")

        if atajos_cancelar:
            lineas.append(f"     También podés escribir: {' / '.join(atajos_cancelar)}")

        lineas.append("  0) Salir")
        return "\n".join(lineas), codigos_cancelar

    # --- manejo de opciones ----------------------------------------------

    def _manejar_opcion(self, opcion):
        tipo, etiqueta = OPCIONES_BASE[opcion]

        if self.gestor.esta_en_ejecucion(tipo):
            self.consola.imprimir(f"ℹ '{etiqueta}' ya se está ejecutando en este momento.")
            return

        motivo = self.gestor.motivo_bloqueo(tipo)
        if motivo:
            self.consola.imprimir(
                f"❌ No se puede iniciar '{etiqueta}' ahora: {motivo}. "
                "Cancelá el que está corriendo o esperá a que termine."
            )
            return

        id_ejec = self.gestor.iniciar(tipo, origen="manual")
        if id_ejec:
            self.consola.imprimir(f"▶ '{etiqueta}' iniciado en segundo plano. Los pasos van a ir apareciendo acá.")
            self._esperar_proceso_manual(id_ejec, etiqueta)
        else:
            self.consola.imprimir(f"❌ No se pudo iniciar '{etiqueta}' (probá de nuevo en unos segundos).")

    def _esperar_proceso_manual(self, id_ejec: str, etiqueta: str):
        """Mantiene una vista mínima mientras corre un proceso manual.

        Mientras está activo solo ofrece cancelar. Cuando el lector de cola
        detecta que terminó, la consola cambia el prompt a "Volver >".
        Recién después de volver se pinta nuevamente el menú principal.
        """
        while self.gestor.ejecucion_activa(id_ejec):
            respuesta = self.consola.preguntar(f"Cancelar {etiqueta} > ").strip().lower()

            if self.consola.consumir_volver_pendiente():
                return

            if respuesta == "":
                self.gestor.cancelar(id_ejec)
                self.consola.preguntar("Volver > ")
                return

            if respuesta:
                self.consola.imprimir("❌ Preciona Enter para detener el proceso o esperá a que termine.")

        self.consola.preguntar("Volver > ")

    def _manejar_cancelacion(self, objetivo):
        clase, referencia = objetivo
        self.gestor.cancelar(referencia)

    def _salir(self) -> bool:
        hay_activos = bool(self.gestor.listar_en_ejecucion())
        if hay_activos:
            confirmacion = self.consola.preguntar(
                "Hay procesos activos. ¿Cancelar todo y salir? (s/n) > "
            ).strip().lower()
            if confirmacion != "s":
                return False
        self.gestor.cancelar_todos()
        self.consola.imprimir("👋 Cerrando el panel...")
        return True
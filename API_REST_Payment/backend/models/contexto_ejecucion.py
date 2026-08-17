from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable

from backend.models.claves_contexto import ClavesContexto


@dataclass
class ContextoEjecucion:
    navegador: object
    contexto_navegador: object = None

    datos: dict = field(default_factory=dict)
    archivos_creados: list[str] = field(default_factory=list)

    # Se pone en True SOLO cuando algún paso efectivamente sube/envía un
    # archivo a un sistema externo (SEMyT o SIGI).
    archivo_subido: bool = False

    _log_fn: Callable[[str], None] = field(default=lambda _: None, repr=False)
    _archivo_fn: Callable[[str], None] = field(default=lambda _: None, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def guardar(self, clave: str, valor: Any) -> None:
        with self._lock:
            self.datos[clave] = valor

    def obtener(self, clave: str) -> Any:
        with self._lock:
            return self.datos.get(clave)

    def existe(self, clave: str) -> bool:
        with self._lock:
            return clave in self.datos

    def eliminar(self, clave: str) -> None:
        with self._lock:
            self.datos.pop(clave, None)

    def obtener_obligatorio(self, clave: str, paso_requerido: str) -> Any:
        valor = self.obtener(clave)

        if valor is None:
            raise ContextoIncompletoError(
                f"No se encontró '{clave}' en el contexto. "
                f"¿Corrió {paso_requerido} antes?"
            )

        return valor

    @property
    def pagina_semyt(self):
        return self.obtener_obligatorio(ClavesContexto.PAGINA_SEMYT, "LoginSEMyTPaso")

    @property
    def pagina_sigi(self):
        return self.obtener_obligatorio(ClavesContexto.PAGINA_SIGI, "LoginSIGIPaso")

    @property
    def ruta_txt_pagos(self):
        return self.obtener_obligatorio(ClavesContexto.RUTA_TXT_PAGOS, "DescargasParalelasPaso")

    @property
    def ruta_excel_actas_cruce_sigemi(self):
        return self.obtener(ClavesContexto.RUTA_EXCEL_ACTAS_CRUCE_SIGEMI)

    @property
    def ruta_excel_actas_cruce_sigi(self):
        return self.obtener(ClavesContexto.RUTA_EXCEL_ACTAS_CRUCE_SIGI)

    def log(self, mensaje: str) -> None:
        self._log_fn(mensaje)

    def registrar_archivo(self, ruta: str) -> None:
        with self._lock:
            self.archivos_creados.append(ruta)

        self._archivo_fn(ruta)

    def marcar_archivo_subido(self) -> None:
        with self._lock:
            self.archivo_subido = True


class ContextoIncompletoError(Exception):
    """Falta un dato que un paso anterior debía dejar en el contexto."""

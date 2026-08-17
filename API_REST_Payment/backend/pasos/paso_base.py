# pasos/paso_base.py
import time
from abc import ABC, abstractmethod

class PasoBase(ABC):

    def iniciar_paso(self):
        self._inicio = time.perf_counter()
        print("\n" + "=" * 50)
        print(f"🚀 PASO: {self.__class__.__name__}")
        print("=" * 50)

    def finalizar_paso(self):
        duracion = time.perf_counter() - getattr(self, "_inicio", time.perf_counter())
        print(f"✅ {self.__class__.__name__} finalizado. (⏱ {duracion:.1f}s)\n")

    @abstractmethod
    async def ejecutar(self, contexto):
        pass
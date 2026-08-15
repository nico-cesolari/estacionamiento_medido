from dataclasses import dataclass


@dataclass(frozen=True)
class Credenciales:
    usuario: str
    contrasena: str

    def __repr__(self) -> str:
        return f"Credenciales(usuario='{self.usuario}', contrasena='******')"

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict


@dataclass
class Producto:
    """Formato común de un producto, venga del supermercado que venga."""
    supermercado: str
    sku_id: str
    ean: str | None
    nombre: str
    marca: str | None
    categoria: str | None          # la más específica, ej: "/Lácteos/Leches/Leches Larga Vida/"
    precio: float                  # precio final de góndola
    precio_sin_descuento: float    # si es mayor que precio, hay descuento
    disponible: bool
    contenido: float | None        # cantidad en unidad base (ml, g o un)
    unidad: str | None             # "ml", "g" o "un"
    precio_unitario: float | None  # $ por litro, por kilo o por unidad
    link: str | None
    venta_por_peso: bool = False   # True: se compra la cantidad exacta y precio es por kilo
    

    def to_dict(self) -> dict:
        return asdict(self)


class SupermercadoAdapter(ABC):
    """Interfaz común para cualquier supermercado, sin importar su plataforma."""

    def __init__(self, nombre: str):
        self.nombre = nombre

    @abstractmethod
    def buscar_crudo(self, texto: str, limite: int = 3) -> dict:
        """Request sin procesar: {"status", "url", "total", "productos"}."""
        ...

    @abstractmethod
    def buscar(self, texto: str, limite: int = 10) -> list[Producto]:
        """Búsqueda normalizada: lista de Producto."""
        ...
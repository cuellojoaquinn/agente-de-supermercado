"""Cache en memoria de las búsquedas.

buscar_productos guarda acá TODOS los resultados normalizados; optimizar_compra los lee.
Así los precios nunca pasan por el modelo: el modelo solo maneja el texto de búsqueda
como referencia (mismo patrón que las "variables simbólicas" del Dual LLM).

Limitación: la cache es del proceso, la comparten todas las conversaciones. Para el TP
alcanza; en producción iría en el estado del agente o en una base por sesión.
"""
from adapters.base import Producto

_busquedas: dict[str, list[Producto]] = {}


def _clave(texto: str) -> str:
    # "Leche  Entera 1 Litro" y "leche entera 1 litro" son la misma búsqueda
    return " ".join(texto.lower().split())


def guardar_busqueda(texto: str, productos: list[Producto], consultados: set[str]) -> None:
    """Fusiona con lo que ya había: reemplaza solo los productos de los supermercados
    consultados ahora y conserva los de los demás. Así una búsqueda filtrada
    (ej: solo Carrefour) no borra los resultados de una búsqueda anterior en todos."""
    previos = [p for p in _busquedas.get(_clave(texto), []) if p.supermercado not in consultados]
    _busquedas[_clave(texto)] = previos + productos


def obtener_busqueda(texto: str) -> list[Producto] | None:
    return _busquedas.get(_clave(texto))

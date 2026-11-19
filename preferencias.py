"""Preferencias del usuario: de esta conversación (estado del agente) o para siempre (Store).

- Conversación: viven en el estado del agente (checkpoints.db) y se pierden al empezar otra.
- Largo plazo: viven en un Store de LangGraph (memoria.db), compartido por todas las
  conversaciones. La app no tiene usuarios, así que hay un único espacio ("usuario_local").

Las tools aplican las preferencias con reglas fijas sobre los datos del producto (marca),
así el resultado no depende de que el modelo "se acuerde".
"""
import re
import sqlite3
import unicodedata
from typing import Literal

from langgraph.store.sqlite import SqliteStore

TipoPreferencia = Literal["excluir_marca", "preferir_marca", "excluir_marcas_propias", "nota"]
NAMESPACE = ("usuario_local", "preferencias")

# check_same_thread=False: Streamlit atiende cada rerun en un hilo distinto
# isolation_level=None (autocommit): el Store abre y cierra sus propias transacciones
store = SqliteStore(sqlite3.connect("memoria.db", check_same_thread=False, isolation_level=None))
store.setup()  # crea las tablas si la base es nueva


def _normalizar(texto: str | None) -> str:
    """Minúsculas, sin tildes ni signos: 'La Serenísima' -> 'la serenisima'."""
    texto = unicodedata.normalize("NFKD", (texto or "").lower()).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", texto))


def nueva(tipo: TipoPreferencia, valor: str | None = None) -> dict:
    valor = (valor or "").strip() or None
    return {"clave": f"{tipo}:{_normalizar(valor)}", "tipo": tipo, "valor": valor}


def etiqueta(pref: dict) -> str:
    """Texto corto para mostrar en la UI y en el prompt."""
    return {
        "excluir_marca": f"Sin {pref['valor']}",
        "preferir_marca": f"Prefiere {pref['valor']}",
        "excluir_marcas_propias": "Sin marcas propias del súper",
    }.get(pref["tipo"], pref["valor"] or "")


# ---------------------------------------------------------------- largo plazo (Store)

def guardadas(store_=None) -> list[dict]:
    return [item.value for item in (store_ or store).search(NAMESPACE, limit=100)]


def guardar(pref: dict, store_=None):
    (store_ or store).put(NAMESPACE, pref["clave"], pref)


def borrar(clave: str, store_=None):
    (store_ or store).delete(NAMESPACE, clave)


# ---------------------------------------------------------------- uso combinado

def efectivas(estado: dict | None, store_=None) -> list[dict]:
    """Las de largo plazo + las de esta conversación, sin repetidas."""
    todas = {p["clave"]: {**p, "alcance": "siempre"} for p in guardadas(store_)}
    de_la_conversacion = (estado or {}).get("preferencias") or {}
    if isinstance(de_la_conversacion, dict):   # {clave: pref}; conversaciones viejas: lista
        de_la_conversacion = list(de_la_conversacion.values())
    for p in de_la_conversacion:
        todas.setdefault(p["clave"], {**p, "alcance": "conversacion"})
    return list(todas.values())


def _es_marca_propia(producto) -> bool:
    """Heurística: la marca contiene el nombre del súper ('Día' en Día, 'Carrefour' en Carrefour).
    No detecta marcas propias con otro nombre."""
    return bool(producto.marca) and _normalizar(producto.supermercado) in _normalizar(producto.marca)


def _coincide(producto, marca: str) -> bool:
    buscada = _normalizar(marca)
    return bool(buscada) and buscada in _normalizar(f"{producto.marca} {producto.nombre}")


def aplicar(productos: list, prefs: list[dict]) -> tuple[list, int]:
    """Filtra según las preferencias y pone primero las marcas preferidas (sin cambiar el
    orden por precio dentro de cada grupo). Devuelve (productos, cantidad descartada)."""
    excluir = [p["valor"] for p in prefs if p["tipo"] == "excluir_marca"]
    preferir = [p["valor"] for p in prefs if p["tipo"] == "preferir_marca"]
    sin_propias = any(p["tipo"] == "excluir_marcas_propias" for p in prefs)

    quedan = [prod for prod in productos
              if not any(_coincide(prod, m) for m in excluir)
              and not (sin_propias and _es_marca_propia(prod))]
    quedan.sort(key=lambda prod: not any(_coincide(prod, m) for m in preferir))  # sort estable
    return quedan, len(productos) - len(quedan)


def es_preferida(producto, prefs: list[dict]) -> bool:
    return any(_coincide(producto, p["valor"]) for p in prefs if p["tipo"] == "preferir_marca")

"""Compara la Legacy Search API (la que usamos hoy) contra Intelligent Search con
fuzzy=0&operator=and, para ver si la segunda trae menos ruido.

Corre desde la raíz:  python -m exploracion.probar_intelligent_search
"""
import time
from urllib.parse import quote
import requests
from supermercados import SUPERMERCADOS
from tools import _es_relevante

BUSQUEDAS = ["yerba mate 1 kg", "leche entera 1 litro", "aceite de girasol 1.5 litros", "galletitas de agua"]
CANTIDAD = 20
IS_PATH = "/api/io/_v/api/intelligent-search/product_search/"


def legacy(adapter, texto: str) -> list[str]:
    # uso el adapter: VTEX legacy rechaza "+" como espacio (400), exige %20
    return [p["productName"] for p in adapter.buscar_crudo(texto, CANTIDAD)["productos"]]


def sin_presentacion(texto: str) -> str:
    """Saca números y unidades: con operator=and, "litro" no matchea "1L"."""
    from tools import _IGNORAR, _palabras
    return " ".join(w for w in _palabras(texto) if w not in _IGNORAR and not w.isdigit())


def intelligent(adapter, texto: str) -> list[str]:
    base_url = adapter.base_url
    r = requests.get(f"{base_url}{IS_PATH}",
                     params={"query": texto, "fuzzy": "0", "operator": "and",
                             "count": CANTIDAD, "page": 1, "locale": "es-AR"},
                     timeout=10)
    r.raise_for_status()
    datos = r.json()
    if "products" not in datos:
        raise RuntimeError(f"respuesta sin 'products': {str(datos)[:150]}")
    return [p["productName"] for p in datos["products"]]


def resumen(nombres: list[str], texto: str) -> str:
    ruido = [n for n in nombres if not _es_relevante(n, texto)]
    return f"{len(nombres):>2} resultados, {len(ruido):>2} ruido", ruido


for texto in BUSQUEDAS:
    print(f"\n=== '{texto}' ===", flush=True)
    for adapter in SUPERMERCADOS.values():
        variantes = (("legacy      ", legacy, texto), ("IS completo ", intelligent, texto),
                     ("IS sin pres.", intelligent, sin_presentacion(texto)))
        for etiqueta, fn, q in variantes:
            try:
                t0 = time.time()
                nombres = fn(adapter, q)
                txt, ruido = resumen(nombres, texto)
                print(f"  {adapter.nombre:<10} {etiqueta} {txt}  ({time.time() - t0:.1f}s)", flush=True)
                for n in ruido[:3]:
                    print(f"      ruido: {n}", flush=True)
            except Exception as e:
                print(f"  {adapter.nombre:<10} {etiqueta} ❌ {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(0.5)

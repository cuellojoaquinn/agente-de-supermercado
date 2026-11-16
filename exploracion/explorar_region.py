import base64
import json
import time
from urllib.parse import urlencode, quote
import requests

BASE = "https://www.vea.com.ar"
SEARCH = f"{BASE}/api/catalog_system/pub/products/search"
TEXTO = "leche entera"
CP_MENDOZA = "5500"
SC = "34"


def buscar(extra_params=None, cookies=None):
    params = {"ft": TEXTO, "_from": 0, "_to": 2, **(extra_params or {})}
    url = f"{SEARCH}?{urlencode(params, quote_via=quote)}"
    r = requests.get(url, cookies=cookies, timeout=15)
    r.raise_for_status()
    return r.json()


def resumen(productos, titulo):
    """Muestra seller y precio de los primeros productos: es lo que queremos ver cambiar."""
    print(f"\n--- {titulo} ---")
    for p in productos:
        seller = p["items"][0]["sellers"][0]
        precio = seller["commertialOffer"]["Price"]
        print(f"  {p['productName'][:45]:45} | seller={seller.get('sellerId')} "
              f"({seller.get('sellerName')}) | ${precio}")


# A) Línea base: lo que recibimos hoy (esperamos el seller de Córdoba)
resumen(buscar(), "A) Sin región (línea base)")
time.sleep(1.5)

# B) Pedir la región de Mendoza por código postal
r = requests.get(
    f"{BASE}/api/checkout/pub/regions",
    params={"country": "ARG", "postalCode": CP_MENDOZA, "sc": SC},
    timeout=15,
)
print(f"\n--- B) Regiones para CP {CP_MENDOZA} --- status {r.status_code}")
print(json.dumps(r.json(), indent=2, ensure_ascii=False)[:1500] if r.ok else r.text[:300])
time.sleep(1.5)

# C) Si B devolvió un regionId, armamos la cookie vtex_segment y repetimos la búsqueda
region_id = r.json()[0].get("id") if r.ok and r.json() else None
if region_id:
    segmento = {"channel": SC, "regionId": region_id}
    cookie = base64.b64encode(json.dumps(segmento).encode()).decode()
    resumen(buscar(cookies={"vtex_segment": cookie}), f"C) Con vtex_segment (regionId={region_id[:30]}...)")
else:
    print("\nC) Sin regionId: no se puede probar la cookie")
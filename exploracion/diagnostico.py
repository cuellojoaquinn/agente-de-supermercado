import time
from urllib.parse import quote
import requests

BASE = "https://www.vea.com.ar/api/catalog_system/pub/products/search"

pruebas = {
    "1) 'leche' sin O (la que funcionó al principio)": f"{BASE}?ft=leche&_from=0&_to=2",
    "2) 'leche' con O":                               f"{BASE}?ft=leche&_from=0&_to=2&O=OrderByPriceASC",
    "3) 'leche entera' con %20, sin O":               f"{BASE}?ft={quote('leche entera')}&_from=0&_to=2",
    "4) 'leche entera' con +, sin O":                 f"{BASE}?ft=leche+entera&_from=0&_to=2",
    "5) 'leche entera' en el path":                   f"{BASE}/{quote('leche entera')}?_from=0&_to=2",
}

for nombre, url in pruebas.items():
    r = requests.get(url, timeout=10)
    resultado = r.headers.get("resources") if r.ok else r.text[:80]
    print(f"{nombre}\n   {r.status_code} | {resultado}\n")
    time.sleep(2)  # pausa para no gatillar un bloqueo
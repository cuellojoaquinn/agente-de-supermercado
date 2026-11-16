"""
Limitación conocida: promociones. La API pública de catálogo de Vea (VTEX) informa el precio de lista sin promociones aplicadas. Ejemplo: Oblea KitKat 41,5 g figura a $2.890 en la API, mientras la web muestra $2.023 (-30%). También probamos la simulación de carrito de VTEX (/api/checkout/pub/orderForms/simulation) con 1 y 2 unidades, y tampoco aplicó el descuento. Inferimos que las promociones dependen del contexto de sesión del navegador (región, canal de venta, medio de pago o usuario), que no logramos reproducir desde la API. Impacto: el agente compara precios regulares, así que el ranking puede diferir del precio final en caja. Los descuentos directos sí se detectan cuando la API los informa (Price < ListPrice / PriceWithoutDiscount). Trabajo futuro: replicar el contexto de sesión de la web o leer los teasers de promoción para al menos señalarlas.
"""

import json
from urllib.parse import urlencode, quote
import requests

BASE = "https://www.vea.com.ar"

# 1. Buscar la oblea KitKat para obtener su itemId (SKU)
q = urlencode({"ft": "oblea kitkat", "_from": 0, "_to": 0}, quote_via=quote)
producto = requests.get(f"{BASE}/api/catalog_system/pub/products/search?{q}", timeout=15).json()[0]
sku = producto["items"][0]["itemId"]
print(f"Producto: {producto['productName']} | SKU {sku}")
print("Clusters con promo:", [c for c in producto.get("clusterHighlights", {}).values() if "%" in c or "x" in c])
oferta = producto["items"][0]["sellers"][0]["commertialOffer"]
print(f"Catálogo → Price: {oferta['Price']} | PriceWithoutDiscount: {oferta['PriceWithoutDiscount']}")

# 2. Simular el carrito con 1 y con 2 unidades
for cantidad in [1, 2]:
    r = requests.post(
        f"{BASE}/api/checkout/pub/orderForms/simulation?sc=34",
        json={"items": [{"id": sku, "quantity": cantidad, "seller": "1"}], "country": "ARG"},
        timeout=15,
    )
    print(f"\n===== {cantidad} unidad(es) | status {r.status_code} =====")
    if not r.ok:
        print(r.text[:300])
        continue
    data = r.json()
    for item in data.get("items", []):
        print("price:", item.get("price"), "| listPrice:", item.get("listPrice"),
              "| sellingPrice:", item.get("sellingPrice"))
        print("priceTags:", json.dumps(item.get("priceTags"), ensure_ascii=False)[:600])
    beneficios = data.get("ratesAndBenefitsData", {}).get("rateAndBenefitsIdentifiers", [])
    print("Promos aplicadas:", [b.get("name") for b in beneficios])
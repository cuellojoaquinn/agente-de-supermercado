import json
from urllib.parse import urlencode, quote
import requests

URL = "https://www.vea.com.ar/api/catalog_system/pub/products/search"

for texto in ["leche", "yerba mate", "aceite girasol"]:
    params = {"ft": texto, "_from": 0, "_to": 9, "O": "OrderByBestDiscountDESC"}
    r = requests.get(f"{URL}?{urlencode(params, quote_via=quote)}", timeout=15)
    r.raise_for_status()

    print(f"\n===== '{texto}' (ordenado por mayor descuento) =====")
    for p in r.json():
        item = p["items"][0]
        oferta = item["sellers"][0]["commertialOffer"]
        sku_data = json.loads(p["SkuData"][0]).get(item["itemId"], {}) if p.get("SkuData") else {}

        precio, sin_desc = oferta["Price"], oferta["PriceWithoutDiscount"]
        hay_algo = (precio < sin_desc or oferta.get("Teasers") or oferta.get("PromotionTeasers")
                    or oferta.get("DiscountHighLight") or sku_data.get("promotions"))
        if not hay_algo:
            continue

        print(f"\n- {p['productName']} | Price={precio} | PriceWithoutDiscount={sin_desc}")
        print("  DiscountHighLight:", json.dumps(oferta.get("DiscountHighLight"), ensure_ascii=False)[:400])
        print("  Teasers:", json.dumps(oferta.get("Teasers"), ensure_ascii=False)[:600])
        print("  PromotionTeasers:", json.dumps(oferta.get("PromotionTeasers"), ensure_ascii=False)[:600])
        print("  SkuData.promotions:", json.dumps(sku_data.get("promotions"), ensure_ascii=False)[:600])
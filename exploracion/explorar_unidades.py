import json
import time
from collections import defaultdict
from supermercados import SUPERMERCADOS

# Productos variados para cubrir líquidos, sólidos, por peso y por unidad
BUSQUEDAS = [
    "leche entera", "aceite girasol", "gaseosa", "agua mineral",  # líquidos
    "yerba mate", "harina", "arroz", "azucar",                    # sólidos envasados
    "queso cremoso", "banana", "carne picada",                    # venta por peso
    "huevos", "detergente", "papel higienico",                    # por unidad / packs
]

adapter = SUPERMERCADOS["vea"]

# (unidad_un, unidad_base) → lista de ejemplos
hallazgos = defaultdict(list)

for texto in BUSQUEDAS:
    try:
        crudo = adapter.buscar_crudo(texto, limite=10)
    except Exception as e:
        print(f"Error buscando '{texto}': {e}")
        continue

    for p in crudo["productos"]:
        try:
            item_id = p["items"][0]["itemId"]
            sku_data = json.loads(p["SkuData"][0]).get(item_id, {})
        except Exception:
            hallazgos[("SIN SkuData", "-")].append(p.get("productName"))
            continue

        clave = (
            sku_data.get("measurement_unit_un"),  # ej: "cc."
            sku_data.get("measurement_unit"),     # ej: "un" (o "kg" para venta por peso?)
        )
        precio = p["items"][0]["sellers"][0]["commertialOffer"].get("Price")
        hallazgos[clave].append(
            f"{p['productName']} | unit_multiplier_un={sku_data.get('unit_multiplier_un')} "
            f"| unit_multiplier={sku_data.get('unit_multiplier')} | ${precio}"
        )

    time.sleep(1.5)  # pausa entre búsquedas para no gatillar bloqueos

# Resumen: cada combinación de unidades con hasta 3 ejemplos
print("\n===== UNIDADES ENCONTRADAS =====")
for (unidad_un, unidad), ejemplos in sorted(hallazgos.items(), key=lambda x: -len(x[1])):
    print(f"\nmeasurement_unit_un={unidad_un!r} | measurement_unit={unidad!r} → {len(ejemplos)} productos")
    for ej in ejemplos[:3]:
        print(f"   - {ej}")
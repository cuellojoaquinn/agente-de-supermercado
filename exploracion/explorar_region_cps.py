"""
Limitación conocida: región de precios. La API pública de catálogo de Vea (VTEX) devuelve precios del canal de venta 34 asociados a la sucursal jumboargentinav700cordoba700 (Córdoba), verificado decodificando el PriceToken (JWT) de la respuesta. Consultamos el endpoint de regiones de VTEX con 8 códigos postales de Mendoza (Capital, Godoy Cruz, Luján, Maipú, Guaymallén, Las Heras, San Martín y San Rafael): todos devuelven el mismo conjunto de 15 sucursales del país, ninguna en Mendoza. Los precios mostrados pueden diferir de los precios en góndola en Mendoza.
"""

import base64
import time
import requests

BASE = "https://www.vea.com.ar"
SC = "34"

# Códigos postales de Mendoza (verificá contra la lista de sucursales de Vea)
CPS = {
    "Capital": "5500",
    "Godoy Cruz": "5501",
    "Luján de Cuyo": "5507",
    "Maipú": "5515",
    "Guaymallén": "5519",
    "Las Heras": "5539",
    "San Martín": "5570",
    "San Rafael": "5600",
}


def decodificar_region(region_id: str) -> list[str]:
    """El regionId es base64 de 'SW#seller1;seller2;...'"""
    texto = base64.b64decode(region_id).decode()
    return texto.split("#", 1)[-1].split(";")


referencia = None
for zona, cp in CPS.items():
    r = requests.get(
        f"{BASE}/api/checkout/pub/regions",
        params={"country": "ARG", "postalCode": cp, "sc": SC},
        timeout=15,
    )
    if not r.ok or not r.json():
        print(f"{zona:15} ({cp}): sin región | status {r.status_code}")
        time.sleep(1)
        continue

    sellers = decodificar_region(r.json()[0]["id"])
    if referencia is None:
        referencia = set(sellers)

    mendoza = [s for s in sellers if "mendoza" in s or "mza" in s]
    distinta = "IGUAL a 5500" if set(sellers) == referencia else "⚠️ DISTINTA"
    print(f"{zona:15} ({cp}): {len(sellers)} sucursales | {distinta} | Mendoza: {mendoza or 'ninguna'}")
    time.sleep(1)
"""
Script de exploración para ver si la API responde y qué devuelve
"""

import json
from supermercados import SUPERMERCADOS

# La búsqueda fulltext encuentra la palabra en cualquier parte del nombre. Entonces el modelo va a tener que buscar con términos más específicos o o filtrar por categoría y conviene que la tool devuelva suficiente información (nombre y categoría) para que el modelo descarte lo que no corresponde.

TEXTO = "leche entera"

# En el servicio los clusters son ruido y no sirven para saber si el producto tiene descuento hay que descartarlos en la normalización. Son colecciones de marketing: campañas viejas, colecciones de prueba y nombres de promos genéricas

RUIDO_OFERTA = {"Installments", "DeliverySlaSamplesPerRegion", "DeliverySlaSamples"}
RUIDO_PRODUCTO = {"productClusters", "clusterHighlights", "searchableClusters", "items"}
# CUIDADO: Catálogo compartido. Aparecen “Jumbo Ofertas” y “Disco | Fin de Semana…”. Eso sugiere que Vea comparte catálogo con Jumbo y Disco (todos de Cencosud). Si es así, el mismo VTEXAdapter serviría para los tres. Lo verificamos más adelante.

for clave, adapter in SUPERMERCADOS.items():
    print(f"\n===== {adapter.nombre} =====")
    try:
        res = adapter.buscar_crudo(TEXTO, limite=5)
        producto = res["productos"][0]

        # 1. Campos del producto (buscamos especificaciones como "Contenido")
        print("--- Campos del producto ---")
        resumen = {k: v for k, v in producto.items() if k not in RUIDO_PRODUCTO}
        print(json.dumps(resumen, indent=2, ensure_ascii=False)[:3000])

        # 2. Oferta comercial sin cuotas ni envíos
        oferta = producto["items"][0]["sellers"][0]["commertialOffer"]
        print("\n--- commertialOffer (sin ruido) ---")
        print(json.dumps({k: v for k, v in oferta.items() if k not in RUIDO_OFERTA},
                         indent=2, ensure_ascii=False)[:3000])

    except Exception as e:
        print(f"Error en {adapter.nombre}: {e}")
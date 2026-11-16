import json
import time
from tools import buscar_productos, optimizar_compra

# 1. Primero buscamos cada producto, como haría el agente (esto llena la cache)
busquedas = ["leche entera 1 litro", "yerba mate 1 kg", "aceite girasol 1,5 litros"]
for texto in busquedas:
    t0 = time.time()
    r = buscar_productos.invoke({"producto": texto, "cantidad_por_super": 1})
    print(f"🔎 {texto}: {len(r['por_supermercado'])} súper con resultados ({time.time() - t0:.1f}s)")

# 2. Optimizamos la lista: el modelo solo pasa búsqueda, cantidad y unidad
print("\n===== OPTIMIZACIÓN =====")
t0 = time.time()
resultado = optimizar_compra.invoke({"items": [
    {"busqueda": "leche entera 1 litro", "cantidad": 2, "unidad": "L"},
    {"busqueda": "yerba mate 1 kg", "cantidad": 1, "unidad": "kg"},
    {"busqueda": "aceite girasol 1,5 litros", "cantidad": 1.5, "unidad": "L"},
]})
print(f"(tardó {time.time() - t0:.2f}s, sin requests: usa la cache)\n")
print(json.dumps(resultado, indent=2, ensure_ascii=False))

caracteres = len(json.dumps(resultado, ensure_ascii=False))
print(f"\nTamaño del resultado: {caracteres} caracteres (~{caracteres // 4} tokens)")
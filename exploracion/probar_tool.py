"""
El orden por precio unitario es correcto: la leche de 200cc ($6.950/L) quedó afuera del top 5, que es justo lo que queríamos.

La categoría distingue tipos: “Leches Refrigeradas” y “Larga Vida”. Si el usuario pide una de las dos, el modelo puede filtrar.

total_encontrados: 16 de los 20 pedidos: los otros 4 se descartaron por no estar disponibles.
"""

import json
from tools import buscar_productos

# 1. Lo que ve el modelo: nombre, descripción y schema de argumentos
print("===== LO QUE VE EL MODELO =====")
print("Nombre:", buscar_productos.name)
print("Descripcióa:", buscar_productos.description[:200], "...")
print("Argumentos:", json.dumps(buscar_productos.args, indent=2, ensure_ascii=False))

# 2. Una ejecución real, igual a la que haría el agente
print("\n===== EJECUCIÓN =====")
resultado = buscar_productos.invoke({"producto": "leche entera", "limite": 5})
print(json.dumps(resultado, indent=2, ensure_ascii=False))

import json
import time
from tools import buscar_productos

# 1. Lo que ve el modelo: el schema completo que LangChain envía en cada request
print("===== LO QUE VE EL MODELO =====")
schema = buscar_productos.tool_call_schema.model_json_schema()
print(json.dumps(schema, indent=2, ensure_ascii=False))

# 2. Una ejecución real, igual a la que haría el agente
print("\n===== EJECUCIÓN =====")
t0 = time.time()
resultado = buscar_productos.invoke({"producto": "leche entera 1 litro", "cantidad_por_super": 2})
print(f"(tardó {time.time() - t0:.1f}s)\n")
print(json.dumps(resultado, indent=2, ensure_ascii=False))

# 3. Tamaño aproximado de lo que entra al contexto del modelo
caracteres = len(json.dumps(resultado, ensure_ascii=False))
print(f"\nTamaño del resultado: {caracteres} caracteres (~{caracteres // 4} tokens)")
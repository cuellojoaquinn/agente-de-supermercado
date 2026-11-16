# Agente optimizador de compras de supermercado — Resumen de avance

**Trabajo Integrador · Modelos de Lenguaje · UTN FRM** — estado al 13/11/2026

---

## 1. Fuente de datos

- **Elegida:** API de **Vea** — Legacy Search API de VTEX (pública y documentada).
- **Endpoint:** `GET https://www.vea.com.ar/api/catalog_system/pub/products/search?ft=...&_from=...&_to=...`
- **Descartadas:** SEPA (zips diarios de ~4 GB, sin API) y la API no oficial de Precios Claros.

## 2. Lo que descubrimos de la API (con evidencia)

| Hallazgo | Cómo lo verificamos | Qué hicimos |
|---|---|---|
| Los espacios codificados como `+` dan **400 "Scripts are not allowed!"** | `diagnostico2.py`: `%20` funciona y `+` falla | Query armada con `urlencode(..., quote_via=quote)` |
| Paginación: respuesta **206** con header `resources: 0-4/446`; máximo 50 por request | Salida real + documentación | Tope `MAX_POR_PAGINA = 50` |
| `OrderByPriceASC` trae primero **las presentaciones más chicas** (200cc, 120ml, 75g) | `explorar_unidades.py` | Orden por relevancia por defecto; ordenamos nosotros por precio unitario |
| La búsqueda fulltext trae productos no relacionados ("leche" → oblea KitKat) | Primera exploración | El campo `categoria` va en la respuesta y el system prompt pide descartar los que no correspondan |
| La presentación está en **`SkuData`** (string con JSON adentro): `measurement_unit_un` + `unit_multiplier_un` | Salida del producto completo | `json.loads` y conversión a ml / g / un |
| Unidades inconsistentes: `gr.`, `ml.`, `cc.`, `KG`, `un`, `m2`, `mt.` | `explorar_unidades.py` (14 búsquedas) | Normalización (minúsculas, sin punto) + diccionario `UNIDADES` |
| **Venta por peso** (`measurement_unit = "kg"`): `Price` es **por kilo** | Web del Queso Cremoso Barraza: "Precio regular x kg.: $11.500" | Caso 1 en `_presentacion` |
| SkuData dice "1 un" pero el nombre dice "500 Grs": `Price` es **por paquete** | Web de la Carne Picada de Cerdo: "Precio regular x un: $5.990" | Si SkuData dice "1 un", se usa el nombre (regex) |
| Algunos productos no tienen `SkuData` (aceites) | `explorar_unidades.py` | Respaldo con regex sobre el nombre |
| `ListPrice` no es confiable ($114.876 para una leche de $1.390) | Salida del `commertialOffer` | Usamos `Price` contra `PriceWithoutDiscount` |
| `AvailableQuantity: 99999` es relleno, no stock real | Salida real | Solo usamos `IsAvailable` y `Price > 0` |
| Papel higiénico: metros por rollo o totales, y además `m2` | `explorar_unidades.py` | Comparar solo dentro de la misma unidad (limitación) |
| Ruido: clusters de marketing, cuotas, `PriceToken` | Salidas reales | Se excluyen al normalizar |

## 3. Región: limitación documentada

- Los precios corresponden a la sucursal **`jumboargentinav700cordoba700`** (canal de venta 34), según el `PriceToken` (JWT).
- Se probó el endpoint de regiones con **8 códigos postales de Mendoza** (Capital, Godoy Cruz, Luján de Cuyo, Maipú, Guaymallén, Las Heras, San Martín, San Rafael): todos devuelven las mismas 15 sucursales del país, ninguna en Mendoza.
- La cookie `vtex_segment` con ese `regionId` no cambió el seller.
- **Decisión:** no invertir más tiempo y documentarlo como limitación conocida.

## 4. Código que funciona

```
super-agente/
├── adapters/
│   ├── __init__.py
│   ├── base.py          # Producto (dataclass) + SupermercadoAdapter (interfaz)
│   └── vtex.py          # VTEXAdapter: buscar_crudo, buscar, _normalizar, _presentacion, _precio_unitario
├── supermercados.py     # registro: {"vea": VTEXAdapter("Vea", "https://www.vea.com.ar")}
├── tools.py             # buscar_productos ✅ probada
├── probar_tool.py
└── explorar*.py, diagnostico*.py   # scripts de exploración (evidencia)
```

- **Patrón Adapter:** cada supermercado se integra implementando `buscar()`, que devuelve `Producto`. Si Jumbo o Disco comparten la plataforma VTEX (lo sugieren los clusters del catálogo), se agregan con una línea en `supermercados.py`.
- **Lógica de presentación (`_presentacion`):**
  1. Venta por peso (`measurement_unit = "kg"`) → 1000 g, precio por kilo.
  2. Presentación estructurada en `SkuData` → cantidad × factor de `UNIDADES`. Si dice "1 un" y el nombre trae peso o volumen, se pasa al caso 3.
  3. Respaldo: regex sobre el nombre (`120 Ml`, `500 Grs`, `1 Kg`, `200cc`).
  4. Si nada funciona, se deja sin presentación y se registra un aviso.
- **Tool `buscar_productos`:** consulta todos los supermercados, trae 20 por súper, filtra los disponibles, ordena por precio unitario y devuelve como máximo 10 resultados con campos reducidos (cuidado del contexto). Si un súper falla, los demás siguen.
  - Resultado real: leches de 1 L ordenadas por $/L; la de 200cc ($6.950/L) quedó fuera del top 5. ✅
  - **Pendiente:** agregar `@tool(parse_docstring=True)` para que los argumentos lleguen con descripción al schema que ve el modelo.

## 5. Escrito pero todavía sin probar

- `.env` (`GOOGLE_API_KEY`, `MODELO`)
- `model.py`: Gemini con `max_retries=5` (429 del tier gratuito) e `include_thoughts=True` (razonamiento para la UI).
- `agente.py`: `create_agent` + system prompt + `InMemorySaver`.
- `probar_agente.py`: flujo por consola con `stream_mode="updates"` (razonamiento, tool calls, resultados y respuesta final).

## 6. Pendientes

| Tarea | Requisito de la consigna |
|---|---|
| Probar el agente con la primera tool | 3.1 |
| Tools: `optimizar_compra` (propia) y `generar_plan_compra` (archivo, resultado tangible) | 3.2 y caso de uso |
| Formato de las **promociones** (todavía no vimos un producto con descuento) | Skill `promociones.md` |
| Skills (`precio_unitario.md`, `promociones.md`) + tool `load_skill` | 3.6 (b) |
| UI en Streamlit con streaming (`@st.cache_resource` para no perder la memoria) | 3.3, 3.4 y 3.5 |
| Verificar si el modelo devuelve razonamiento; si no, documentarlo en el README | 3.5 |
| README, `.env.example`, `requirements.txt` | Entregables |
| Presentación: diagrama de flujo, limitaciones y justificación de componentes | Entregables y criterio 7 |

## 7. Limitaciones para la presentación

1. **Región:** precios de la sucursal de Córdoba, no de Mendoza.
2. **Datos del catálogo inconsistentes:** papel higiénico (metros por rollo o totales, `m2`) y productos sin `SkuData`.
3. **Relevancia de la búsqueda:** depende de términos específicos y del filtro por categoría que hace el modelo.
4. **Latencia:** cada búsqueda tarda varios segundos, así que la demo tiene que usar pocas llamadas.

### Texto sugerido para la limitación de región

> **Limitación conocida: región de precios.** La API pública de catálogo de Vea (VTEX) devuelve precios del canal de venta 34 asociados a la sucursal `jumboargentinav700cordoba700` (Córdoba), verificado decodificando el `PriceToken` (JWT) de la respuesta. Consultamos el endpoint de regiones de VTEX con 8 códigos postales de Mendoza (Capital, Godoy Cruz, Luján, Maipú, Guaymallén, Las Heras, San Martín y San Rafael): todos devuelven el mismo conjunto de 15 sucursales del país, ninguna en Mendoza. Los precios mostrados pueden diferir de los precios en góndola en Mendoza.

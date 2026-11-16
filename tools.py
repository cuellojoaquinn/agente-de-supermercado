import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from langchain.tools import tool
from pydantic import BaseModel, Field
from cache import guardar_busqueda, obtener_busqueda
from supermercados import SUPERMERCADOS

RESULTADOS_POR_SUPER = 20   # cuántos traemos de cada API para después ordenar nosotros
MAX_POR_SUPER = 5           # tope de lo que devolvemos por súper (cuidado del contexto)
ETIQUETA_UNIDAD = {"ml": "$/L", "g": "$/kg", "un": "$/un", "m": "$/m", "m2": "$/m2"}


def _formatear(prod) -> dict:
    """Solo los campos que el modelo necesita, para no llenar el contexto.
    El supermercado no va acá porque los resultados ya vienen agrupados por súper."""
    return {
        "sku_id": prod.sku_id,
        "nombre": prod.nombre,
        "marca": prod.marca,
        "categoria": prod.categoria,
        "precio": prod.precio,
        "precio_unitario": prod.precio_unitario,
        "unidad_precio": ETIQUETA_UNIDAD.get(prod.unidad),
        "con_descuento": prod.precio < prod.precio_sin_descuento,
    }


def _buscar_en(adapter, producto: str):
    """Busca en un supermercado. Devuelve (nombre, productos, error)."""
    try:
        return adapter.nombre, adapter.buscar(producto, limite=RESULTADOS_POR_SUPER), None
    except Exception as e:
        return adapter.nombre, [], f"{type(e).__name__}: {str(e)[:150]}"


@tool(parse_docstring=True)
def buscar_productos(producto: str, cantidad_por_super: int = 3,
                     supermercados: list[str] | None = None) -> dict:
    """Busca un producto en los supermercados disponibles (todos, o solo los indicados)
    y devuelve, para cada supermercado, las opciones más convenientes ordenadas por
    precio unitario ($/L, $/kg o $/un). Sirve para comparar precios entre supermercados.

    Si el usuario pide un supermercado puntual, pasalo en `supermercados` en vez de
    buscar en todos y filtrar después.

    Usá términos específicos que incluyan tipo y, si importa, presentación
    (ej: "leche entera 1 litro", "yerba mate 1 kg"). Una búsqueda genérica
    como "leche" puede traer productos no deseados (ej: chocolates con leche):
    revisá la categoría de cada resultado y descartá los que no correspondan.

    Args:
        producto: texto a buscar, lo más específico posible.
        cantidad_por_super: cantidad de opciones a devolver por supermercado (1 a 5).
        supermercados: opcional, limita la búsqueda a estos supermercados. Valores
            válidos: "vea", "jumbo", "carrefour", "changomas", "dia". Si se omite,
            busca en todos.
    """
    cantidad = max(1, min(cantidad_por_super, MAX_POR_SUPER))

    claves = [s.strip().lower() for s in supermercados] if supermercados else list(SUPERMERCADOS)
    desconocidos = [s for s in claves if s not in SUPERMERCADOS]
    if desconocidos:
        return {"error": f"Supermercado desconocido: {desconocidos}. "
                         f"Opciones válidas: {list(SUPERMERCADOS)}"}
    elegidos = [SUPERMERCADOS[s] for s in dict.fromkeys(claves)]  # sin repetidos

    # Consultamos los supermercados elegidos en paralelo
    with ThreadPoolExecutor(max_workers=len(elegidos)) as pool:
        resultados = list(pool.map(lambda a: _buscar_en(a, producto), elegidos))

    por_super, errores, mejor, todos = {}, [], None, []
    for nombre, productos, error in resultados:
        if error:
            # un supermercado que falla no corta la búsqueda en los demás
            errores.append(f"{nombre}: {error}")
            continue

        # la API trae ruido ("yerba mate" -> tomate): se filtra antes de ordenar para que
        # no ocupe el top que ve el modelo ni quede como "mas_barato"
        disponibles = [p for p in productos if p.disponible and _es_relevante(p.nombre, producto)]
        # primero los que tienen precio unitario (ordenados), al final los que no
        disponibles.sort(key=lambda p: (p.precio_unitario is None, p.precio_unitario or 0))
        if not disponibles:
            continue
        todos.extend(disponibles)  # a la cache van todos, no solo los que ve el modelo

        por_super[nombre] = [_formatear(p) for p in disponibles[:cantidad]]

        # el más barato global (solo comparable si tiene precio unitario)
        top = disponibles[0]
        if top.precio_unitario and (mejor is None or top.precio_unitario < mejor.precio_unitario):
            mejor = top

    # optimizar_compra lee de acá, sin nuevos requests. Se reemplazan solo los súper que
    # respondieron bien: si uno falló, se conservan sus resultados anteriores (si había).
    consultados = {nombre for nombre, _, error in resultados if not error}
    guardar_busqueda(producto, todos, consultados)

    respuesta = {"busqueda": producto, "por_supermercado": por_super}
    if mejor:
        respuesta["mas_barato"] = {
            "supermercado": mejor.supermercado,
            "nombre": mejor.nombre,
            "precio": mejor.precio,
            "precio_unitario": mejor.precio_unitario,
            "unidad_precio": ETIQUETA_UNIDAD.get(mejor.unidad),
        }
    if errores:
        respuesta["errores"] = errores
    if not por_super:
        respuesta["sugerencia"] = "Sin resultados disponibles. Probá con un término más general o sin la marca."
    return respuesta


# ---------------------------------------------------------------- optimizar_compra

# unidad que pide el modelo -> (unidad base del Producto, factor para pasar a esa base)
UNIDADES = {
    "l": ("ml", 1000), "lt": ("ml", 1000), "litro": ("ml", 1000), "litros": ("ml", 1000),
    "ml": ("ml", 1),
    "kg": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000),
    "g": ("g", 1), "gr": ("g", 1),
    "un": ("un", 1), "unidad": ("un", 1), "unidades": ("un", 1),
}


class ItemCompra(BaseModel):
    busqueda: str = Field(description="El MISMO texto usado antes en buscar_productos.")
    cantidad: float = Field(gt=0, description="Cantidad total a comprar, ej: 2 (litros).")
    unidad: str = Field(description='"L", "ml", "kg", "g" o "un".')


def _costo(prod, cantidad_base: float) -> tuple[float, float] | None:
    """Costo de cubrir cantidad_base con este producto: (subtotal, envases)."""
    if prod.venta_por_peso and prod.precio_unitario:          # se paga el peso exacto
        return prod.precio_unitario * cantidad_base / 1000, cantidad_base / 1000
    if not prod.contenido:
        return None
    envases = math.ceil(round(cantidad_base / prod.contenido, 6))  # no se compra medio envase
    return prod.precio * envases, envases


# palabras de la búsqueda que no sirven para decidir si un producto es relevante
_IGNORAR = {"de", "del", "la", "el", "los", "las", "en", "con", "sin", "x", "y", "por",
            "l", "lt", "lts", "litro", "litros", "ml", "cc", "kg", "kilo", "kilos", "g", "gr",
            "grs", "gramos", "un", "unidad", "unidades"}


def _palabras(texto: str) -> list[str]:
    """Minúsculas, sin acentos, separado en palabras."""
    texto = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", texto)


def _es_relevante(nombre: str, busqueda: str) -> bool:
    """Cada palabra clave de la búsqueda tiene que estar en el nombre como palabra
    (no como pedazo: "mate" no matchea "tomate"). Tolera plurales: "galletita" ~ "galletitas"."""
    claves = [w.rstrip("s") for w in _palabras(busqueda) if w not in _IGNORAR and not w.isdigit()]
    del_nombre = _palabras(nombre)
    return all(any(w.startswith(c) for w in del_nombre) for c in claves)


def _mejor_opcion(productos, unidad_base: str, cantidad_base: float, busqueda: str) -> dict | None:
    mejor = None
    for p in productos:
        if p.unidad != unidad_base:   # descarta lo que no se mide igual (ej: pedís L y es un pack "un")
            continue
        if not _es_relevante(p.nombre, busqueda):   # la API trae ruido ("yerba mate" -> tomate)
            continue
        costo = _costo(p, cantidad_base)
        if costo and (mejor is None or costo[0] < mejor["subtotal"]):
            mejor = {"supermercado": p.supermercado, "nombre": p.nombre,
                     "envases": costo[1], "subtotal": costo[0]}
    return mejor


@tool(parse_docstring=True)
def optimizar_compra(items: list[ItemCompra]) -> dict:
    """Calcula la forma más barata de comprar una lista completa, usando los precios
    ya obtenidos con buscar_productos (no consulta de nuevo los supermercados).
    Compara comprar cada producto donde está más barato (compra mixta) contra
    comprar todo en un solo supermercado, considerando envases enteros.

    Antes de usarla, llamá a buscar_productos para cada ítem y pasá acá exactamente
    el mismo texto de búsqueda. Revisá los nombres elegidos: si alguno no corresponde
    a lo pedido, repetí la búsqueda con un término más específico.

    Args:
        items: productos a comprar, cada uno con busqueda, cantidad y unidad.
    """
    sin_buscar, unidad_invalida = [], []
    mixta, por_super = [], {}   # por_super: súper -> {busqueda: opción}
    pedidos = []

    for item in items:
        item = item if isinstance(item, ItemCompra) else ItemCompra(**item)
        productos = obtener_busqueda(item.busqueda)
        if productos is None:
            sin_buscar.append(item.busqueda)
            continue
        if item.unidad.lower() not in UNIDADES:
            unidad_invalida.append(f"{item.busqueda} ({item.unidad})")
            continue
        unidad_base, factor = UNIDADES[item.unidad.lower()]
        cantidad_base = item.cantidad * factor
        pedidos.append(item.busqueda)

        mejor_global = _mejor_opcion(productos, unidad_base, cantidad_base, item.busqueda)
        mixta.append({"busqueda": item.busqueda, **(mejor_global or {"sin_opciones": True})})

        for super_ in {p.supermercado for p in productos}:
            opcion = _mejor_opcion([p for p in productos if p.supermercado == super_],
                                   unidad_base, cantidad_base, item.busqueda)
            if opcion:
                por_super.setdefault(super_, {})[item.busqueda] = opcion

    def _r(x):
        return round(x, 2)

    for d in mixta:
        if "subtotal" in d:
            d["subtotal"] = _r(d["subtotal"])
    total_mixta = sum(d.get("subtotal", 0) for d in mixta)

    # resumen por supermercado: total y qué le falta
    resumen = []
    for super_, opciones in por_super.items():
        faltan = [b for b in pedidos if b not in opciones]
        resumen.append({"supermercado": super_,
                        "total": _r(sum(o["subtotal"] for o in opciones.values())),
                        **({"faltan": faltan} if faltan else {})})
    resumen.sort(key=lambda r: ("faltan" in r, r["total"]))   # primero los completos

    respuesta = {"compra_mixta": {"total": _r(total_mixta), "detalle": mixta},
                 "por_supermercado": resumen}
    completos = [r for r in resumen if "faltan" not in r]
    if completos:
        mejor = completos[0]
        respuesta["mejor_un_solo_super"] = {
            "supermercado": mejor["supermercado"], "total": mejor["total"],
            "ahorro_compra_mixta": _r(mejor["total"] - total_mixta),
            "detalle": [{"busqueda": b, "nombre": o["nombre"], "envases": o["envases"],
                         "subtotal": _r(o["subtotal"])}
                        for b, o in por_super[mejor["supermercado"]].items()],
        }
    if sin_buscar:
        respuesta["sin_buscar"] = sin_buscar
        respuesta["sugerencia"] = "Llamá primero a buscar_productos con estos textos exactos."
    if unidad_invalida:
        respuesta["unidad_invalida"] = unidad_invalida
    return respuesta

import math
import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import combinations
from typing import Annotated, NotRequired
from langchain.agents import AgentState
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field
import preferencias as prefs_
from cache import guardar_busqueda, obtener_busqueda
from preferencias import TipoPreferencia
from supermercados import SUPERMERCADOS

RESULTADOS_POR_SUPER = 20   # cuántos traemos de cada API para después ordenar nosotros
MAX_POR_SUPER = 5           # tope de lo que devolvemos por súper (cuidado del contexto)
ETIQUETA_UNIDAD = {"ml": "$/L", "g": "$/kg", "un": "$/un", "m": "$/m", "m2": "$/m2"}


@dataclass
class Contexto:
    """Lo que la UI le pasa al agente en cada mensaje. carrito: los ítems del carrito
    (estado local de la sesión), para que optimizar_carrito los pueda ver."""
    carrito: list[dict] = field(default_factory=list)


# Reducers: el modelo puede llamar varias tools en paralelo en el mismo paso (ej: un
# descartar_productos por cada búsqueda). Sin reducer, LangGraph no sabe qué valor quedarse
# y corta con InvalidUpdateError. Con reducer, cada tool manda solo su cambio y se combinan.

def _ultimo(actual, nuevo):
    return nuevo


def _fusionar_preferencias(actual, nuevo) -> dict:
    """{clave: preferencia}; una clave con None la quita. Acepta el formato viejo (lista)."""
    como_dict = lambda x: {p["clave"]: p for p in x} if isinstance(x, list) else dict(x or {})
    resultado = como_dict(actual)
    for clave, pref in como_dict(nuevo).items() if isinstance(nuevo, list) else (nuevo or {}).items():
        if pref is None:
            resultado.pop(clave, None)
        else:
            resultado[clave] = pref
    return resultado


def _fusionar_descartados(actual, nuevo) -> dict:
    """{búsqueda: [claves de producto]}: se suman, sin repetidos."""
    resultado = {k: list(v) for k, v in (actual or {}).items()}
    for busqueda, claves in (nuevo or {}).items():
        resultado[busqueda] = list(dict.fromkeys(resultado.get(busqueda, []) + list(claves)))
    return resultado


class EstadoAgente(AgentState):
    """Estado de la conversación (se guarda en checkpoints.db con los mensajes).
    supermercados: los que eligió el usuario con elegir_supermercados; ausente = todos.
    preferencias: las de esta conversación, {clave: preferencia} (las de largo plazo están en el Store).
    descartados: por búsqueda, los productos que el modelo marcó como que no corresponden."""
    supermercados: NotRequired[Annotated[list[str] | None, _ultimo]]
    preferencias: NotRequired[Annotated[dict[str, dict], _fusionar_preferencias]]
    descartados: NotRequired[Annotated[dict[str, list[str]], _fusionar_descartados]]


def _validar(supermercados: list[str]) -> tuple[list[str], list[str]]:
    """(claves normalizadas sin repetidos, desconocidas)."""
    claves = list(dict.fromkeys(s.strip().lower() for s in supermercados))
    return claves, [s for s in claves if s not in SUPERMERCADOS]


def _por_defecto(runtime) -> list[str]:
    """Si el modelo no indica supermercados: los que eligió el usuario en esta conversación, o todos."""
    estado = getattr(runtime, "state", None) or {}
    return estado.get("supermercados") or list(SUPERMERCADOS)


def _preferencias(runtime) -> list[dict]:
    """Las que valen ahora: largo plazo (Store) + esta conversación (estado)."""
    return prefs_.efectivas(getattr(runtime, "state", None), getattr(runtime, "store", None))


def _presentacion(prod) -> str | None:
    """1000 ml -> '1 L', 500 g -> '500 g', 6 un -> '6 un'."""
    if not prod.contenido or not prod.unidad:
        return None
    grande = {"ml": "L", "g": "kg"}.get(prod.unidad)
    if grande and prod.contenido >= 1000:
        return f"{prod.contenido / 1000:g} {grande}"
    return f"{prod.contenido:g} {prod.unidad}"


def _variantes(productos) -> dict:
    """Resumen de lo encontrado para ofrecerle al usuario cómo afinar la búsqueda."""
    marcas = Counter(p.marca for p in productos if p.marca)
    presentaciones = Counter(filter(None, map(_presentacion, productos)))
    return {"marcas": [m for m, _ in marcas.most_common(8)],
            "presentaciones": [x for x, _ in presentaciones.most_common(6)]}


def clave_producto(supermercado: str, sku_id: str) -> str:
    """Identifica un producto entre supermercados: 'dia|12345' (los sku se repiten entre súper)."""
    return f"{prefs_._normalizar(supermercado).replace(' ', '')}|{sku_id}"


def _nombres(claves) -> str:
    return ", ".join(SUPERMERCADOS[s].nombre for s in claves)


# ---------------------------------------------------------------- elegir_supermercados

@tool
def elegir_supermercados(runtime: ToolRuntime[Contexto]) -> Command:
    """Le pregunta al usuario en qué supermercados buscar, con una ventana para elegir.

    Usala UNA vez, sola y antes de buscar_productos, cuando el usuario pide comparar
    precios y no dijo en qué supermercados. No la uses si ya los nombró ("en Vea y Día"),
    si ya los eligió antes en esta conversación (respetá esa elección en las búsquedas
    siguientes) o si pide explícitamente cambiarlos (en ese caso sí, volvé a preguntar).
    """
    # pausa el agente: la UI muestra la ventana y lo reanuda con la lista elegida
    elegidos = interrupt({"tipo": "elegir_supermercados"})
    claves, desconocidos = _validar(elegidos or [])
    claves = [c for c in claves if c not in desconocidos] or list(SUPERMERCADOS)
    texto = ("El usuario eligió buscar en todos los supermercados." if len(claves) == len(SUPERMERCADOS)
             else f"El usuario eligió buscar solo en: {_nombres(claves)}. Usá esa elección en las búsquedas "
                  f"de esta conversación, salvo que pida explícitamente otro supermercado.")
    return Command(update={
        "supermercados": None if len(claves) == len(SUPERMERCADOS) else claves,
        "messages": [ToolMessage(texto, tool_call_id=runtime.tool_call_id)],
    })


# ---------------------------------------------------------------- preferencias

@tool(parse_docstring=True)
def guardar_preferencia(tipo: TipoPreferencia, valor: str | None = None,
                        alcance: str = "conversacion", runtime: ToolRuntime[Contexto] = None) -> Command:
    """Guarda una preferencia del usuario para que las búsquedas la respeten.

    Usala cuando el usuario expresa un gusto o restricción: marcas que no quiere,
    marcas que prefiere, no querer marcas propias del súper, u otra condición.
    Traducí lo abstracto a algo concreto: "segundas marcas" no es un dato; si no
    está claro a qué se refiere, preguntale antes (¿marcas propias del súper?
    ¿qué marcas prefiere?).

    Alcance: "siempre" solo si lo dijo explícitamente ("siempre", "nunca", "en
    general", "no me gusta X"). Si fue ambiguo, guardala en "conversacion" y
    preguntale si quiere que la recuerdes para próximas compras; si dice que sí,
    volvé a llamarla con "siempre". Si es temporal ("esta vez"), "conversacion".

    Args:
        tipo: "excluir_marca", "preferir_marca", "excluir_marcas_propias" (las del
            súper, ej: marca Día en Día) o "nota" (otra condición, ej: "sin TACC";
            esta no la filtra la herramienta: tenela en cuenta vos al elegir).
        valor: la marca o el texto de la nota. Vacío para excluir_marcas_propias.
        alcance: "conversacion" o "siempre".
    """
    pref = prefs_.nueva(tipo, valor)
    if alcance == "siempre":
        prefs_.guardar(pref, getattr(runtime, "store", None))
        texto = f"Guardada para siempre: {prefs_.etiqueta(pref)}."
        cambio = {pref["clave"]: None}   # si estaba en la conversación, pasa a largo plazo
    else:
        texto = (f"Guardada para esta conversación: {prefs_.etiqueta(pref)}. Si no lo pidió "
                 f"explícitamente para siempre, preguntale si quiere que la recuerdes.")
        cambio = {pref["clave"]: pref}
    return Command(update={"preferencias": cambio,
                           "messages": [ToolMessage(texto, tool_call_id=runtime.tool_call_id)]})


@tool(parse_docstring=True)
def quitar_preferencia(tipo: TipoPreferencia, valor: str | None = None,
                       runtime: ToolRuntime[Contexto] = None) -> Command:
    """Quita una preferencia (de esta conversación y de las guardadas para siempre),
    cuando el usuario dice que ya no le importa o cambió de idea.

    Args:
        tipo: el mismo tipo con que se guardó.
        valor: la misma marca o texto con que se guardó.
    """
    clave = prefs_.nueva(tipo, valor)["clave"]
    prefs_.borrar(clave, getattr(runtime, "store", None))
    texto = f"Preferencia quitada: {prefs_.etiqueta(prefs_.nueva(tipo, valor))}."
    return Command(update={"preferencias": {clave: None},
                           "messages": [ToolMessage(texto, tool_call_id=runtime.tool_call_id)]})


# ---------------------------------------------------------------- descartar_productos

class ProductoDescartado(BaseModel):
    supermercado: str = Field(description='Como aparece en los resultados, ej: "Día".')
    sku_id: str = Field(description="El sku_id del producto en los resultados.")


@tool(parse_docstring=True)
def descartar_productos(busqueda: str, productos: list[ProductoDescartado], motivo: str,
                        runtime: ToolRuntime[Contexto] = None) -> Command:
    """Registra los resultados de una búsqueda que no corresponden a lo que pidió el
    usuario, para que no se le ofrezcan (lista para el carrito) ni se usen al optimizar.

    Llamala SIEMPRE que descartes resultados, antes de responder: no alcanza con
    decirlo en el texto. Ej: buscando cereales de desayuno, una jardinera de legumbres
    o unas galletitas de salvado.

    Args:
        busqueda: el MISMO texto usado en buscar_productos.
        productos: los que no corresponden, con su supermercado y sku_id.
        motivo: por qué no corresponden, en pocas palabras.
    """
    nuevos = [clave_producto(p.supermercado, p.sku_id) for p in
              (x if isinstance(x, ProductoDescartado) else ProductoDescartado(**x) for x in productos)]
    texto = f"Ocultados {len(nuevos)} resultados de '{busqueda}' ({motivo})."
    # solo lo nuevo: el reducer lo suma a lo que ya había (aunque haya otras llamadas en paralelo)
    return Command(update={"descartados": {prefs_._normalizar(busqueda): nuevos},
                           "messages": [ToolMessage(texto, tool_call_id=runtime.tool_call_id)]})


# ---------------------------------------------------------------- buscar_productos

def _formatear(prod, preferida: bool = False) -> dict:
    """Solo los campos que el modelo necesita, para no llenar el contexto.
    El supermercado no va acá porque los resultados ya vienen agrupados por súper."""
    return ({"marca_preferida": True} if preferida else {}) | {
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
                     supermercados: list[str] | None = None,
                     runtime: ToolRuntime[Contexto] = None) -> dict:
    """Busca un producto en los supermercados disponibles (todos, o solo los indicados)
    y devuelve, para cada supermercado, las opciones más convenientes ordenadas por
    precio unitario ($/L, $/kg o $/un). Sirve para comparar precios entre supermercados.

    Si el usuario pide un supermercado puntual, pasalo en `supermercados` en vez de
    buscar en todos y filtrar después. Si lo omitís, se usan los que el usuario eligió
    con elegir_supermercados en esta conversación (o todos, si no eligió).

    Empezá buscando con las palabras del usuario, sin agregar detalles que no dijo
    (si pide "leche", buscá "leche"). La respuesta trae `variantes` (marcas y
    presentaciones encontradas): usalas para ofrecerle al final afinar la búsqueda
    con algo más específico (tipo, marca, tamaño) o quedarse con lo más barato.
    Revisá la categoría de cada resultado: los que no correspondan (ej: chocolates
    al buscar "leche") registralos con descartar_productos antes de responder.

    Ya aplica las preferencias del usuario (marcas a evitar o preferidas): lo que
    se descartó por eso viene en `descartados_por_preferencias`.

    Args:
        producto: texto a buscar, con las palabras del usuario o afinado si él lo pidió.
        cantidad_por_super: cantidad de opciones a devolver por supermercado (1 a 5).
        supermercados: opcional, limita la búsqueda a estos supermercados. Valores
            válidos: "vea", "jumbo", "carrefour", "changomas", "dia". Si se omite,
            usa la elección del usuario o todos.
    """
    cantidad = max(1, min(cantidad_por_super, MAX_POR_SUPER))

    claves, desconocidos = _validar(supermercados or _por_defecto(runtime))
    if desconocidos:
        return {"error": f"Supermercado desconocido: {desconocidos}. "
                         f"Opciones válidas: {list(SUPERMERCADOS)}"}
    elegidos = [SUPERMERCADOS[s] for s in claves]

    # Consultamos los supermercados elegidos en paralelo
    with ThreadPoolExecutor(max_workers=len(elegidos)) as pool:
        resultados = list(pool.map(lambda a: _buscar_en(a, producto), elegidos))

    prefs = _preferencias(runtime)
    por_super, errores, mejor, todos, descartados, vistos = {}, [], None, [], 0, []
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
        todos.extend(disponibles)  # a la cache van todos (las preferencias se aplican al leer)

        # preferencias: se sacan las marcas a evitar y las preferidas pasan adelante
        disponibles, n = prefs_.aplicar(disponibles, prefs)
        descartados += n
        if not disponibles:
            continue
        vistos.extend(disponibles)

        por_super[nombre] = [_formatear(p, prefs_.es_preferida(p, prefs)) for p in disponibles[:cantidad]]

        # el más barato global (solo comparable si tiene precio unitario)
        con_unitario = [p for p in disponibles if p.precio_unitario]
        top = min(con_unitario, key=lambda p: p.precio_unitario) if con_unitario else None
        if top and (mejor is None or top.precio_unitario < mejor.precio_unitario):
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
    if vistos:
        respuesta["variantes"] = _variantes(vistos)
    if prefs:
        respuesta["preferencias_aplicadas"] = [prefs_.etiqueta(p) for p in prefs]
    if descartados:
        respuesta["descartados_por_preferencias"] = descartados
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


def _r(x):
    return round(x, 2)


def _planificar(opciones: dict[str, dict[str, dict]], max_supermercados: int | None = None) -> dict | None:
    """Mejor forma de comprar todos los pedidos usando como mucho max_supermercados.
    opciones: {pedido: {supermercado: opción con "subtotal"}}. Prueba todas las combinaciones
    de supermercados (con 5 súper son 31) y en cada una elige lo más barato de cada pedido."""
    opciones = {p: ops for p, ops in opciones.items() if ops}
    supers = sorted({s for ops in opciones.values() for s in ops})
    tope = min(max_supermercados or len(supers), len(supers))
    mejor = None
    for k in range(1, tope + 1):
        for combo in combinations(supers, k):
            elegidas = {}
            for pedido, ops in opciones.items():
                candidatas = [ops[s] for s in combo if s in ops]
                if not candidatas:
                    break   # este combo no cubre el pedido
                elegidas[pedido] = min(candidatas, key=lambda o: o["subtotal"])
            else:
                total = sum(o["subtotal"] for o in elegidas.values())
                if mejor is None or total < mejor["total"] - 0.005:
                    usados = sorted({o["supermercado"] for o in elegidas.values()})
                    mejor = {"total": _r(total), "supermercados": usados, "elegidas": elegidas}
    return mejor


def _por_cantidad_de_supermercados(opciones: dict[str, dict[str, dict]]) -> list[dict]:
    """Cuánto sale la compra yendo a 1, 2, 3... supermercados: para ver si ir a uno más
    vale la pena. Solo se listan las cantidades que bajan el total."""
    filas, anterior = [], None
    n_supers = len({s for ops in opciones.values() for s in ops})
    for k in range(1, n_supers + 1):
        plan = _planificar(opciones, k)
        if plan and (anterior is None or plan["total"] < anterior - 0.005):
            filas.append({"max_supermercados": k, "total": plan["total"], "en": plan["supermercados"]})
            anterior = plan["total"]
    return filas


@tool(parse_docstring=True)
def optimizar_compra(items: list[ItemCompra], supermercados: list[str] | None = None,
                     max_supermercados: int | None = None, runtime: ToolRuntime[Contexto] = None) -> dict:
    """Calcula la forma más barata de comprar una lista completa, usando los precios
    ya obtenidos con buscar_productos (no consulta de nuevo los supermercados).
    Compara comprar cada producto donde está más barato (compra mixta) contra
    comprar todo en un solo supermercado, considerando envases enteros.

    Antes de usarla, llamá a buscar_productos para cada ítem y pasá acá exactamente
    el mismo texto de búsqueda. Revisá los nombres elegidos: si alguno no corresponde
    a lo pedido, repetí la búsqueda con un término más específico.

    La compra mixta puede repartir la compra en muchos supermercados por poco ahorro:
    `por_cantidad_de_supermercados` dice cuánto sale yendo a 1, 2, 3... súper; usalo para
    recomendar si conviene ir a uno más. Si el usuario pone un límite ("máximo 2 súper"),
    pasalo en max_supermercados.

    Args:
        items: productos a comprar, cada uno con busqueda, cantidad y unidad.
        supermercados: opcional, supermercados a comparar (mismos valores que en
            buscar_productos). Si se omite, usa la elección del usuario o todos.
        max_supermercados: opcional, máximo de supermercados distintos a los que ir.
    """
    sin_buscar, unidad_invalida = [], []
    mixta, por_super = [], {}   # por_super: súper -> {busqueda: opción}
    pedidos = []
    # la cache puede tener súper de búsquedas anteriores: se comparan solo los que corresponden
    claves, desconocidos = _validar(supermercados or _por_defecto(runtime))
    if desconocidos:
        return {"error": f"Supermercado desconocido: {desconocidos}. "
                         f"Opciones válidas: {list(SUPERMERCADOS)}"}
    nombres_permitidos = {SUPERMERCADOS[s].nombre for s in claves}
    prefs = _preferencias(runtime)
    descartados = (getattr(runtime, "state", None) or {}).get("descartados") or {}

    for item in items:
        item = item if isinstance(item, ItemCompra) else ItemCompra(**item)
        productos = obtener_busqueda(item.busqueda)
        if productos is None:
            sin_buscar.append(item.busqueda)
            continue
        productos = [p for p in productos if p.supermercado in nombres_permitidos]
        productos, _ = prefs_.aplicar(productos, prefs)
        # los que el modelo marcó como que no corresponden no pueden salir como "más barato"
        fuera = set(descartados.get(prefs_._normalizar(item.busqueda), []))
        productos = [p for p in productos if clave_producto(p.supermercado, p.sku_id) not in fuera]
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
    # reorganizado por pedido para el planificador: {busqueda: {súper: opción}}
    opciones = {b: {s: ops[b] for s, ops in por_super.items() if b in ops} for b in pedidos}
    if len({d["supermercado"] for d in mixta if "supermercado" in d}) > 1:
        respuesta["por_cantidad_de_supermercados"] = _por_cantidad_de_supermercados(opciones)
    if max_supermercados and (plan := _planificar(opciones, max_supermercados)):
        respuesta["mejor_con_max_supermercados"] = {
            "max_supermercados": max_supermercados, "total": plan["total"],
            "supermercados": plan["supermercados"],
            "diferencia_con_compra_mixta": _r(plan["total"] - total_mixta),
            "detalle": [{"busqueda": b, "supermercado": o["supermercado"], "nombre": o["nombre"],
                         "envases": o["envases"], "subtotal": _r(o["subtotal"])}
                        for b, o in plan["elegidas"].items()],
        }
    if sin_buscar:
        respuesta["sin_buscar"] = sin_buscar
        respuesta["sugerencia"] = "Llamá primero a buscar_productos con estos textos exactos."
    if unidad_invalida:
        respuesta["unidad_invalida"] = unidad_invalida
    return respuesta


# ---------------------------------------------------------------- optimizar_carrito

def _asegurar_cache(busqueda: str, claves: list[str]) -> None:
    """La cache es del proceso: si la app se reinició, se vuelve a buscar (sin pasar por el modelo)."""
    if obtener_busqueda(busqueda) is not None:
        return
    elegidos = [SUPERMERCADOS[c] for c in claves]
    with ThreadPoolExecutor(max_workers=len(elegidos)) as pool:
        resultados = list(pool.map(lambda a: _buscar_en(a, busqueda), elegidos))
    todos = [p for _, productos, error in resultados if not error for p in productos
             if p.disponible and _es_relevante(p.nombre, busqueda)]
    guardar_busqueda(busqueda, todos, {nombre for nombre, _, error in resultados if not error})


def _misma_marca(a, b) -> bool:
    marca_a, marca_b = prefs_._normalizar(a.marca), prefs_._normalizar(b.marca)
    return marca_a == marca_b if marca_a and marca_b else False


def _opcion(prod, cantidad_base: float) -> dict | None:
    costo = _costo(prod, cantidad_base)
    if not costo:
        return None
    return {"supermercado": prod.supermercado, "nombre": prod.nombre, "sku_id": prod.sku_id,
            "presentacion": _presentacion(prod), "envases": costo[1], "subtotal": costo[0]}


@tool(parse_docstring=True)
def optimizar_carrito(max_supermercados: int | None = None, runtime: ToolRuntime[Contexto] = None) -> dict:
    """Revisa el carrito del usuario y dice qué conviene cambiar para pagar menos por
    lo mismo: el mismo producto en otro supermercado, o la misma marca en otra
    presentación (ej: 1 envase de 1 L en vez de 2 de 500 ml). Aparte, informa si hay
    otra marca más barata, por si al usuario le da igual la marca.

    Usala cuando el usuario pide optimizar, revisar o abaratar su carrito. Usa los
    precios ya buscados (vuelve a buscar solo si hace falta). Explicá los cambios con
    el ahorro de cada uno; no cambia el carrito: el usuario decide.

    Args:
        max_supermercados: opcional, máximo de supermercados distintos a los que ir.
    """
    carrito = (getattr(getattr(runtime, "context", None), "carrito", None)) or []
    if not carrito:
        return {"error": "El carrito está vacío. El usuario agrega productos con ➕ desde los resultados."}

    claves = _validar(_por_defecto(runtime))[0]
    nombres_permitidos = {SUPERMERCADOS[s].nombre for s in claves}
    prefs = _preferencias(runtime)
    descartados = (getattr(runtime, "state", None) or {}).get("descartados") or {}

    opciones, items, otras_marcas, sin_datos = {}, [], [], []
    for i, item in enumerate(carrito):
        busqueda = item.get("busqueda")
        if not busqueda:
            sin_datos.append(item["nombre"])
            continue
        _asegurar_cache(busqueda, claves)
        productos = obtener_busqueda(busqueda) or []
        clave_actual = clave_producto(item["supermercado"], item["sku_id"])
        actual = next((p for p in productos if clave_producto(p.supermercado, p.sku_id) == clave_actual), None)
        if actual is None or not actual.unidad:
            sin_datos.append(item["nombre"])
            continue

        # cuánto lleva el usuario, en unidad base (ml, g o un)
        cantidad_base = item["cantidad"] * (1000 if actual.venta_por_peso else (actual.contenido or 0))
        if not cantidad_base:
            sin_datos.append(item["nombre"])
            continue
        costo_actual = item["precio"] * item["cantidad"]
        fuera = set(descartados.get(prefs_._normalizar(busqueda), []))
        candidatos = [p for p in productos
                      if p.unidad == actual.unidad and p.supermercado in nombres_permitidos | {actual.supermercado}
                      and clave_producto(p.supermercado, p.sku_id) not in fuera]
        candidatos, _ = prefs_.aplicar(candidatos, prefs)

        # lo que tiene hoy, con el precio del carrito (la cache puede tener otro)
        pedido = f"{i}:{item['nombre']}"
        hoy = {"supermercado": actual.supermercado, "nombre": actual.nombre, "sku_id": actual.sku_id,
               "presentacion": _presentacion(actual), "envases": item["cantidad"], "subtotal": costo_actual}
        opciones[pedido] = {actual.supermercado: hoy}
        # misma marca, en cualquier súper y presentación: la opción más barata de cada súper
        for p in candidatos:
            if not _misma_marca(p, actual):
                continue
            o = _opcion(p, cantidad_base)
            previa = opciones[pedido].get(p.supermercado)
            if o and (previa is None or o["subtotal"] < previa["subtotal"]):
                opciones[pedido][p.supermercado] = o
        items.append((pedido, item, actual, costo_actual, cantidad_base, candidatos))

    if not items:
        return {"error": "No se encontraron los productos del carrito en las búsquedas. "
                         "Volvé a buscarlos y agregalos de nuevo.", "sin_datos": sin_datos}

    plan = _planificar(opciones, max_supermercados)
    total_actual = sum(costo for _, _, _, costo, _, _ in items)
    cambios, sin_cambios = [], []
    for pedido, item, actual, costo_actual, cantidad_base, candidatos in items:
        elegida = plan["elegidas"][pedido]
        ahorro = costo_actual - elegida["subtotal"]
        if ahorro > 0.5:
            mismo_super = elegida["supermercado"] == item["supermercado"]
            cambios.append({
                "producto": item["nombre"], "en_el_carrito": f"{item['cantidad']} × {item['supermercado']}",
                "tipo": "presentación" if mismo_super else (
                    "supermercado" if elegida["presentacion"] == _presentacion(actual)
                    else "supermercado y presentación"),
                "cambiar_por": f"{elegida['envases']:g} × {elegida['nombre']} en {elegida['supermercado']}",
                "ahorro": _r(ahorro)})
        else:
            sin_cambios.append(item["nombre"])
        # otra marca más barata (solo informativo: el usuario eligió esa marca)
        otras = [o for p in candidatos if not _misma_marca(p, actual) and (o := _opcion(p, cantidad_base))]
        mas_barata = min(otras, key=lambda o: o["subtotal"], default=None)
        if mas_barata and mas_barata["subtotal"] < elegida["subtotal"] * 0.95:   # al menos 5% menos
            otras_marcas.append({"en_vez_de": item["nombre"],
                                 "opcion": f"{mas_barata['envases']:g} × {mas_barata['nombre']} en {mas_barata['supermercado']}",
                                 "ahorro_extra": _r(elegida["subtotal"] - mas_barata["subtotal"])})

    respuesta = {
        "total_actual": _r(total_actual), "supermercados_actual": sorted({it["supermercado"] for _, it, *_ in items}),
        "total_optimizado": plan["total"], "supermercados_optimizado": plan["supermercados"],
        "ahorro": _r(total_actual - plan["total"]), "cambios": cambios, "sin_cambios": sin_cambios,
    }
    if max_supermercados:
        respuesta["max_supermercados"] = max_supermercados
    if len(plan["supermercados"]) > 1:
        respuesta["por_cantidad_de_supermercados"] = _por_cantidad_de_supermercados(opciones)
    if otras_marcas:
        respuesta["otras_marcas_mas_baratas"] = otras_marcas
    if sin_datos:
        respuesta["sin_datos"] = sin_datos
    return respuesta

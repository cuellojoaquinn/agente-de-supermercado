"""UI del agente en Streamlit.

Cubre la consigna:
- 3.4: chat con st.chat_input / st.chat_message, respuestas renderizadas en markdown.
- 3.5: en streaming, cada paso apenas termina: razonamiento (diferenciado, en un expander),
  cada tool con sus argumentos y un resumen del resultado, en orden.
- 3.3: memoria con checkpointer SQLite (thread_id en session_state); las conversaciones
  guardadas se listan en la barra lateral (por fecha, con buscador) y se pueden retomar.
- 4: errores de cuota/proveedor como mensaje claro, sin traceback.
- Preferencias (conversación y largo plazo) como chips en la barra lateral, y carrito:
  los productos encontrados se pueden agregar a un carrito (estado local de la sesión).

Correr con:  streamlit run app.py
"""
import json
import unicodedata
import uuid
from pathlib import Path
from datetime import date, datetime

import streamlit as st

import preferencias
from agente import crear_agente, listar_conversaciones, mensaje_error
from model import modelos_disponibles, proveedor_default
from supermercados import SUPERMERCADOS
from langgraph.types import Command
from tools import Contexto, clave_producto

st.set_page_config(page_title="Super-agente", page_icon="🛒")


# ------------------------------------------------------------------ agente y memoria

@st.cache_resource  # una sola instancia del agente (la memoria vive en checkpoints.db)
def get_agente():
    return crear_agente()


def nueva_conversacion():
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.historial = []
    st.session_state.en_curso = None  # turno pausado esperando que el usuario elija supermercados


if "thread_id" not in st.session_state:
    nueva_conversacion()
st.session_state.setdefault("carrito", {})  # estado local: no se guarda en la base
st.session_state.setdefault("en_curso", None)  # sesiones abiertas antes de este cambio

MODELO_ACTUAL = modelos_disponibles()[proveedor_default()]  # el del .env (PROVEEDOR)

agente = get_agente()
config = {"configurable": {"thread_id": st.session_state.thread_id}}


# ------------------------------------------------------------------ resúmenes de tools

def _pesos(x) -> str:
    """1776.6 -> $1.776,60 (formato argentino)."""
    if x is None:
        return "-"
    texto = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${texto.removesuffix(',00')}"


def md(texto: str) -> str:
    """Escapa los $ para st.markdown: dos $ en una línea ("$1.890 ... $5.999") los toma
    como una fórmula LaTeX y rompe el texto."""
    return texto.replace("$", r"\$")


def resumir_resultado(nombre: str, contenido) -> str:
    """Una línea legible en lugar del JSON completo que recibe el modelo."""
    if nombre == "cargar_skill":  # devuelve markdown, no JSON
        texto = str(contenido)
        if texto.startswith("Error"):
            return f"⚠️ {texto}"
        lineas = texto.splitlines()
        nombre_skill = lineas[0].removeprefix("Skill:").strip() if lineas else "?"
        titulo = next((l.lstrip("# ") for l in lineas if l.startswith("#")), "")
        return f"📘 skill cargada: **{nombre_skill}**" + (f" · {titulo}" if titulo else "")
    try:
        datos = contenido if isinstance(contenido, dict) else json.loads(contenido)
    except (TypeError, ValueError):
        return str(contenido)[:200]

    if nombre == "buscar_productos":
        supers = datos.get("por_supermercado", {})
        partes = [f"{len(supers)} súper con resultados"]
        if mb := datos.get("mas_barato"):
            partes.append(f"el más barato: **{mb['nombre']}** en {mb['supermercado']} "
                          f"({_pesos(mb['precio_unitario'])}{(mb.get('unidad_precio') or '').removeprefix('$')})")
        if n := datos.get("descartados_por_preferencias"):
            partes.append(f"{n} descartados por tus preferencias")
        if datos.get("errores"):
            partes.append(f"⚠️ {len(datos['errores'])} súper con error")
        if datos.get("sugerencia"):
            partes.append(datos["sugerencia"])
        return " · ".join(partes)

    if nombre == "optimizar_compra":
        partes = []
        if mixta := datos.get("compra_mixta"):
            lugares = sorted({d["supermercado"] for d in mixta["detalle"] if "supermercado" in d})
            partes.append(f"compra mixta {_pesos(mixta['total'])} ({', '.join(lugares)})")
        if mejor := datos.get("mejor_un_solo_super"):
            partes.append(f"todo en **{mejor['supermercado']}** {_pesos(mejor['total'])}")
        if limitada := datos.get("mejor_con_max_supermercados"):
            partes.append(f"con máximo {limitada['max_supermercados']} súper {_pesos(limitada['total'])}")
        if datos.get("sin_buscar"):
            partes.append(f"⚠️ sin buscar antes: {', '.join(datos['sin_buscar'])}")
        return " · ".join(partes) or "sin resultados"

    if nombre == "generar_plan_compra":
        if datos.get("error"):
            return f"⚠️ {datos['error']}"
        return f"📄 plan generado: **{datos.get('titulo')}** · total {_pesos(datos.get('total') or 0)}"

    if nombre == "optimizar_carrito":
        if datos.get("error"):
            return f"⚠️ {datos['error']}"
        partes = [f"hoy {_pesos(datos['total_actual'])} → optimizado {_pesos(datos['total_optimizado'])}",
                  f"ahorro **{_pesos(datos['ahorro'])}** con {len(datos['cambios'])} cambios"]
        if datos.get("otras_marcas_mas_baratas"):
            partes.append(f"{len(datos['otras_marcas_mas_baratas'])} con otra marca más barata")
        return " · ".join(partes)

    return json.dumps(datos, ensure_ascii=False)[:200]


# ------------------------------------------------------------------ dibujo de pasos

def dibujar_paso(paso: dict):
    """Se usa igual en vivo y al redibujar el historial, así ambos se ven idénticos."""
    tipo = paso["tipo"]
    if tipo == "razonamiento":
        with st.expander("🧠 Razonamiento del modelo"):
            st.markdown(md(paso["texto"]))
    elif tipo == "nota":  # texto que el modelo escribe junto con las tool calls
        st.caption(md(f"💬 {paso['texto']}"))
    elif tipo == "tool_call":
        args = ", ".join(f"{k}={v!r}" for k, v in paso["args"].items())
        st.markdown(f"🔧 **{paso['nombre']}**(`{args}`)")
    elif tipo == "tool_result":
        st.markdown(md(f"✅ `{paso['nombre']}` → {paso['resumen']}"))


def dibujar_turno_asistente(turno: dict, indice: int | None = None):
    """indice: posición en el historial; si viene, se muestran los productos para el carrito."""
    if turno["pasos"]:
        n_tools = sum(p["tipo"] == "tool_call" for p in turno["pasos"])
        with st.expander(f"🔍 Qué hizo el agente ({n_tools} llamadas a herramientas)"):
            for paso in turno["pasos"]:
                dibujar_paso(paso)
    if turno.get("error"):
        st.error(turno["error"])
    if turno.get("respuesta"):
        st.markdown(md(turno["respuesta"]))
    if indice is not None:
        dibujar_planes(turno, indice)
        dibujar_productos(turno, indice)


def dibujar_planes(turno: dict, indice: int):
    """Botón de descarga por cada plan que generó el agente en este turno (resultado tangible)."""
    for i, paso in enumerate(p for p in turno["pasos"] if p.get("archivo")):
        archivo = Path(paso["archivo"])
        if not archivo.exists():  # se borró la carpeta planes/: la conversación sigue igual
            st.caption(f"📄 {archivo.name} (el archivo ya no está en disco)")
            continue
        with st.expander(f"📄 {archivo.name}", expanded=True):
            st.markdown(md(archivo.read_text(encoding="utf-8")))
            st.download_button("⬇️ Descargar plan (.md)", archivo.read_bytes(), file_name=archivo.name,
                               mime="text/markdown", key=f"plan-{indice}-{i}")


# ------------------------------------------------------------------ carrito (estado local)

def _clave_producto(supermercado: str, prod: dict) -> str:
    return f"{supermercado}|{prod['sku_id']}"


def agregar_al_carrito(supermercado: str, prod: dict, busqueda: str):
    carrito = st.session_state.carrito
    clave = _clave_producto(supermercado, prod)
    if clave in carrito:
        carrito[clave]["cantidad"] += 1
    else:
        # busqueda y sku_id: para que optimizar_carrito encuentre el producto y sus alternativas
        carrito[clave] = {"supermercado": supermercado, "nombre": prod["nombre"], "marca": prod.get("marca"),
                          "precio": prod["precio"], "cantidad": 1, "sku_id": prod["sku_id"],
                          "busqueda": busqueda}
    st.toast(f"🛒 Agregado: {prod['nombre']}")


def cambiar_cantidad(clave: str, delta: int):
    item = st.session_state.carrito[clave]
    item["cantidad"] += delta
    if item["cantidad"] <= 0:
        del st.session_state.carrito[clave]


def descartados_en_historial() -> dict[str, set[str]]:
    """Lo que el modelo marcó con descartar_productos en cualquier turno: {búsqueda: claves}."""
    descartados = {}
    for turno in st.session_state.historial:
        for paso in turno.get("pasos", []):
            if paso["tipo"] == "tool_call" and paso["nombre"] == "descartar_productos":
                args = paso["args"]
                claves = {clave_producto(p["supermercado"], p["sku_id"]) for p in args.get("productos", [])}
                descartados.setdefault(preferencias._normalizar(args.get("busqueda")), set()).update(claves)
    return descartados


def dibujar_productos(turno: dict, indice: int):
    """Por cada búsqueda del turno, los productos encontrados con un botón para el carrito.
    No se ofrecen los que el modelo descartó por no corresponder a lo pedido."""
    descartados = descartados_en_historial()
    busquedas = [p for p in turno["pasos"]
                 if p["tipo"] == "tool_result" and p["nombre"] == "buscar_productos"
                 and (p.get("datos") or {}).get("por_supermercado")]
    for j, paso in enumerate(busquedas):
        datos = paso["datos"]
        fuera = descartados.get(preferencias._normalizar(datos.get("busqueda")), set())
        por_super = {sup: [p for p in prods if clave_producto(sup, p["sku_id"]) not in fuera]
                     for sup, prods in datos["por_supermercado"].items()}
        por_super = {sup: prods for sup, prods in por_super.items() if prods}
        n = sum(len(v) for v in por_super.values())
        ocultos = sum(len(v) for v in datos["por_supermercado"].values()) - n
        if not n:
            continue
        with st.expander(f"🛍️ {datos.get('busqueda', 'Productos')}: {n} productos · agregá al carrito"):
            if ocultos:
                st.caption(f"Se ocultaron {ocultos} que no corresponden a lo que pediste.")
            for supermercado, productos in por_super.items():
                st.markdown(f"**{supermercado}**")
                for prod in productos:
                    col_nombre, col_precio, col_boton = st.columns([6, 3, 1], vertical_alignment="center")
                    marca = f" · {prod['marca']}" if prod.get("marca") else ""
                    estrella = "⭐ " if prod.get("marca_preferida") else ""
                    col_nombre.markdown(f"{estrella}{prod['nombre']}<br><small>{marca.removeprefix(' · ')}</small>",
                                        unsafe_allow_html=True)
                    unitario = (f"<br><small>{_pesos(prod['precio_unitario'])}"
                                f"{(prod.get('unidad_precio') or '').removeprefix('$')}</small>"
                                if prod.get("precio_unitario") else "")
                    col_precio.markdown(md(f"{_pesos(prod['precio'])}{unitario}"), unsafe_allow_html=True)
                    col_boton.button("➕", key=f"add-{indice}-{j}-{_clave_producto(supermercado, prod)}",
                                     help="Agregar al carrito", on_click=agregar_al_carrito,
                                     args=(supermercado, prod, datos.get("busqueda")))


def dibujar_carrito():
    carrito = st.session_state.carrito
    total = sum(i["precio"] * i["cantidad"] for i in carrito.values())
    unidades = sum(i["cantidad"] for i in carrito.values())
    with st.expander(f"🛒 Carrito · {unidades} · {_pesos(total)}" if carrito else "🛒 Carrito vacío",
                     expanded=bool(carrito)):
        if not carrito:
            st.caption("Agregá productos con ➕ desde los resultados de una búsqueda.")
            return
        por_super = {}
        for clave, item in carrito.items():
            por_super.setdefault(item["supermercado"], []).append((clave, item))
        for supermercado, items in por_super.items():
            subtotal = sum(i["precio"] * i["cantidad"] for _, i in items)
            st.markdown(f"**{supermercado}** · {_pesos(subtotal)}")
            for clave, item in items:
                st.markdown(f"<small>{item['nombre']}<br>{item['cantidad']} × {_pesos(item['precio'])}</small>",
                            unsafe_allow_html=True)
                menos, mas, _ = st.columns([1, 1, 3])
                menos.button("−", key=f"menos-{clave}", on_click=cambiar_cantidad, args=(clave, -1))
                mas.button("+", key=f"mas-{clave}", on_click=cambiar_cantidad, args=(clave, 1))
        st.markdown(f"**Total: {_pesos(total)}**")
        if len(por_super) > 1:
            st.caption(f"Comprás en {len(por_super)} supermercados distintos.")
        if st.button("✨ Optimizar carrito", use_container_width=True, type="primary",
                     disabled=bool(st.session_state.en_curso)):
            st.session_state.pregunta_programada = "Optimizá mi carrito"
            st.rerun()
        if st.button("Vaciar carrito", use_container_width=True):
            carrito.clear()
            st.rerun()


# ------------------------------------------------------------------ preferencias

def quitar_preferencia_ui(pref: dict):
    if pref["alcance"] == "siempre":
        preferencias.borrar(pref["clave"])
    else:
        agente.update_state(config, {"preferencias": {pref["clave"]: None}})  # el reducer la quita


def dibujar_preferencias():
    prefs = preferencias.efectivas(agente.get_state(config).values)
    st.markdown("**⭐ Preferencias**")
    if not prefs:
        st.caption("Contale al agente tus gustos (\"no me gustan las marcas propias\", "
                   "\"prefiero La Serenísima\") y se guardan acá.")
        return
    for pref in prefs:
        icono = "📌" if pref["alcance"] == "siempre" else "💬"
        st.button(f"{icono} {preferencias.etiqueta(pref)} ✕", key=f"pref-{pref['alcance']}-{pref['clave']}",
                  help="Guardada para siempre" if pref["alcance"] == "siempre" else "Solo en esta conversación",
                  on_click=quitar_preferencia_ui, args=(pref,),
                  disabled=bool(st.session_state.en_curso))  # no tocar el estado con el agente pausado
    st.caption("📌 siempre · 💬 esta conversación · ✕ para quitar")


# ------------------------------------------------------------------ ejecución en streaming

def pasos_de_mensaje(nodo: str, msg) -> tuple[list[dict], str | None]:
    """Convierte un mensaje del stream en pasos para la UI. Devuelve (pasos, respuesta_final)."""
    pasos, respuesta = [], None
    if nodo == "model":
        for bloque in msg.content_blocks:
            if bloque["type"] == "reasoning" and bloque.get("reasoning", "").strip():
                pasos.append({"tipo": "razonamiento", "texto": bloque["reasoning"]})
        if msg.tool_calls:
            if msg.text.strip():
                pasos.append({"tipo": "nota", "texto": msg.text.strip()})
            for tc in msg.tool_calls:
                pasos.append({"tipo": "tool_call", "nombre": tc["name"], "args": tc["args"]})
        elif msg.text.strip():
            respuesta = msg.text.strip()
    elif nodo == "tools":
        paso = {"tipo": "tool_result", "nombre": msg.name, "id": msg.tool_call_id,
                "resumen": resumir_resultado(msg.name, msg.content)}
        if msg.name == "generar_plan_compra":  # la ruta del archivo, para el botón de descarga
            try:
                datos = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                if datos.get("archivo"):
                    paso["archivo"] = datos["archivo"]
            except (ValueError, AttributeError):
                pass
        if msg.name == "buscar_productos":  # los productos se guardan para poder agregarlos al carrito
            try:
                paso["datos"] = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
            except ValueError:
                pass
        pasos.append(paso)
    return pasos, respuesta


def ejecutar(entrada, turno: dict | None = None) -> dict:
    """Corre el agente en streaming. entrada: la pregunta, o Command(resume=...) para seguir un
    turno pausado. Si el agente se pausa (elegir_supermercados), devuelve el turno con esperando=True."""
    turno = turno or {"pasos": [], "respuesta": None, "error": None}
    turno["esperando"] = False
    with st.status("Pensando...", expanded=True) as estado:
        try:
            # el carrito es estado local de la UI: se le pasa al agente en cada mensaje
            contexto = Contexto(carrito=list(st.session_state.carrito.values()))
            for chunk in agente.stream(entrada, config, context=contexto, stream_mode="updates"):
                for nodo, data in chunk.items():
                    if nodo == "__interrupt__":  # una tool pidió la intervención del usuario
                        turno["esperando"] = True
                        continue
                    # una tool que devuelve Command llega como lista de actualizaciones
                    for update in data if isinstance(data, list) else [data]:
                        for msg in (update or {}).get("messages", []):
                            pasos, respuesta = pasos_de_mensaje(nodo, msg)
                            # al reanudar tras un interrupt, LangGraph vuelve a emitir el resultado de
                            # las tools que ya habían terminado en ese paso: no se dibujan dos veces
                            vistos = {p.get("id") for p in turno["pasos"] if p.get("id")}
                            pasos = [p for p in pasos if not p.get("id") or p["id"] not in vistos]
                            for paso in pasos:
                                dibujar_paso(paso)  # se ve apenas termina cada paso
                                if paso["tipo"] == "tool_call":
                                    estado.update(label=f"Usando {paso['nombre']}...")
                            turno["pasos"].extend(pasos)
                            if respuesta:
                                turno["respuesta"] = respuesta
            n_tools = sum(p["tipo"] == "tool_call" for p in turno["pasos"])
            etiqueta = ("Esperando que elijas los supermercados" if turno["esperando"]
                        else f"Listo · {n_tools} llamadas a herramientas")
            estado.update(label=etiqueta, state="complete", expanded=False)
        except Exception as e:
            mensaje = mensaje_error(e)
            if mensaje is None:
                raise  # error que no es del proveedor: mejor ver el traceback
            turno["error"] = mensaje
            estado.update(label="Error del proveedor del modelo", state="error", expanded=False)

    if turno["error"]:
        st.error(turno["error"])
    if turno["respuesta"]:
        st.markdown(md(turno["respuesta"]))
    return turno


def terminar_turno(turno: dict):
    """Guarda el turno en el historial, o lo deja en curso si el agente espera al usuario."""
    if turno["esperando"]:
        st.session_state.en_curso = turno
    else:
        st.session_state.historial.append({"rol": "assistant", **turno})
        st.session_state.en_curso = None
    st.rerun()  # redibuja: la tarjeta si espera, y la barra lateral con la conversación


# ------------------------------------------------------------------ retomar conversaciones

def mensajes_de(thread_id: str) -> list:
    """Lee del checkpointer (SQLite) los mensajes del último checkpoint de ese hilo."""
    estado = agente.get_state({"configurable": {"thread_id": thread_id}})
    return estado.values.get("messages", [])  # hilo sin checkpoints -> {}


def historial_desde_estado(thread_id: str) -> list[dict]:
    """Rearma los turnos de la UI desde los mensajes guardados, con los mismos pasos que en vivo."""
    historial = []
    for msg in mensajes_de(thread_id):
        if msg.type == "human":
            historial.append({"rol": "user", "texto": msg.text})
            historial.append({"rol": "assistant", "pasos": [], "respuesta": None, "error": None})
        elif historial:  # "ai" -> nodo model, "tool" -> nodo tools
            pasos, respuesta = pasos_de_mensaje("model" if msg.type == "ai" else "tools", msg)
            historial[-1]["pasos"].extend(pasos)
            if respuesta:
                historial[-1]["respuesta"] = respuesta
    return historial


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para que 'azucar' encuentre 'azúcar'."""
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def resumen(thread_id: str) -> dict:
    """Título (primera pregunta), texto completo para el buscador y fecha del último mensaje."""
    estado = agente.get_state({"configurable": {"thread_id": thread_id}})
    mensajes = estado.values.get("messages", [])
    preguntas = [m.text for m in mensajes if m.type == "human"]
    titulo = preguntas[0] if preguntas else "(vacía)"
    return {
        "thread_id": thread_id,
        "titulo": titulo if len(titulo) <= 38 else titulo[:38] + "…",
        "texto": _normalizar(" ".join(m.text for m in mensajes if m.type in ("human", "ai"))),
        "fecha": datetime.fromisoformat(estado.created_at).astimezone().date(),
    }


def grupo_de_fecha(fecha: date) -> str:
    dias = (date.today() - fecha).days
    if dias <= 0:
        return "Hoy"
    if dias == 1:
        return "Ayer"
    if dias <= 7:
        return "Últimos 7 días"
    return "Anteriores"


def retomar(thread_id: str):
    st.session_state.thread_id = thread_id
    st.session_state.historial = historial_desde_estado(thread_id)
    st.session_state.en_curso = None
    # si la conversación quedó pausada esperando la elección, se vuelve a mostrar la tarjeta
    pausada = agente.get_state({"configurable": {"thread_id": thread_id}}).interrupts
    if pausada and st.session_state.historial and st.session_state.historial[-1]["rol"] == "assistant":
        st.session_state.en_curso = {**st.session_state.historial.pop(), "esperando": True}


def dibujar_historial_conversaciones():
    """Lista estilo ChatGPT/Claude: buscador + conversaciones agrupadas por fecha."""
    busqueda = _normalizar(st.text_input("Buscar", placeholder="🔎 Buscar conversaciones",
                                         label_visibility="collapsed"))
    conversaciones = [resumen(t) for t in listar_conversaciones()]  # ya vienen de la más reciente
    if busqueda:
        conversaciones = [c for c in conversaciones if busqueda in c["texto"]]
    if not conversaciones:
        st.caption("Sin resultados." if busqueda else "Todavía no hay conversaciones guardadas.")
        return
    grupo_actual = None
    for c in conversaciones:
        if (grupo := grupo_de_fecha(c["fecha"])) != grupo_actual:
            st.caption(grupo)
            grupo_actual = grupo
        st.button(c["titulo"], key=f"conv-{c['thread_id']}", use_container_width=True,
                  type="primary" if c["thread_id"] == st.session_state.thread_id else "secondary",
                  on_click=retomar, args=(c["thread_id"],))


# ------------------------------------------------------------------ página

with st.sidebar:
    st.header("🛒 Super-agente")
    if st.button("🆕 Nueva conversación", use_container_width=True):
        nueva_conversacion()
        st.rerun()
    dibujar_carrito()
    dibujar_preferencias()
    st.divider()
    dibujar_historial_conversaciones()
    st.divider()
    st.caption("Compara precios en Vea, Jumbo, Carrefour, ChangoMás y Día.")

st.title("Asistente de compras")

# historial: se redibuja completo en cada rerun, incluidos los pasos de cada turno
for i, turno in enumerate(st.session_state.historial):
    with st.chat_message(turno["rol"]):
        if turno["rol"] == "user":
            st.markdown(md(turno["texto"]))
        else:
            dibujar_turno_asistente(turno, i)

# nombre del modelo junto al cuadro de chat, fijo abajo como en ChatGPT/Claude
# (st.bottom es el contenedor donde Streamlit fija el chat_input)
with st.bottom:
    st.markdown(f"<div style='text-align:right;font-size:0.8rem;opacity:0.6'>{MODELO_ACTUAL}</div>",
                unsafe_allow_html=True)

# ------------------------------------------------------------------ elección de supermercados

# casillas: arrancan todas marcadas y después recuerdan la última elección. Se guarda aparte
# porque Streamlit borra el estado de las casillas cuando la tarjeta deja de mostrarse.
st.session_state.setdefault("ultima_eleccion", list(SUPERMERCADOS))


def _marcar_todos():
    for k in SUPERMERCADOS:
        st.session_state[f"super_{k}"] = st.session_state.super_todos


def _actualizar_todos():
    st.session_state.super_todos = all(st.session_state[f"super_{k}"] for k in SUPERMERCADOS)


def elegir_supermercados() -> list[str] | None:
    """Tarjeta con casillas. Devuelve las claves elegidas al tocar 'Buscar', si no None."""
    for k in SUPERMERCADOS:
        st.session_state.setdefault(f"super_{k}", k in st.session_state.ultima_eleccion)
    st.session_state.setdefault("super_todos", all(st.session_state[f"super_{k}"] for k in SUPERMERCADOS))
    with st.container(border=True):
        st.markdown("**¿En qué supermercados busco?**")
        st.checkbox("Todos", key="super_todos", on_change=_marcar_todos)
        columnas = st.columns(3)
        for i, (clave, adapter) in enumerate(SUPERMERCADOS.items()):
            columnas[i % 3].checkbox(adapter.nombre, key=f"super_{clave}", on_change=_actualizar_todos)
        elegidos = [k for k in SUPERMERCADOS if st.session_state[f"super_{k}"]]
        if not elegidos:
            st.caption("Elegí al menos un supermercado.")
        if st.button("Buscar", type="primary", disabled=not elegidos):
            st.session_state.ultima_eleccion = elegidos
            return elegidos
    return None


# ------------------------------------------------------------------ chat

# el agente pausado por elegir_supermercados: lo hecho hasta ahora + la tarjeta para elegir
if turno_en_curso := st.session_state.en_curso:
    with st.chat_message("assistant"):
        dibujar_turno_asistente(turno_en_curso)
        tarjeta = st.empty()
        with tarjeta.container():
            elegidos = elegir_supermercados()
        if elegidos:
            tarjeta.empty()  # se reemplaza la tarjeta por la continuación en vivo
            terminar_turno(ejecutar(Command(resume=elegidos), turno_en_curso))

pregunta = st.chat_input("Ej: tengo que comprar 2 L de leche y 1 kg de yerba, ¿dónde me conviene?",
                         disabled=bool(st.session_state.en_curso))
pregunta = pregunta or st.session_state.pop("pregunta_programada", None)  # botones como "Optimizar carrito"
if pregunta:
    st.session_state.historial.append({"rol": "user", "texto": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)
    with st.chat_message("assistant"):
        terminar_turno(ejecutar({"messages": [{"role": "user", "content": pregunta}]}))

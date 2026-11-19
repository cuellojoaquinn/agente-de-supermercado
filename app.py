"""UI del agente en Streamlit.

Cubre la consigna:
- 3.4: chat con st.chat_input / st.chat_message, respuestas renderizadas en markdown.
- 3.5: en streaming, cada paso apenas termina: razonamiento (diferenciado, en un expander),
  cada tool con sus argumentos y un resumen del resultado, en orden.
- 3.3: memoria con checkpointer SQLite (thread_id en session_state); las conversaciones
  guardadas se listan en la barra lateral (por fecha, con buscador) y se pueden retomar.
- 4: errores de cuota/proveedor como mensaje claro, sin traceback.

Correr con:  streamlit run app.py
"""
import json
import unicodedata
import uuid
from datetime import date, datetime

import streamlit as st

from agente import crear_agente, listar_conversaciones, mensaje_error
from model import modelos_disponibles, proveedor_default
from supermercados import SUPERMERCADOS
from langgraph.types import Command

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


def resumir_resultado(nombre: str, contenido) -> str:
    """Una línea legible en lugar del JSON completo que recibe el modelo."""
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
        if datos.get("sin_buscar"):
            partes.append(f"⚠️ sin buscar antes: {', '.join(datos['sin_buscar'])}")
        return " · ".join(partes) or "sin resultados"

    return json.dumps(datos, ensure_ascii=False)[:200]


# ------------------------------------------------------------------ dibujo de pasos

def dibujar_paso(paso: dict):
    """Se usa igual en vivo y al redibujar el historial, así ambos se ven idénticos."""
    tipo = paso["tipo"]
    if tipo == "razonamiento":
        with st.expander("🧠 Razonamiento del modelo"):
            st.markdown(paso["texto"])
    elif tipo == "nota":  # texto que el modelo escribe junto con las tool calls
        st.caption(f"💬 {paso['texto']}")
    elif tipo == "tool_call":
        args = ", ".join(f"{k}={v!r}" for k, v in paso["args"].items())
        st.markdown(f"🔧 **{paso['nombre']}**(`{args}`)")
    elif tipo == "tool_result":
        st.markdown(f"✅ `{paso['nombre']}` → {paso['resumen']}")


def dibujar_turno_asistente(turno: dict):
    if turno["pasos"]:
        n_tools = sum(p["tipo"] == "tool_call" for p in turno["pasos"])
        with st.expander(f"🔍 Qué hizo el agente ({n_tools} llamadas a herramientas)"):
            for paso in turno["pasos"]:
                dibujar_paso(paso)
    if turno.get("error"):
        st.error(turno["error"])
    if turno.get("respuesta"):
        st.markdown(turno["respuesta"])


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
        pasos.append({"tipo": "tool_result", "nombre": msg.name,
                      "resumen": resumir_resultado(msg.name, msg.content)})
    return pasos, respuesta


def ejecutar(entrada, turno: dict | None = None) -> dict:
    """Corre el agente en streaming. entrada: la pregunta, o Command(resume=...) para seguir un
    turno pausado. Si el agente se pausa (elegir_supermercados), devuelve el turno con esperando=True."""
    turno = turno or {"pasos": [], "respuesta": None, "error": None}
    turno["esperando"] = False
    with st.status("Pensando...", expanded=True) as estado:
        try:
            for chunk in agente.stream(entrada, config, stream_mode="updates"):
                for nodo, data in chunk.items():
                    if nodo == "__interrupt__":  # una tool pidió la intervención del usuario
                        turno["esperando"] = True
                        continue
                    # una tool que devuelve Command llega como lista de actualizaciones
                    for update in data if isinstance(data, list) else [data]:
                        for msg in (update or {}).get("messages", []):
                            pasos, respuesta = pasos_de_mensaje(nodo, msg)
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
        st.markdown(turno["respuesta"])
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
    dibujar_historial_conversaciones()
    st.divider()
    st.caption("Compara precios en Vea, Jumbo, Carrefour, ChangoMás y Día.")

st.title("Asistente de compras")

# historial: se redibuja completo en cada rerun, incluidos los pasos de cada turno
for turno in st.session_state.historial:
    with st.chat_message(turno["rol"]):
        if turno["rol"] == "user":
            st.markdown(turno["texto"])
        else:
            dibujar_turno_asistente(turno)

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

if pregunta := st.chat_input("Ej: tengo que comprar 2 L de leche y 1 kg de yerba, ¿dónde me conviene?",
                             disabled=bool(st.session_state.en_curso)):
    st.session_state.historial.append({"rol": "user", "texto": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)
    with st.chat_message("assistant"):
        terminar_turno(ejecutar({"messages": [{"role": "user", "content": pregunta}]}))

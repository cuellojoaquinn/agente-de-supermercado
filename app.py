"""UI del agente en Streamlit.

Cubre la consigna:
- 3.4: chat con st.chat_input / st.chat_message, respuestas renderizadas en markdown.
- 3.5: en streaming, cada paso apenas termina: razonamiento (diferenciado, en un expander),
  cada tool con sus argumentos y un resumen del resultado, en orden.
- 3.3: memoria con checkpointer (agente cacheado + thread_id en session_state).
- 4: errores de cuota/proveedor como mensaje claro, sin traceback.

Correr con:  streamlit run app.py
"""
import json
import os
import uuid

import streamlit as st

from agente import crear_agente, mensaje_error

st.set_page_config(page_title="Super-agente", page_icon="🛒")


# ------------------------------------------------------------------ agente y memoria

@st.cache_resource  # una sola instancia: si se recrea en cada rerun, el InMemorySaver se pierde
def get_agente():
    return crear_agente()


def nueva_conversacion():
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.historial = []


if "thread_id" not in st.session_state:
    nueva_conversacion()

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


def ejecutar(pregunta: str) -> dict:
    turno = {"pasos": [], "respuesta": None, "error": None}
    with st.status("Pensando...", expanded=True) as estado:
        try:
            for chunk in agente.stream({"messages": [{"role": "user", "content": pregunta}]},
                                       config, stream_mode="updates"):
                for nodo, data in chunk.items():
                    for msg in (data or {}).get("messages", []):
                        pasos, respuesta = pasos_de_mensaje(nodo, msg)
                        for paso in pasos:
                            dibujar_paso(paso)  # se ve apenas termina cada paso
                            if paso["tipo"] == "tool_call":
                                estado.update(label=f"Usando {paso['nombre']}...")
                        turno["pasos"].extend(pasos)
                        if respuesta:
                            turno["respuesta"] = respuesta
            n_tools = sum(p["tipo"] == "tool_call" for p in turno["pasos"])
            estado.update(label=f"Listo · {n_tools} llamadas a herramientas",
                          state="complete", expanded=False)
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


# ------------------------------------------------------------------ página

with st.sidebar:
    st.header("🛒 Super-agente")
    st.caption("Compara precios en Vea, Jumbo, Carrefour, ChangoMás y Día.")
    proveedor = os.getenv("PROVEEDOR", "gemini")
    modelo = {"opencode": os.getenv("MODELO_OPENCODE"), "lmstudio": os.getenv("MODELO_LOCAL")}.get(
        proveedor, os.getenv("MODELO"))
    st.markdown(f"**Modelo:** `{modelo}` ({proveedor})")
    st.markdown(f"**Conversación:** `{st.session_state.thread_id[:8]}`")
    if st.button("🆕 Nueva conversación", use_container_width=True):
        nueva_conversacion()
        st.rerun()
    st.divider()
    st.markdown("**Probá la memoria:**\n1. *¿Cuánto sale la leche entera por litro?*\n"
                "2. *¿Y si son 3 litros, dónde me conviene?*")

st.title("Asistente de compras")

# historial: se redibuja completo en cada rerun, incluidos los pasos de cada turno
for turno in st.session_state.historial:
    with st.chat_message(turno["rol"]):
        if turno["rol"] == "user":
            st.markdown(turno["texto"])
        else:
            dibujar_turno_asistente(turno)

if pregunta := st.chat_input("Ej: tengo que comprar 2 L de leche y 1 kg de yerba, ¿dónde me conviene?"):
    st.session_state.historial.append({"rol": "user", "texto": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)
    with st.chat_message("assistant"):
        turno = ejecutar(pregunta)
    st.session_state.historial.append({"rol": "assistant", **turno})

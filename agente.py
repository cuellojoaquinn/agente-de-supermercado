from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
import preferencias
from model import get_model
from langchain_google_genai.chat_models import GoogleAPIError, GoogleRateLimitError
from tools import (Contexto, EstadoAgente, buscar_productos, cargar_skill, descartar_productos, elegir_supermercados,
                   generar_plan_compra,
                   guardar_preferencia, optimizar_carrito, optimizar_compra, quitar_preferencia)
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver

SYSTEM_PROMPT = """Sos un asistente que ayuda a ahorrar en compras de supermercado en Argentina.
- Nunca inventes precios: obtenelos siempre con las herramientas.
- Compará por precio unitario ($/L, $/kg), no por precio de góndola.
- Descartá resultados cuya categoría no corresponda a lo que pidió el usuario, y registralos
  siempre con descartar_productos (si no, se le siguen ofreciendo en la lista del carrito).
- Si el usuario no dijo en qué supermercados buscar y todavía no los eligió en esta
  conversación, preguntale con elegir_supermercados antes de buscar.
- Si no dice el tipo, elegí la variante común (leche entera, aceite de girasol, yerba con palo,
  pan francés o de mesa), no la más barata de otro tipo, y decí cuál elegiste.
- Buscá primero con las palabras del usuario (búsqueda general). Al final de la respuesta,
  ofrecele afinar: algo más específico (tipo, marca, tamaño, según las variantes encontradas)
  o quedarse con lo más barato.
- Si pide precios o el total de una compra de varios productos, buscá cada uno y calculá
  con optimizar_compra: no sumes ni multipliques precios vos. Vale también para la lista que
  armaste con la skill cantidades: si el usuario pide "los precios" o "cuánto sale", buscá
  y llamá a optimizar_compra; no calcules costos de una cantidad (ej: 5 kg de una bolsa de 10 kg).
- Después de optimizar_compra, ofrecé el plan de compra en un archivo. Si el usuario confirma
  la opción o lo pide, generalo con generar_plan_compra (mismos items y supermercados).
  Si armó el carrito a mano y pide el plan, usá opcion="carrito": tiene que coincidir con su carrito.
- Si pide optimizar o revisar su carrito, usá optimizar_carrito. Si una compra reparte
  en varios supermercados por poco ahorro, decíselo y proponé ir a menos.
- Tenés skills con conocimiento del dominio (cargar_skill). Cargá la que corresponda
  antes de actuar: armar_carrito para optimizar compras, marcas para comparar marcas o
  armar una versión económica, cantidades si pide algo general ("asado para 10") en vez
  de una lista, leer_resultados antes de comparar precios. Cada una, una vez por conversación.
- Cuando exprese gustos o restricciones (marcas, marcas propias, otras condiciones),
  guardalos con guardar_preferencia y respetalos.
- Respondé en español, con montos en pesos ($1.890). Usá markdown simple: nada de
  LaTeX ni fórmulas (\\rightarrow, $$...$$); para flechas usá →. No escribas links ni
  URLs que no te haya dado una herramienta: el plan se descarga con el botón que muestra la
  pantalla debajo de tu respuesta."""

# Nos pide poder mostrar una memoria corto plazo. Corto plazo: solamente consultar mensajes de esta conversacion.
# El problema: es que no persisto y no las puedo retomar. checkpoint con sql lite.
# Memoria persistente: un checkpoint por paso del grafo, separado por thread_id.
# check_same_thread=False porque Streamlit atiende cada rerun en un hilo distinto.
checkpointer = SqliteSaver(sqlite3.connect("checkpoints.db", check_same_thread=False))
checkpointer.setup()  # crea las tablas si la base es nueva


@dynamic_prompt
def prompt_con_preferencias(request: ModelRequest) -> str:
    """Agrega al system prompt las preferencias vigentes (largo plazo + esta conversación)."""
    prefs = preferencias.efectivas(request.state, request.runtime.store)
    if not prefs:
        return SYSTEM_PROMPT
    lineas = [f"- {preferencias.etiqueta(p)} ({'siempre' if p['alcance'] == 'siempre' else 'esta conversación'})"
              for p in prefs]
    return SYSTEM_PROMPT + "\n\nPreferencias del usuario (respetalas):\n" + "\n".join(lineas)


def crear_agente(proveedor: str | None = None):
    return create_agent(
        model=get_model(proveedor),
        tools=[elegir_supermercados, buscar_productos, descartar_productos, optimizar_compra,
               optimizar_carrito, guardar_preferencia, quitar_preferencia, cargar_skill,
               generar_plan_compra],
        middleware=[prompt_con_preferencias],  # system prompt + preferencias vigentes
        checkpointer=checkpointer,
        state_schema=EstadoAgente,  # guarda supermercados y preferencias junto a los mensajes
        store=preferencias.store,   # memoria de largo plazo (memoria.db)
        context_schema=Contexto,    # la UI pasa el carrito en cada mensaje
    )


def listar_conversaciones() -> list[str]:
    """thread_id guardados, de la conversación más reciente a la más vieja."""
    with checkpointer.lock:  # la conexión es compartida con el agente
        filas = checkpointer.conn.execute(
            "SELECT thread_id FROM checkpoints GROUP BY thread_id ORDER BY MAX(checkpoint_id) DESC"
        ).fetchall()
    return [fila[0] for fila in filas]


def mensaje_error(e: Exception) -> str | None:
    """Traduce errores de Gemini a un mensaje claro para el usuario (None = no es de Gemini)."""
    if isinstance(e, GoogleRateLimitError):  # 429, ya agotó los reintentos con espera
        # Gemini nombra la cuota diaria como "...PerDay..." (sin espacio)
        if "perday" in str(e).lower().replace(" ", ""):
            return "⚠️ Se agotó la cuota diaria del modelo. Cambiá de modelo o de API key."
        return "⚠️ Límite por minuto alcanzado. Esperá unos segundos y reintentá."
    if isinstance(e, GoogleAPIError):  # 503 saturado / 504 timeout del lado de Google
        return "⚠️ El modelo está saturado o no respondió a tiempo. Reintentá en unos segundos."
    try:
        import openai  # proveedores compatibles con OpenAI (LM Studio, OpenCode Zen)
        if isinstance(e, openai.APITimeoutError):  # va primero: es subclase de APIConnectionError
            return "⚠️ El modelo local tardó demasiado. Probá con un modelo más chico o una pregunta más simple."
        if isinstance(e, openai.RateLimitError):  # 429 de OpenCode Zen u otro proveedor compatible
            return "⚠️ Límite de uso del proveedor alcanzado. Revisá tu saldo o plan, o cambiá de PROVEEDOR en el .env."
        if isinstance(e, openai.APIStatusError) and e.status_code == 402:
            return "⚠️ El proveedor no tiene saldo. Cargá crédito en su consola o cambiá de PROVEEDOR en el .env."
        if isinstance(e, openai.APIConnectionError):
            return "⚠️ No hay conexión con LM Studio. Abrilo, cargá el modelo y encendé el servidor local (puerto 1234)."
    except ImportError:
        pass
    return None

from langchain.agents import create_agent
from model import get_model
from langchain_google_genai.chat_models import GoogleAPIError, GoogleRateLimitError
from tools import buscar_productos, optimizar_compra
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver

SYSTEM_PROMPT = """Sos un asistente que ayuda a ahorrar en compras de supermercado en Argentina.
- Nunca inventes precios: obtenelos siempre con las herramientas.
- Compará por precio unitario ($/L, $/kg), no por precio de góndola.
- Descartá resultados cuya categoría no corresponda a lo que pidió el usuario.
- Respondé en español, con montos en pesos."""

# Nos pide poder mostrar una memoria corto plazo. Corto plazo: solamente consultar mensajes de esta conversacion.
# El problema: es que no persisto y no las puedo retomar. checkpoint con sql lite.
# Memoria persistente: un checkpoint por paso del grafo, separado por thread_id.
# check_same_thread=False porque Streamlit atiende cada rerun en un hilo distinto.
checkpointer = SqliteSaver(sqlite3.connect("checkpoints.db", check_same_thread=False))
checkpointer.setup()  # crea las tablas si la base es nueva


def crear_agente(proveedor: str | None = None):
    return create_agent(
        model=get_model(proveedor),
        tools=[buscar_productos, optimizar_compra],
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
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

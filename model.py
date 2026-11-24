import os
import uuid
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()


def _modelo_gemini(modelo: str):
    return ChatGoogleGenerativeAI(
        model=modelo,
        max_retries=2,          # reintenta con backoff ante 429/5xx
        timeout=60,             # Gemini lo usa como plazo del servidor: con 8 tools + razonamiento, 15 s daba 504
        thinking_level="low",   # menos latencia por paso del agente
        include_thoughts=True,  # sin esto Gemini razona pero no devuelve el bloque 'reasoning'
    )


def _modelo_compatible(modelo: str, base_url: str, api_key: str, nombre: str, **kwargs):
    """Cualquier API compatible con OpenAI: LM Studio (local), OpenCode Zen, OpenRouter..."""
    from langchain_openai import ChatOpenAI

    class ChatCompatible(ChatOpenAI):
        """Estos servidores mandan el razonamiento en `reasoning_content`, que ChatOpenAI
        descarta. Lo copiamos a additional_kwargs: LangChain lo expone como bloque 'reasoning'."""

        def _create_chat_result(self, response, generation_info=None):
            result = super()._create_chat_result(response, generation_info)
            datos = response if isinstance(response, dict) else response.model_dump()
            for gen, choice in zip(result.generations, datos.get("choices") or []):
                razonamiento = (choice.get("message") or {}).get("reasoning_content")
                if razonamiento:
                    gen.message.additional_kwargs["reasoning_content"] = razonamiento
                # con proveedor "openai" LangChain usa un traductor que ignora reasoning_content;
                # con uno sin traductor propio aplica la extracción genérica, que sí lo toma
                gen.message.response_metadata["model_provider"] = nombre
            return result

    return ChatCompatible(model=modelo, base_url=base_url, api_key=api_key,
                          temperature=0.3, **kwargs)


def modelos_disponibles() -> dict[str, str]:
    """Un modelo por proveedor, según el .env: {proveedor: modelo}."""
    return {
        "gemini": os.getenv("MODELO", "gemini-3.8-flash"),
        "opencode": os.getenv("MODELO_OPENCODE", "deepseek-v4.1-flash-free"),
        "lmstudio": os.getenv("MODELO_LOCAL", "qwen/qwen3.5-9b"),
    }


def proveedor_default() -> str:
    return os.getenv("PROVEEDOR", "gemini").lower()


def get_model(proveedor: str | None = None):
    """Sin argumento usa PROVEEDOR del .env."""
    proveedor = (proveedor or proveedor_default()).lower()
    modelo = modelos_disponibles()[proveedor] if proveedor in modelos_disponibles() else None

    if proveedor == "lmstudio":
        print(f"🤖 usando {modelo} (local, LM Studio)")
        return _modelo_compatible(
            modelo,
            base_url=os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1"),
            api_key="lm-studio",   # LM Studio no la valida, pero el cliente exige una
            nombre="lmstudio",
            timeout=300,           # local es más lento: un paso largo puede tardar minutos
            max_retries=0,         # si el servidor no está, reintentar no sirve
        )

    if proveedor == "opencode":
        print(f"🤖 usando {modelo} (OpenCode Zen)")
        return _modelo_compatible(
            modelo,
            base_url=os.getenv("OPENCODE_URL", "https://opencode.ai/zen/v1"),
            api_key=os.environ["OPENCODE_API_KEY"],
            nombre="opencode",
            timeout=90,
            max_retries=2,         # reintenta con backoff ante 429/5xx
            # OpenCode Go pide un ID de sesión estable y un user agent propio
            # (https://opencode.ai/v2/docs/console/go/). Una sesión por instancia del modelo.
            default_headers={
                "x-opencode-session": str(uuid.uuid4()),
                "User-Agent": "super-agente/0.1",
            },
        )

    modelo = modelos_disponibles()["gemini"]
    print(f"🤖 usando {modelo}")          # para confirmar que el .env no lo pisa
    return _modelo_gemini(modelo)


if __name__ == "__main__":
    modelo = get_model()
    respuesta = modelo.invoke("Respondé únicamente: Hola")
    print(respuesta.text)

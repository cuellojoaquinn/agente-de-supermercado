"""
Elegimos gemini-3.8-flash porque soporta tool calling (requisito), expone bloques de razonamiento para mostrar en la UI, tiene 1M de tokens de contexto y su tier gratuito alcanza para el desarrollo y la demo. Preferimos un modelo Flash sobre uno Pro por latencia y cuota, ya que cada tarea del agente requiere varias llamadas al modelo.
"""

from google import genai

client = genai.Client()  # usa GOOGLE_API_KEY del entorno

for m in client.models.list():
    if "generateContent" in (m.supported_actions or []):
        print(f"{m.name:45} | thinking={m.thinking} | entrada={m.input_token_limit} tokens")
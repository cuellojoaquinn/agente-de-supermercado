from agente import crear_agente, mensaje_error
import sys
import time

agente = crear_agente()
config = {"configurable": {"thread_id": "prueba-1"}}
# uso: python -m exploracion.probar_agente "tu pregunta"  (sin argumento usa esta)
pregunta = " ".join(sys.argv[1:]) or "Necesito hacer un asado para 2 personas con 20 mil pesos"

inicio = time.time()
print(f"👤 {pregunta}\n⏳ esperando al modelo...\n", flush=True)

try:
    for chunk in agente.stream({"messages": [{"role": "user", "content": pregunta}]},
                               config, stream_mode="updates"):
        for nodo, data in chunk.items():
            print(f"[{time.time() - inicio:5.1f}s] terminó el nodo '{nodo}'", flush=True)
            for msg in data["messages"]:
                if nodo == "model":
                    print("   tipos de bloque:", [b.get("type") for b in msg.content_blocks])
                    print("   uso de tokens:", msg.usage_metadata)
                    for bloque in msg.content_blocks:
                        if bloque["type"] == "reasoning":
                            print(f"🧠 Razonamiento: {bloque.get('reasoning', '')[:300]}\n")
                    for tc in msg.tool_calls:
                        print(f"🔧 Llama a {tc['name']} con {tc['args']}\n")
                    if msg.text and not msg.tool_calls:
                        print(f"🤖 {msg.text}")
                elif nodo == "tools":
                    print(f"✅ Resultado de {msg.name}: {str(msg.content)[:200]}...\n")
except Exception as e:
    mensaje = mensaje_error(e)
    if mensaje is None:
        raise  # error que no es de Gemini: mostrar el traceback completo
    print(f"\n{mensaje}\n   (detalle: {type(e).__name__}: {str(e)[:150]})")

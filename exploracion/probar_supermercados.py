import time
from adapters import VTEXAdapter

CANDIDATOS = {
    "Jumbo": "https://www.jumbo.com.ar",
    "Disco": "https://www.disco.com.ar",
    "Carrefour": "https://www.carrefour.com.ar",
    "ChangoMás": "https://www.masonline.com.ar",
    "Día": "https://diaonline.supermercadosdia.com.ar",
}

TEXTO = "leche entera 1 litro"

print(f"Buscando '{TEXTO}' en {len(CANDIDATOS)} supermercados...\n", flush=True)

for nombre, url in CANDIDATOS.items():
    adapter = VTEXAdapter(nombre, url)
    try:
        t0 = time.time()
        productos = adapter.buscar(TEXTO, limite=5)
        print(f"✅ {nombre}: {len(productos)} productos en {time.time() - t0:.1f}s", flush=True)
        for p in productos[:3]:
            print(f"     {p.nombre} | ${p.precio} | {p.precio_unitario} $/{p.unidad}", flush=True)
    except Exception as e:
        print(f"❌ {nombre}: {type(e).__name__}: {str(e)[:120]}", flush=True)
    time.sleep(1)  # pausa entre supermercados para no gatillar bloqueos
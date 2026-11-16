from adapters import VTEXAdapter


SUPERMERCADOS = {
    "vea": VTEXAdapter("Vea", "https://www.vea.com.ar"),
    "jumbo": VTEXAdapter("Jumbo", "https://www.jumbo.com.ar"),
    "carrefour": VTEXAdapter("Carrefour", "https://www.carrefour.com.ar"),
    "changomas": VTEXAdapter("ChangoMás", "https://www.masonline.com.ar"),
    "dia": VTEXAdapter("Día", "https://diaonline.supermercadosdia.com.ar"),
    # Disco descartado: mismos precios que Jumbo (ambos Cencosud, verificado 13/11)
}
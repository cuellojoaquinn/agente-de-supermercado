import json
from urllib.parse import urlencode, quote
import requests
from .base import SupermercadoAdapter, Producto
import re


# Unidades de VTEX → (unidad base, factor de conversión)
# Solo vimos "cc." en datos reales; el resto hay que verificarlo con otros productos.
UNIDADES = {
    "cc": ("ml", 1), "ml": ("ml", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000),
    "litro": ("ml", 1000), "litros": ("ml", 1000),
    "gr": ("g", 1), "grs": ("g", 1), "g": ("g", 1),
    "kg": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000),
    "un": ("un", 1),
    "mt": ("m", 1), "m2": ("m2", 1),
}

REGEX_PRESENTACION = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(litros?|lts?|l|kilos?|kg|grs?|g|cc|ml)\b", re.IGNORECASE
)
def _normalizar_unidad(u: str | None) -> str:
    return (u or "").strip().lower().rstrip(".")


MAX_ENVASE = 50_000  # 50 kg o 50 L en unidad base: ningún envase de súper pesa más


def _convertir(cantidad: float, unidad: str) -> tuple[float, str]:
    """Pasa a unidad base (ml, g, un...) corrigiendo errores de carga del catálogo.
    Ej. real de ChangoMás: "Yerba Nobleza Gaucha Cimarrón 500 Kg" es 500 g, no 500 kilos:
    si en kg/L el resultado es absurdo para un súper, asumimos que quisieron decir g/ml."""
    base, factor = UNIDADES[unidad]
    contenido = cantidad * factor
    if factor == 1000 and contenido > MAX_ENVASE:
        contenido = cantidad
    return contenido, base

class VTEXAdapter(SupermercadoAdapter):
    """Adaptador para tiendas que usan la Legacy Search API de VTEX."""

    ENDPOINT = "/api/catalog_system/pub/products/search"
    MAX_POR_PAGINA = 50  # VTEX no permite más de 50 por request (_to - _from <= 49)

    def __init__(self, nombre: str, base_url: str):
        super().__init__(nombre)
        self.base_url = base_url.rstrip("/")

    def buscar_crudo(self, texto: str, limite: int = 3, ordenar_por_precio: bool = False) -> dict:
        limite = max(1, min(limite, self.MAX_POR_PAGINA))
        params = {"ft": texto, "_from": 0, "_to": limite - 1}
        if ordenar_por_precio:
            params["O"] = "OrderByPriceASC"
        query = urlencode(params, quote_via=quote)

        r = requests.get(f"{self.base_url}{self.ENDPOINT}?{query}", timeout=10)
        if not r.ok:
            raise RuntimeError(f"{self.nombre} respondió {r.status_code}: {r.text[:300]}")
        return {
            "status": r.status_code,
            "url": r.url,
            "total": r.headers.get("resources"),
            "productos": r.json(),
        }
    def buscar(self, texto: str, limite: int = 10) -> list[Producto]:
        crudo = self.buscar_crudo(texto, limite)
        productos = []
        for p in crudo["productos"]:
            try:
                productos.append(self._normalizar(p))
            except Exception as e:
                # un producto mal formado no rompe toda la búsqueda
                print(f"[{self.nombre}] producto {p.get('productId')} descartado: {e}")
        return productos

    def _normalizar(self, p: dict) -> Producto:
        item = p["items"][0]
        oferta = item["sellers"][0]["commertialOffer"]

        contenido, unidad, por_peso = self._presentacion(p, item["itemId"])
        precio = float(oferta["Price"])

        return Producto(
            supermercado=self.nombre,
            sku_id=item["itemId"],
            ean=item.get("ean") or None,
            nombre=p["productName"],
            marca=p.get("brand"),
            categoria=(p.get("categories") or [None])[0],
            precio=precio,
            precio_sin_descuento=float(oferta.get("PriceWithoutDiscount") or precio),
            disponible=bool(oferta.get("IsAvailable")) and precio > 0,
            contenido=contenido,
            unidad=unidad,
            precio_unitario=self._precio_unitario(precio, contenido, unidad),
            link=p.get("link"),
            venta_por_peso=por_peso,
        )

    def _presentacion(self, p: dict, item_id: str) -> tuple[float | None, str | None, bool]:
        """Presentación: (contenido en unidad base, unidad base, venta_por_peso)."""
        datos = {}
        raw = (p.get("SkuData") or [None])[0]
        if raw:
            datos = json.loads(raw).get(item_id, {})

        # Caso 1: venta por peso → Price es el precio por kilo
        # (verificado: Queso Cremoso Barraza, web "Precio regular x kg.: $11.500" = API Price)
        if _normalizar_unidad(datos.get("measurement_unit")) == "kg":
            return 1000.0, "g", True

        # Caso 2: presentación estructurada en SkuData
        unidad = _normalizar_unidad(datos.get("measurement_unit_un"))
        cantidad = datos.get("unit_multiplier_un")
        nombre = p.get("productName", "")
        if unidad in UNIDADES and cantidad:
            contenido, base = _convertir(float(cantidad), unidad)
            # "1 un" no informa nada útil: si el nombre trae peso o volumen, usamos eso
            # (verificado: Carne Picada de Cerdo 500 Grs → SkuData "1 un", web "Precio x un: $5.990")
            if not (base == "un" and float(cantidad) == 1 and REGEX_PRESENTACION.search(nombre)):
                return contenido, base, False

        # Caso 3: respaldo, extraer del nombre
        m = REGEX_PRESENTACION.search(nombre)
        if m:
            cantidad = float(m.group(1).replace(",", "."))
            contenido, base = _convertir(cantidad, _normalizar_unidad(m.group(2)))
            return contenido, base, False

        print(f"[{self.nombre}] sin presentación: {nombre}")
        return None, None, False

    @staticmethod
    def _precio_unitario(precio: float, contenido: float | None, unidad: str | None) -> float | None:
        """$ por litro (ml), por kilo (g) o por unidad."""
        if not contenido or not unidad or precio <= 0:
            return None
        if unidad in ("ml", "g"):
            return round(precio / contenido * 1000, 2)
        return round(precio / contenido, 2)
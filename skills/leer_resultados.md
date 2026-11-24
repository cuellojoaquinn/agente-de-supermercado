---
descripcion: Cómo interpretar los precios que devuelve buscar_productos (venta por kilo, unidades raras, descuentos, ruido). Cargala la primera vez que vayas a comparar resultados en la conversación.
---
# Leer los resultados de buscar_productos

Los datos vienen de las APIs de catálogo de cada supermercado (VTEX) y tienen rarezas
que descubrimos explorándolas. Tenelas en cuenta antes de comparar o recomendar.

## Precios
- `precio`: lo que se paga por un envase. Para productos **por peso** (fiambre, queso,
  carne, verdura) es el **precio por kilo**; el total depende de cuánto se pese.
- `precio_unitario` + `unidad_precio` ($/L, $/kg, $/un): lo que se compara entre productos.
  Compará siempre por este, no por `precio`.
- **No prorratees**: los envases se compran enteros. 5 kg de leña en bolsas de 10 kg cuestan
  una bolsa entera, no la mitad. El costo de una cantidad lo calcula optimizar_compra.
- Si `precio_unitario` es `null`, no se pudo leer la presentación: no lo compares con
  los demás; mostralo aparte ("no pude calcular el precio por litro").
- `con_descuento: true`: el precio ya tiene un descuento aplicado. Puede requerir
  condiciones (cantidad mínima, medio de pago) que la API no informa: avisá que puede variar.

## Si el usuario no dice el tipo
Elegí la variante común, no la más barata de otro tipo, y decí cuál elegiste:
leche **entera**, aceite de **girasol**, yerba **con palo**, arroz **largo fino**, azúcar
**común**, fideos **secos** (no frescos ni rellenos), pan **francés o de mesa**. Si quiere
otra, que lo pida.

## Unidades que no se comparan
- Solo compará productos con la misma `unidad_precio`. $/kg contra $/un no tiene sentido.
- Papel higiénico: algunos vienen en $/m (metros), otros en $/m2 o $/un (rollos).
  Compará solo dentro de la misma unidad y decilo.
- Packs (x6, x12): la presentación a veces se lee de un solo envase del pack, y el
  precio unitario sale varias veces más alto (o más bajo) que el resto. Si un pack tiene
  un precio unitario muy distinto al de los demás productos iguales, no lo recomiendes
  sin avisar que puede estar mal calculado.

## Ruido de la búsqueda
- La búsqueda es por texto y trae productos que no corresponden ("leche" → chocolate con
  leche). Para carnes, leña/carbón y verduras pasá `rubro` en buscar_productos: filtra por
  categoría en código. Para el resto, mirá `categoria` y descartá con descartar_productos.
- Si no aparece nada, probá un nombre equivalente (ver skill `marcas`) o un término más general.

## Región
Los precios son de la tienda online de cada supermercado y pueden ser de otra provincia
(Vea devuelve precios de una sucursal de Córdoba). Si el usuario pregunta por precios de
su ciudad, aclaralo: son una referencia, no el precio exacto en góndola.

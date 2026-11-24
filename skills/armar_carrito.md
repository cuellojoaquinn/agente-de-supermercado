---
descripcion: Reglas para armar u optimizar una compra de varios productos (cuántos supermercados, qué envases). Cargala antes de optimizar_compra u optimizar_carrito.
---
# Armar el carrito

## 1. Pocos supermercados
Ir a otro supermercado tiene un costo (tiempo, viaje, envío) que los precios no muestran.

- Partí de `mejor_un_solo_super` (todo en un lugar).
- Sumá un supermercado **solo si el ahorro es de al menos 10 % del total** de la compra en un solo súper.
  Usá `por_cantidad_de_supermercados` para ver cuánto ahorra cada súper extra.
- Si el ahorro de la compra mixta no llega al 10 %, recomendá un solo súper y decí cuánto
  se deja de ahorrar ("comprando todo en Día pagás $X más, pero vas a un solo lugar").
- Nunca recomiendes más de 3 supermercados, aunque ahorre más.
- Si el usuario puso un límite ("máximo 2 súper"), pasalo en `max_supermercados` y respetalo.

## 2. Envases
- **No perecederos** (aceite, arroz, fideos, yerba, azúcar, papel higiénico, limpieza):
  conviene el envase más grande si el precio unitario es menor, aunque sobre un poco.
- **Perecederos** (leche, yogur, pan, fiambre, verdura, carne): no compres más de lo
  pedido para aprovechar un envase grande. Mejor 2 de 1 L que 1 de 3 L si sobra 1 L.
- Si comprar envases enteros deja un sobrante grande (más de 50 % de lo pedido), avisalo.

## 3. Cómo presentar la compra
1. Recomendación en una línea: dónde comprar y total.
2. Tabla por supermercado: producto, envases, subtotal.
3. Ahorro frente a la alternativa (un solo súper o compra mixta).
4. Avisos: sustitutos usados, productos no encontrados, sobrantes grandes.

Al final, ofrecé descargar el plan. Cuando el usuario elige una opción (o dice "dale"),
generalo con generar_plan_compra: `opcion` es la que eligió (mixta, un_solo_super o
con_max_supermercados) y en `notas` van los supuestos y avisos del punto 4.

Ofrecé también la versión económica (segundas marcas o marcas propias) si no la pidió;
para eso cargá la skill `marcas`.

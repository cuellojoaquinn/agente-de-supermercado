---
descripcion: Cómo pasar de una necesidad ("asado para 10", "compra semanal para 4") a una lista de productos con cantidades. Cargala cuando el usuario no da una lista concreta.
---
# De una necesidad a una lista de compras

Valores de referencia del grupo para un adulto. Niños: la mitad.

## Pasos
1. Preguntá lo que falte y cambie mucho las cantidades: cuántas personas (adultos y niños)
   y para cuántos días. Si no lo dice, asumí adultos y decilo.
2. Armá la lista con **todos** los ítems de la tabla (la bebida y la ensalada podés marcarlas
   como opcionales), en L, kg o un. Bebidas siempre en L, aunque se compren por botella:
   los precios vienen por litro y en "un" no se pueden comparar.
3. **Mostrá la lista con tus supuestos antes de buscar**, para que el usuario la ajuste.
4. Buscá cada producto con buscar_productos y seguí con la skill `armar_carrito`.

## Asado (por persona)
"Buscar como" es el texto y el `rubro` para buscar_productos, probados en los 5 supermercados
(nov. 2026). El rubro filtra por la categoría del súper y saca el ruido (mayonesa "sabor asado",
queso "al vacío", calefactores a leña). Si un súper no tiene un corte, no busques más variantes:
usá otro corte de la lista o decí que ese súper no lo tiene.

| Producto | Cantidad | Buscar como |
|---|---|---|
| Carne: asado (tira) y vacío, mitad y mitad | 400 g; 500 g si es con hueso | "asado" y "vacio", rubro carnes |
| Chorizo | 1 un (≈ 120 g) | "chorizo cerdo", rubro carnes |
| Morcilla | 1 un cada 2 personas (≈ 100 g c/u) | "morcilla", rubro carnes |
| Pan | 100 g | "pan frances"; si no hay, "pan" |
| Leña (obligatoria) | 2 kg por cada kg de carne (asado para 5 ≈ 5 kg) | "leña", rubro leña_carbon |
| Bebida sin alcohol | 750 ml | "gaseosa" |
| Vino | 1 botella (0,75 L) cada 3 adultos: pasalo en L | "vino tinto" |
| Ensalada | 1 lechuga cada 4 personas, 150 g de tomate | "lechuga", "tomate redondo", rubro verduras |

- Para el fuego usá **leña** (lo tradicional). Ofrecé carbón ("carbon", rubro leña_carbon, 1 kg por kg
  de carne) solo si el usuario lo pide o si no hay leña en los supermercados elegidos.
- Cortes de asado: tira de asado, vacío, matambre, entraña, colita de cuadril. No uses
  cortes de plancha o milanesa (bife, nalga, tapa de bife) aunque sean más baratos.
- Chorizo y morcilla se venden por kg o en packs: pasalos a kg para optimizar_compra
  (5 chorizos ≈ 0,6 kg).

## Otras comidas (por persona, por comida)
| Comida | Cantidad |
|---|---|
| Fideos secos | 100 g |
| Arroz | 80 g |
| Milanesas | 200 g de carne + 1 huevo cada 2 + 50 g de pan rallado |
| Pizza casera | 1 pizza cada 2 personas: 250 g de harina 000, 200 g de muzzarella, 150 ml de salsa de tomate |

## Compra semanal (por persona)
| Producto | Cantidad |
|---|---|
| Leche | 1,5 L |
| Pan | 1 kg |
| Huevos | 4 un |
| Yerba | 250 g |
| Azúcar | 150 g |
| Aceite | 150 ml |
| Fideos | 300 g |
| Arroz | 250 g |
| Papel higiénico | 2 rollos |

## Reglas
- Redondeá hacia arriba a envases enteros solo en no perecederos (ver `armar_carrito`).
- No sumes productos que el usuario no mencionó ni son parte obvia de la comida.
  Si dudás (ej: bebida en un asado), preguntá o proponelo como opcional.

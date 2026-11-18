# Mejora del buscador de ecomodico.com: qué queremos y qué hay que adaptar

Acompaña a `buscador.py` (en esta misma carpeta). Ese archivo es un **mock**: muestra el comportamiento que queremos
para el buscador de la tienda, pero fue escrito **sin acceso al backend**, solo viendo lo que el navegador muestra.
Por eso hay partes que sirven tal cual (la lógica de búsqueda) y partes que hay que reemplazar por lo real (de dónde
salen los productos y dónde se guardan las reglas).

`python buscador.py` corre un chequeo rápido con productos de ejemplo (necesita `rapidfuzz`).

## 1. Por qué existe

El buscador actual busca por coincidencia de texto. En la práctica:

| Búsqueda del cliente | Hoy | Queremos |
|---|---|---|
| `cocacola` | 0 resultados | Coca-Cola (entiende palabras pegadas) |
| `coca` | mezcla Coca-Cola con Glade Coco | solo lo que contiene "coca" |
| `lehce`, `magensio` | 0 resultados | corrige el tipeo y avisa "Mostrando resultados de leche" |
| `champu`, `tele`, `birra` | 0 o mal | sinónimos argentinos: shampoo, televisor, cerveza |
| `leche` | primero galletitas "con leche" | primero la categoría Leches |
| `sal` (mientras escribe) | salsas, salmón, saladix | primero la sal |
| `bebidas`, `limpieza` | irrelevante | lo más vendido de esa categoría |
| `supermercado online`, `coto` | 0 resultados | lo más vendido de la tienda |

Además: orden por relevancia, stock, más vendidos y descuento; precio sin impuestos nacionales (Ley 27.743).

## 2. Limitaciones con las que se desarrolló (y qué cambia en la versión real)

Todo esto se hizo desde afuera, con acceso únicamente al front de la tienda. Entonces:

| Qué hizo el prototipo | Por qué | Qué esperamos en la versión real |
|---|---|---|
| Lee los productos **bajando las páginas públicas** de la tienda y extrayendo el JSON que trae cada una (`window.__SERVER_DATA__`), y usa un endpoint no documentado de "más vendidos" | No había otra forma de ver el catálogo | Leer el catálogo de su origen real (base o servicio interno). En el mock eso es `FuenteEjemplo.productos()`: devuelve productos con el contrato de datos del docstring de `buscador.py` |
| Para probarlo en la tienda real, el prototipo tiene un script de front (desplegable y página de resultados) que se engancha a la barra por selectores CSS y a internos de Vue | Es lo único accesible desde afuera | **El front no se entrega**: la interfaz (estilo, tarjetas, filtros) la resuelve quien despliega con el diseño de la tienda, usando la respuesta de `buscar()` (sección 3) |
| Las reglas de sinónimos, frases y categorías están escritas en el código | Es un prototipo | Que vivan en la base y se editen sin redeploy (sección 4) |
| Estado en variables globales, un solo proceso | Simplicidad | Un objeto por worker, índice compartido o reconstruido al arrancar |
| Lo que se registra de las búsquedas, la IA opcional, el chat "Modi" | Parte del prototipo mayor | **Fuera de este pedido**: no se incluye en el mock |

El código de la lógica de búsqueda (normalizar, sinónimos, tipeo, ranking) **no depende de nada de lo anterior**:
recibe una lista de productos y responde consultas.

## 3. Cómo busca (la intención, paso a paso)

1. **Normaliza**: minúsculas, sin tildes, `omega3 → omega 3`, `400ml → 400 ml`, unidades (`gr → g`, `lts → l`).
2. **Frases** (`papel de baño → papel higienico`) y **sinónimos** (`champu → shampoo`). Se descartan palabras vacías
   (`quiero`, `para`), genéricas (`online`, `delivery`, `supermercado`) y de oferta (`promo`, `descuento`, que activan el modo ofertas).
3. **Raíz liviana** para que singular y plural sean lo mismo (`leches`/`leche`, `pañales`/`pañal`).
4. **Palabras pegadas**: `cocacola → coca cola`, solo si las dos mitades existen en el catálogo.
5. **Tipeo**: `lehce → leche` (distancia de edición 1, o 2 si la palabra es larga; misma primera letra),
   **solo si la palabra no existe**. Por eso `coca` nunca trae `coco`.
6. **Mientras escribe**: la última palabra se completa por prefijo (`lec → leche`), pero solo si la palabra tal cual
   casi no existe en el catálogo (si hay 5 o más productos con "sal", no se completa a "salsa").
7. **Categoría entera**: si la búsqueda es una categoría o un alias (`bebidas`, `limpieza`), se muestra lo más
   vendido de esa categoría. Si es genérica (`supermercado online`, `coto`), lo más vendido de la tienda.
8. **Ranking**: pesa más el nombre que la marca, la categoría o los atributos; suma si la categoría coincide
   (+3, y +2,5 si es exacta); desempata con stock, más vendido, orden en la tienda y descuento.
9. Si ningún producto tiene todas las palabras, se aceptan los que tienen la mayoría.
10. Responde los filtros de la página de resultados: categorías y marcas con su cantidad, cantidad en oferta, orden
    (relevancia, precio, descuento, nombre) y paginación. Las respuestas repetidas se cachean 15 minutos.

La respuesta de `buscar(...)` tiene: `consulta`, `corregida`, `titulo`, `url_todos`, `total`, `productos[]`,
`categorias[]`, `marcas[]`, `ofertas`, `via`.

## 4. Reglas dinámicas: sinónimos, frases y categorías en la base de datos

**Queremos que estas tres entidades se puedan modificar en vivo y queden guardadas en la base**, sin tocar código ni
desplegar. Hoy son diccionarios en el código; en el mock son la tabla `reglas` (`ReglasBD`, de juguete sobre SQLite):

| tipo | clave | valor | ejemplo |
|---|---|---|---|
| `sinonimos` | palabra que escribe el cliente | lista de palabras del catálogo (**una palabra** cada una) | `champu → [shampoo]`, `tele → [televisor, tv]` |
| `frases` | frase completa | frase de reemplazo | `papel de bano → papel higienico` |
| `categorias` | búsqueda que equivale a una categoría | lista de categorías del catálogo (nombre exacto) | `limpieza → [Cuidado Del Hogar]` |

Cómo está resuelto en el mock (para que se vea la intención):

- `guardar_regla(tipo, clave, valor)`, `borrar_regla(tipo, clave)` y `listar_reglas(tipo)` son lo que llamaría una
  pantalla de edición. `guardar_regla` valida, guarda y **aplica al instante** (y vacía el caché de resultados).
- Validaciones: se normaliza lo que se escribe igual que el buscador; un sinónimo es una sola palabra (si el cliente
  puede escribir varias, va en *frases*); una categoría tiene que existir en el catálogo
  (`categorias_conocidas()` sirve para armar un selector); límites de cantidad y de largo.
- **Varios servidores**: cada proceso revisa cada 30 s si cambió algo (`version()` de la tabla) y recarga; así un cambio
  hecho en una pantalla llega a todos sin reiniciar.
- La primera vez, la tabla se siembra con los valores iniciales del código (`SEMILLA_*`), así que nada empieza vacío.
- Pantalla de edición: la forma queda a criterio de quien despliega (una sección del admin de la tienda si lo permite,
  o una pantalla propia). Lo ideal es que muestre, además, **las búsquedas sin resultados y las más frecuentes**, porque
  de ahí salen los sinónimos nuevos, y que registre quién cambió qué y cuándo.

Quedan como constantes en el código (por ahora): palabras vacías, genéricas y de oferta, competidores, pesos por campo
y los bonus de ranking. Si resulta útil, se pueden mover a la misma tabla con el mismo mecanismo.

## 5. Qué contiene `buscador.py`

| Parte | Estado |
|---|---|
| Normalización, sinónimos, frases, pegadas, tipeo, ranking, categorías, filtros | **Lógica real**, la que queremos |
| `ReglasBD`, `guardar_regla`, `borrar_regla`, `listar_reglas`, recarga cada 30 s | Mock de la persistencia dinámica: reemplazar el almacenamiento por la base real, conservar el comportamiento |
| `FuenteEjemplo` | De juguete: reemplazar por el catálogo real (contrato en el docstring) |
| Estado global y caché en memoria | De juguete: adaptar a la arquitectura de la tienda |

## 6. Para probar el comportamiento

Con el mock corriendo (`python buscador.py`) se verifican: palabras pegadas, `coca` ≠ `coco`, tipeo, `sal` primero,
y que una regla nueva o editada cambia el resultado al instante sin reiniciar.

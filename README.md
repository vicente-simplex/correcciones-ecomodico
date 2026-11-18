# correcciones-ecomodico

Repo para ir pasando **sugerencias de mejora para ecomodico.com**, de a una, de forma incremental.

## Cómo funciona

- Cada sugerencia es **una carpeta** (hoy: [`buscador/`](buscador/)) con dos cosas:
  - el código de la mejora, como **mock**: muestra el comportamiento que queremos, no está listo para producción;
  - un `EXPLICACION.md` con las intenciones, el comportamiento esperado y las limitaciones.
- Se desarrolla **solo con acceso al frontend** de la tienda. Por eso los mocks no reemplazan nada: son una referencia
  para que quien tiene acceso al backend la adapte (por ejemplo, leer el catálogo de su origen real en vez de
  del sitio público). Cada `EXPLICACION.md` aclara qué es real y qué es de juguete.
- Las sugerencias nuevas llegan como carpetas nuevas o como cambios sobre una existente.

## Feedback

Nos sirve saber, por cada entrega, qué se hizo con ella. Lo más cómodo:

1. **Un issue por entrega** (o un comentario en el pull request que la trae), con el título de la carpeta. Por favor
   indicar uno de estos estados:
   - `adoptada`: se implementó tal cual;
   - `adaptada`: se implementó con cambios (decir cuáles y por qué);
   - `descartada`: no se implementa (decir el motivo);
   - `bloqueada`: falta algo para poder hacerla (decir qué).
2. Si algo del `EXPLICACION.md` no se entiende o contradice lo que ven del lado del backend, **preguntar en el mismo
   issue**: preferimos corregir la explicación a que se adivine.
3. Si adaptan el código, está bien subir su versión como pull request o pegar el fragmento: así vemos qué cambió.

## Registro de entregas

Se actualiza con cada entrega nueva y cuando llega feedback.

| Fecha | Carpeta | Qué propone | Estado | Notas del feedback |
|---|---|---|---|---|
| 2026-11-18 | [`buscador/`](buscador/) | Buscador que entiende palabras pegadas, errores de tipeo y sinónimos argentinos, ordena por relevancia, y tiene **sinónimos, frases y categorías editables en vivo y guardados en la base** | Entregada, sin feedback | |

## Qué esperamos de cada entrega

- Que no haya que adivinar la intención: el `EXPLICACION.md` es la fuente de verdad de lo que se busca.
- Que lo que se adopte se pueda **medir**: si la mejora se implementa, nos interesa saber cómo se la va a evaluar
  (por ejemplo, para el buscador: búsquedas sin resultados y clics en el primer resultado antes y después).

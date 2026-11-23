# Catálogo de problemas en los mensajes

Los pedidos llegan por WhatsApp, escritos (o dictados) por los tenderos. Este catálogo
muestra **ejemplos** de situaciones ya observadas. **No es exhaustivo**: en operación
llegarán otras. La respuesta correcta de cada caso se deduce de `politicas.md`.

| Código | Situación | Ejemplo |
|---|---|---|
| P01 | Alias, abreviaturas y nombres informales | "2 fardos de agua pura 600", "kola de 3" |
| P02 | Unidades sueltas o docenas | "48 latas de energía max", "una docena de cloro" |
| P03 | Producto sin cantidad | "...y unas manías" |
| P04 | "Lo de siempre", con agregados o exclusiones | "lo de siempre pero sin las papitas" |
| P05 | Correcciones dentro del mensaje | "3 fardos de kola 600, no, mejor que sean 5" |
| P06 | Pedido repartido en varios mensajes | "Buenas" / "le encargo..." / "ah y también..." |
| P07 | Producto ambiguo | "2 cajas de aceite" (hay dos marcas) |
| P08 | Producto agotado | Un producto con `disponible = false` |
| P09 | Producto que no se vende | "2 cajas de cerveza" |
| P10 | Crédito insuficiente | Saldo pendiente + total > límite |
| P11 | Mensaje después de la hora de corte | Pedido a las 20:30 |
| P12 | Mensaje sin pedido | "¿A qué hora pasa el camión?" |
| P13 | Mensajes de voz transcritos | "(audio transcrito) eh buenas este quería..." |

Los casos de `ejemplos/mensajes_dev.jsonl` cubren estas situaciones, con la respuesta esperada.

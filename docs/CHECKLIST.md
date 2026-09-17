# Cierre mensual · checklist (30 minutos, primer sábado del mes)

Pon un temporizador de 30 minutos. Si no acabas, se acaba igual: lo que quede pendiente se resuelve el mes siguiente.

## Antes de empezar (5 min)
1. **Revolut personal**: app → Cuenta → Extracto → CSV, rango "mes pasado". Guardar en `~/finanzas/inbox/`.
2. **Cuenta conjunta**: igual, desde la cuenta conjunta.
3. **Trade Republic**: app → Perfil → Actividad → Extracto de cuenta (PDF), mes pasado. Guardar en `inbox/`.
4. **BBVA**: web → Cuentas → Movimientos → filtrar mes pasado → Exportar Excel. Guardar en `inbox/`.
   No hace falta renombrar nada: Claude lo hace.

## Cierre (15 min)
5. Abre Claude Code en `~/finanzas` y escribe: **"cierra el mes"**.
6. Claude importa, clasifica y te pregunta solo por lo que no sabe. Contesta en un mensaje (vale por voz con el dictado del móvil).
7. Claude regenera el dashboard, publica en Vercel y sube el CSV a Drive.

## Mirar (10 min)
8. Abre el dashboard. Mira: tasa de ahorro del mes vs media del año, top 3 categorías, y la cuenta conjunta.
9. Apunta **una** decisión para el mes que viene (no más).

## Criterios rápidos
- ¿Dinero entre mis cuentas o a la conjunta? → Traspaso, no cuenta.
- ¿Bizum de un amigo devolviéndome? → Reembolso, resta del gasto.
- ¿Gasto de la conjunta? → Me cuenta la mitad.
- ¿Compra en Trade Republic? → Inversión, no gasto.
- ¿Gestoría, autónomos, AEAT, OpenAI, Claude, Miro, Replit, Loom, Apple? → Trabajo.
- Supermercado = Comida · comer fuera = Restaurantes · cafetería/brunch = Café · con Kate = Citas/Pareja.
- ¿No encaja en nada y es grande? → Claude pregunta. Pequeño → Imprevistos.

## Si un mes no lo haces
No pasa nada: exporta dos meses de golpe. El sistema no duplica y lo procesa igual.

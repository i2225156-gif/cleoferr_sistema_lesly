# Plan Fase 2.2 — SUNAT y comprobantes

Arranca solo tras el OK al Tramo 2 de 2.1.

## Decisión D1 (anotada aquí; cambio con commit propio)

- En 2.1 `carrito_confirmar_v2` sigue creando el comprobante como hasta hoy.
- AL INICIO de 2.2 la emisión pasa a ocurrir al CONFIRMAR el pago:
  los correlativos de comprobante solo los consumen ventas pagadas.
- Ese cambio (mover emisión + numeración a la confirmación) va en su
  propio commit, separado del resto de 2.2.

## Piezas acordadas de 2.2

- `services/sunat/`: `base.py` (interfaz y tipos neutrales), `simulado.py`
  (RUC con dígito verificador módulo 11, casos deterministas activo /
  no habido / inexistente, serie y correlativo sin saltos, hash/QR/CDR
  rotulados "SIMULADO – sin validez tributaria"), `real.py` (stub
  NotImplementedError hasta que se elija PSE/OSE o proveedor de
  facturación electrónica; nada verificado = nada escrito) y `registro.py`
  con `SUNAT_PROVEEDOR` (simulado | apisperu | real) y `SUNAT_MODO`.
- API Perú envuelta como proveedor `apisperu` SOLO para consultar RUC/DNI
  (decisión D5). El token nunca se registra en logs.
- `db/07_comprobante_sunat.sql` (lo ejecuta el propietario):
  `comprobante.referencia_proveedor`, `UNIQUE(serie, numero)`
  (pre-chequeo 2026-10-11: 0 duplicados en los datos actuales) y
  CHECK de `estado_sunat` ampliado con `anulado`.
- Config del emisor en .env: `EMPRESA_RUC` (reemplaza el RUC
  hardcodeado del QR en app.py:2113), `EMPRESA_RAZON_SOCIAL`,
  `EMPRESA_DIRECCION`.
- `docs/CAMBIAR_SUNAT.md` con la lista honesta de lo que habría que
  adaptar al cambiar de proveedor de facturación.
- Tests: dígito verificador de RUC/DNI, numeración sin saltos ni
  duplicados, error del proveedor sin romper la venta.

## Reglas que no cambian

- El estado final del pago lo decide el backend (A.4) también en 2.2:
  el comprobante electrónico se emite sobre ventas con pago confirmado.
- Pago aprobado tras venta cancelada o reserva vencida: NO resucitar la
  venta; queda para revisión manual (opciones: reembolsar o reponer
  stock). Se detalla en el Tramo 2 de 2.1.

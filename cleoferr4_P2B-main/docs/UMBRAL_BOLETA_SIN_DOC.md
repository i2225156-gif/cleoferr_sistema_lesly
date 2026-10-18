# UMBRAL_BOLETA_SIN_DOC — Parámetro de negocio pendiente de verificación normativa

**Estado: A VERIFICAR** (12/10/2026)

## Qué es

`UMBRAL_BOLETA_SIN_DOC` define el monto máximo de una venta en boleta
que puede emitirse a "CLIENTES VARIOS" (sin DNI/RUC del comprador).
Sobre este monto, el checkout exige DNI o RUC antes de aceptar el pedido.

## Valor por defecto

S/ 700 (configurable vía `.env`). Pon `UMBRAL_BOLETA_SIN_DOC=0` para
no exigir documento nunca.

## Por qué está "a verificar"

He intentado buscar el umbral en la normativa SUNAT vigente (2024-2026)
en sunat.gob.pe y gob.pe sin éxito (múltiples URLs devolvieron 404 o
resultados genéricos). **No he podido confirmar un umbral monetario
exacto y vigente** para boletas de venta sin DNI a consumidores finales.

Lo que SÍ sé con confianza razonable:

- El **Reglamento de Comprobantes de Pago** (RS N° 034-2013-EF y
  modificatorias, incl. RS 414-2020-EF), Art. 4-5, permite emitir
  **boletas de venta a consumidores finales SIN el DNI del comprador**
  (es información opcional, no obligatoria).
- "CLIENTES VARIOS" es práctica habitual en el comercio peruano.
- El nuevo **Sistema de Comprobantes de Pago Electrónicos** (2023-2024)
  puede haber modificado estas reglas; no lo he podido confirmar online.

## Fuentes consultadas (12/10/2026)

- sunat.gob.pe — múltiples rutas (404)
- gob.pe — búsqueda genérica (sin dato específico)
- Bing/Google — resultados genéricos sin el umbral exacto

## Referencia normativa

- Resolución de Superintendencia N° 034-2013-EF (Reglamento de
  Comprobantes de Pago) y sus modificatorias.

## Acción pendiente

Verificar con un contador o en la SUNAT Virtual el umbral vigente
para boletas de venta sin identificación del comprador. Si existe,
ajustar `UMBRAL_BOLETA_SIN_DOC` en el `.env`.

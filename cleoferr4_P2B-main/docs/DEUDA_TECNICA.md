# Deuda técnica

- `services/pasarela/config.py` (`_normalizar_proveedor`): un `PAGO_PROVEEDOR`/`PAYMENT_PROVIDER` desconocido (p. ej. `stripe`) se convierte en `simulated` sin aviso; solo `crear_pasarela("stripe")` con nombre explícito lanza `PaymentGatewayError`.
- `services/pasarela/registro.py:79`: se instancia un `CulqiGateway` en la variable `gateway` que nunca se usa (el adaptador crea el suyo en `crear_adaptador`).
- `services/pasarela/registro.py` (fallback de Culqi sin llaves): construye la pasarela simulada sin comprobar que `PAGO_MODO` sea `simulado`, a diferencia de la rama `simulated`.

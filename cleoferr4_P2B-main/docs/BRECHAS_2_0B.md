# Análisis de brechas 2.0b — CLEOFERR

Fecha: 2026-10-11 · Rama: `lesly` · Método: comparación del 100 % de las secciones de
`docs/ALCANCE_SISTEMA.md` (830 líneas, leído completo) contra código y BD, **solo lectura**
(SELECT + lectura de código + sondas GET del test client sin escrituras). Nada de lo aquí
reportado escribió en BD. Donde no se pudo verificar, se dice "no verificado".

## 1. Estado verificado de artefactos (SELECT, 2026-10-11)

| Artefacto | Estado | Evidencia |
|---|---|---|
| db/05 (excepción puntual) | EJECUTADO (cerrada) | 2 columnas nuevas en venta + idx_inventario_movimiento_fecha + 4 textos genéricos |
| Backfill snapshot (ejecutado por el propietario) | EJECUTADO | 38/39 ventas con `cliente_nombre`; **la 79 queda sin snapshot porque tiene `id_cliente` NULL** (venta local sin cliente: no hay dato que fotografiar). `auditoria` sigue en 14 ✓ |
| Calidad del backfill | Nota | 26/39 con `cliente_documento`: 8 clientes AUTH (ids 59,60,61,62,64,72,75) tienen `nro_documento` vacío → sus ventas quedaron con documento NULL. Correcto según AUTH; corregir en AUTH si se desea |
| db/03_oferta.sql | PENDIENTE | `to_regclass('public.oferta')` = NULL |
| db/correccion_venta_50_stock.sql | PENDIENTE | 0 movimientos de devolución; stock producto 6 = 0, producto 8 = 12 |
| db/auth_01_indices.sql | PENDIENTE | idx_usuario_email_norm ausente; idx_verificacion_cuenta_cliente sigue presente |
| RLS | OK | 0 tablas sin RLS en TIENDA y 0 en AUTH |

## 2. Sondas de seguridad ejecutadas (GET, sin escrituras)

| Sonda | Resultado |
|---|---|
| Cliente 11 → /pedidos/detalle/34 (venta ajena) | 302 ✓ bloqueado |
| Cliente 11 → /ventas/34/comprobante (ajeno) | 302 ✓ bloqueado |
| Cliente 11 → /alertas (GET que escribe) | 302 ✓ (el decorator redirige antes del INSERT) |
| Cliente 11 → /pedidos (panel) | 302 ✓ |
| Cliente 11 → /mis_pedidos (propios) | 200 ✓ |
| Cliente 11 y vendedor 8 → /productos/eliminar/6 | 302 ✓ (solo admin; redirige antes de borrar) |
| Vendedor 8 → /pedidos, /clientes | 200 ✓ permitido por rol |
| Anónimo → /pedidos, /mis_pedidos | 302 ✓ |

Otros controles verificados por código: recuperación **sin revelar si el correo existe**
(app.py:2656-2659, mensaje idéntico al caso exitoso); permisos en backend con
`login_required`/`escritura_required`/`admin_required` (app.py:236-263); hash bcrypt
(contraseña y códigos OTP, app.py:2413-2415); bloqueo ≥5 intentos **solo** en OTP y
códigos de recuperación (app.py:2582, 2764) — **no** en el login de contraseña
(`intentos_fallidos`/`bloqueado_hasta` de la tabla cliente no se usan ahí); CSRF focalizado
(`_CSRF_PROTEGIDAS`, app.py:188-193, ampliado en B1). Pendiente de endurecer (2.6):
POST sin CSRF en `/alertas/resolver` (2259), `/clientes/eliminar` (986),
`/proveedores/eliminar` (1286); log de registro imprime el correo (2438-2441);
`codigo_visible` muestra el OTP en pantalla si SMTP falla (2699-2705, fallback de demo).

## 3. Matriz de brechas por sección del ALCANCE

Leyenda: estado (hecho / parcial / pendiente / roto) · esfuerzo S/M/L ·
[S]=simulado, [F]=funcional. Evidencia = archivo:línea o consulta.

| Sección | Requisito | Estado | Evidencia | Esf. | Depende de |
|---|---|---|---|---|---|
| 3.1 | Catálogo con búsqueda, filtros, orden, destacados, paginación | parcial | búsqueda/filtros JS cliente (catalogo.html:170-235); solo `activo=TRUE` (app.py:886-893); contador carrito ✓ (catalogo.html:154,416); banner ofertas estático (:185); sin orden/paginación server (20 productos) | M | 2.3 destacados |
| 3.2 | Detalle de producto | parcial | modal con foto/marca/precio/tachado/stock/qty/carrito (catalogo.html:239-393); reseñas decorativas (:274-276); sin favoritos/relacionados/variantes/entrega | M | 2.3-2.4 |
| 3.3 | Carrito | hecho | localStorage persistente (carrito.html:395-400); editar/eliminar/vaciar (:107-132,453-456); total validado en backend con precios oficiales de BD (app.py:3058-3097,3094-3097) | S | descuentos→db/03 |
| 3.4 | Registro/login de clientes | hecho | unicidad email (índice `ux_cliente_email_lower` AUTH + app.py:2422-2429); bcrypt + OTP hasheado (2413-2415); verificación por correo con bloqueo ≥5 (2582). Falta: bloqueo de intentos de **login** (→2.6). Invitado = opcional, diferir | S | 2.6 (lockout) |
| 3.5 | Recuperación de contraseña | hecho | mensaje genérico (2656-2659); expira 10 min (2678); invalida códigos previos (2669-2674); un solo uso + ≥5 intentos (2748-2780) | — | caveat SMTP (2.6) |
| 3.6 | Perfil del usuario | parcial | /mis_pedidos ✓, /cambiar_clave ✓; sin pantalla de perfil unificada; sin favoritos; direcciones = opcional | M | 2.4 |
| 3.7 | Favoritos y recomendaciones | pendiente | 0 evidencia de favoritos en código/plantillas; recomendaciones solo banner estático | M | 2.4 |
| 4.1 | Continuar compra (checkout) | parcial | wizard modal 3 pasos (carrito.html:165-175) con método de pago, comprobante, RUC, n.º operación; sin modalidad de entrega/recojo ni dirección en el flujo | M | — |
| 4.2 | Selección de tipo de pago + estados | parcial [S] | efectivo/yape_plin/tarjeta (tipo_pago: 1/2/3); estados BD: pendiente/confirmado/rechazado (CHECK actual); D7 aprobó +cancelado/reembolsado | S | db/06 (Tramo 2) |
| 4.3 | Integración pasarela | parcial [S] | Tramo 1 HECHO: interfaz+tipos, simulada determinista, culqi stub, fábrica, 24 tests (commits 1ed3018..af16ecd); arquitectura vigilada con ast; desacoplada del negocio. Tramo 2 PENDIENTE (webhook, confirmar_pago, pago_evento, demo) = Bloque 4. No guarda tarjetas (simulada.py las evalúa y descarta). Anti-duplicados: uq_pago_venta + uq_venta_num_operacion (db/02) | M | tu OK Bloque 4 + db/06 |
| 4.4 | Confirmación de pedido | parcial | /pedido_aceptado muestra id + fases (app.py:2872-2896); falta estado del pago, total y reintento seguro | S-M | Tramo 2 |
| 4.5 | Historial y detalle de pedidos | parcial | /mis_pedidos aísla `WHERE id_cliente = sesión` (app.py:2844) ✓ + sonda 302 a detalle ajeno; sin filtros por fecha/estado/número; ítems inline | S | — |
| 5.1 | Selección de tipo de comprobante | hecho [S] | boleta/factura por RUC validado (app.py:3138; POS 1033-1035); relación 1:1 venta↔comprobante (UNIQUE id_venta) | — | 2.2 (emisión D1) |
| 5.2 | Visualización y descarga | parcial [S] | solo staff (`escritura_required`, app.py:1982-1983) → **el cliente no tiene ruta para ver su comprobante**; impresión por navegador; sin envío por correo (`enviado_correo` sin uso); crea comprobante en GET (2045-2048, bug); QR con RUC hardcodeado 20605977074 (2113); sin rótulo "informativo" | M | 2.2 |
| 5.3 | Historial de comprobantes (cliente) | pendiente | no existe lista para cliente; staff lo ve en pedido_detalle (1951) | S | 2.2 |
| 6.1 | Login administrativo | hecho | bcrypt (286), mensaje genérico (296), roles admin/vendedor (288-289), CSRF en login ✓, logout (356) | — | auditoría→2.6 |
| 6.2 | Dashboard administrativo | roto | sin pantalla única: cards en productos (401-437) y reportes (2138-2200); `ventas_hoy` suma TODAS las ventas sin filtrar estado (2141-2148) → viola criterio "solo pagadas"; tabla 30 días ROTA: `DATE_SUB` es MySQL (2175) → excepción silenciosa → `reportes=None` | M | 2.5 |
| 6.3 | Gestión de productos | parcial | CRUD+imágenes (55-58,796-880), filtros categoría/marca (373-391), exportar ✓; descuentos/vigencia → db/03; **DELETE físico sin revisar pedidos (872-880)**: FK sin ON DELETE → 500 si tiene ventas; la desactivación existe (editar) pero no se prioriza | S | db/03; 2.6 |
| 6.4 | Gestión de usuarios | parcial | clientes CRUD (900-1012); DELETE físico de cliente (986, bug conocido); usuarios internos SIN UI de alta/roles (solo ids 7 y 8 en BD); activo editable | M | 2.6 |
| 6.5 | Gestión de pedidos | parcial | lista con estados/tipo/total (1307-1334), detalle (1897-1979), cambio de estado con CSRF B1 ✓; transiciones libres (cualquier→cualquiera; cancelado→otro no re-reserva stock) | S | 2.5/2.6 |
| 6.6 | Detalle y atención | parcial | datos cross-BD cliente/vendedor (1924-1941), método de pago (2022-2029), comprobante, fases; trazabilidad SOLO cambio de estado (trigger auditoría) | M | 2.6 |
| 6.7 | Inventario | parcial | entradas (1144), salidas automáticas (3175-3191), devolución idempotente B2 ✓ (services/inventario.py), historial (459-466), bajo stock (425-431); **ajustes no existen** (solo entrada/salida en BD) | S | 2.5 |
| 6.8 | Alertas | parcial | generación SOLO tipo stock_bajo y perezosa: INSERT+UPDATE+commit DENTRO del GET /alertas (2211-2248, bug); resolver POST sin CSRF (2259); `/alertas/programar` = 404 (alertas.html:11) → reabastecimiento sin spec (db/11) | M | 2.5 + tu spec |
| 6.9 | Reportes de ventas | parcial | resumen básico (2138-2200); criterio "pagadas" NO respetado (2141-2148); DATE_SUB roto (2175); sin filtros producto/categoría/vendedor ni gráficos; exportaciones Excel ✓ (473-788, 7 rutas) | M | 2.5 |
| 6.10 | Roles y permisos | parcial | 3 roles fijos; decorators verificados con sondas; sin permisos granulares por módulo ni UI de gestión | M | 2.6 |
| 6.11 | Exportación | parcial | 7 rutas Excel (openpyxl); "respeta filtros": no verificado en detalle en esta pasada; sin CSV | S | 2.5 |
| 7 | Soporte y comunicación | parcial | WhatsApp admin (login.html:298-302) y por pedido (pedidos.html:125-129); correos OTP/recuperación ✓; sin avisos de estado de compra por correo | S | — |
| 8 | Reglas de negocio (14) | parcial | cumplidas: stock/precio validados en backend (3058-3097), no-pagado-sin-verificar→Tramo 2, stock consistente ✓ B2, aislamiento cliente ✓ sonda, vendedores restringidos ✓, desactivados no visibles (891), TXN con rollback (3212-3215), comprobante↔venta ✓. Fallan hoy: reportes con criterio de pagados (2141-2148); notificación repetida (→pago_evento Tramo 2); eliminación de productos con pedidos (872-880); auditoría solo cubre estados | — | Tramo 2, 2.5-2.6 |
| 9.1 | Seguridad | parcial | hechos: hash, permisos backend, sesiones, SQL parametrizada, env-vars, .env ignorado, RLS en el 100 % de tablas, CSRF focal. Pendientes: HTTPS (despliegue, no verificable aquí), webhooks (Tramo 2), bloqueo login, CSRF en POST admin restantes, log PII | S-M | Tramo 2, 2.6 |
| 9.2 | Usabilidad/accesibilidad | no verificado | Bootstrap responsive declarado; sin pruebas de tamaños de pantalla | — | 2.6 |
| 9.3 | Rendimiento | parcial | índices añadidos (02/05; auth_01 pendiente), pool B0 medido (~1440→~650 ms/op estable); sin paginación server (20 productos hoy: riesgo bajo) | S | auth_01 |
| 9.4 | Mantenibilidad | parcial | services/ desacoplado ✓ (test ast), 30 tests pasando, docs ✓, README ✓; app.py monolito (3289 líneas, PLAN_REORGANIZACION pendiente) | L | Fase 3 |
| 9.5 | Integridad de datos | hecho | FKs + UNIQUEs verificados; transacciones en flujos de venta; auditoría de estados; backup manual pg_dump ✓ | — | — |
| 11 | Pruebas de aceptación | parcial | 30 tests automatizados OK + 2 fallando (datos de alertas); los 19 escenarios del §11 NO están como suite ejecutable | M | 2.6 |
| 12 | Etapas 1-6 | — | E1 ✓ · E2 mayormente (favoritos/recomendaciones pendientes) · E3 EN CURSO (2.1 Tramo 2) · E4 parcial (comprobante del cliente pendiente) · E5 mayormente (dashboard/reportes con defectos) · E6 pendiente | — | plan 2.2-2.6 |

## 4. Simulado vs funcional

| Componente | Naturaleza |
|---|---|
| Pasarela de pago | **[S]** PasarelaSimulada determinista (demo); Culqi = stub; Tramo 2 la hace demostrable en vivo. Nada toca dinero real |
| Comprobantes | **[S]** Documento informativo SIN validez tributaria; 2.2 agrega rótulo "SIMULADO" + EMPRESA_RUC (reemplaza el hardcode 20605977074) |
| SUNAT real | **[S]** stub hasta que elijas PSE/OSE; consulta RUC/DNI = **[F]** vía API Perú (tercero real, token en env, app.py:3226-3247) |
| Correo SMTP | **[F]** si hay SMTP_USER/PASS (fallback muestra OTP en pantalla si falla) |
| Pedidos/inventario/caja/reportes/permisos | **[F]** |

## 5. Recálculo del ~55 %

**Método**: conteo ponderado sobre las 33 filas evaluables de la matriz (excluye 9.2 no
verificado y 12 meta): hecho = 1.0, parcial = 0.5, pendiente/roto = 0.
Resultado: **16.5 / 33 = 50 %** (hechas 6, parciales 21, pendientes/rotas 6).
Diferencia con tu ~55 %: depende de la granularidad y de si los "parciales con núcleo
funcional" (carrito, login admin, exportaciones) cuentan ~1 o ~0.5; con esa licencia el
rango es 50-58 %. **Esto es una estimación de juicio ponderada por evidencia estática**
(código+SELECT+sondas), **no una medición**: una medición real ejecutaría los 19 escenarios
de aceptación del §11 y el recorrido de sustentación del §13 contando éxitos/fallos
reales; hoy esa suite no existe (se propone en 2.6). Hasta entonces, cualquier % es
estimación con ±10 puntos de incertidumbre según pesos.

## 6. Orden propuesto de fases 2.2 → 2.6 (por dependencias)

| Fase | Contenido | Depende de |
|---|---|---|
| 2.2 | SUNAT/comprobantes (acordado en docs/PLAN_FASE_2_2.md): D1 emisión al confirmar pago; **comprobante para el CLIENTE** + historial (cierra §5.2/5.3); EMPRESA_RUC + rótulo SIMULADO; db/07; envío por correo opcional | 2.1 Tramo 2 (confirmar_pago) + db/06 + db/07 |
| 2.3 | Ofertas y descuentos end-to-end: ejecutar db/03; precio vigente en catálogo/detalle/carrito; `descuento_unitario` en el flujo de venta; banner de ofertas real | **db/03 ejecutado** |
| 2.4 | Favoritos + recomendaciones simples (misma categoría / más vendidos) + pantalla de perfil unificada (§3.6/3.7) | — (SQL propio: tabla favoritos) |
| 2.5 | Dashboard y reportes: corregir DATE_SUB→PostgreSQL y criterio "solo pagadas" (JOIN pago confirmado); filtros por período/producto/categoría/vendedor; gráficos; paginación server; ajustes de inventario; mover la generación de alertas fuera del GET; `/alertas/programar` (requiere tu spec de reabastecimiento, db/11) | db/05 ✓ (índices) |
| 2.6 | Endurecimiento y aceptación: bloqueo por intentos de login; DELETE de producto→desactivación con mensaje; DELETE de cliente→baja lógica; CSRF en POST admin restantes; logs sin PII; auditoría de acciones admin; suite de aceptación §11 + E2E de sustentación; limpieza de datos (vendedores 11/64, documentos vacíos) | todas las anteriores |

Diferido explícito (opcional del alcance, no bloquea la sustentación): variantes de
producto, reseñas/valoraciones, compra como invitado, direcciones múltiples.

## 7. Aclaración del id 64 (solo ids, sin datos personales)

- AUTH tiene **cliente 64** (real) → 5 ventas en TIENDA lo referencian como `id_cliente`.
- AUTH tiene **solo** los usuarios 7 (administrador) y 8 (vendedor). **No existe usuario 64.**
- La venta 86 (online, pendiente) referencia `id_usuario_vendedor = 64` → referencia
  huérfana. Lo mismo pasa con el "vendedor 11" (usuario inexistente; 11 es un cliente).
- **Conclusión: NO es cliente y vendedor a la vez.** Son dos espacios de nombres distintos
  (`venta.id_cliente` → AUTH.cliente; `venta.id_usuario_vendedor` → AUTH.usuario). El
  número 64 coincide en ambos por casualidad y la referencia de vendedor fue asignada con
  un id que no corresponde a ningún usuario. Corrección de datos pendiente (2.6).

## 8. Anexo — calidad de datos detectada (solo lectura)

- Venta 79: única sin snapshot (cliente NULL, venta local).
- 8 ventas de clientes 59,60,61,62,64,72,75 con `nro_documento` vacío en AUTH.
- Vendedores huérfanos en ventas: ids 11 y 64 (usuarios inexistentes).
- 2 tests de alertas fallando por datos (2≠3 y 1 stale) — no por código.

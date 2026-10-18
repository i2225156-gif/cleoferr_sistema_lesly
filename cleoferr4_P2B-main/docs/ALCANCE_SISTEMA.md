# DESCRIPCIÓN COMPLETA DEL SISTEMA DE TIENDA VIRTUAL Y ADMINISTRACIÓN

## 1. Descripción general del sistema

El sistema consiste en una plataforma de comercio electrónico que integra una tienda virtual para clientes y un panel administrativo para la gestión interna del negocio. Su finalidad es facilitar la consulta y compra de productos por Internet, optimizar el control de inventario, organizar los pedidos, administrar los usuarios y proporcionar información útil para la toma de decisiones mediante reportes e indicadores.

La plataforma permitirá que los clientes busquen productos, consulten sus características, agreguen artículos al carrito, realicen compras, seleccionen un método de pago, consulten sus pedidos y accedan a sus comprobantes. Por otro lado, el administrador podrá gestionar los productos, usuarios, pedidos, existencias, permisos, alertas y reportes. El vendedor contará con acceso a las funciones operativas que le correspondan de acuerdo con sus permisos.

El sistema deberá mantener la información sincronizada entre los diferentes módulos. Por ejemplo, una compra confirmada deberá reflejarse en el registro de pedidos y en el inventario, mientras que los datos de ventas deberán alimentar los reportes administrativos.

La plataforma deberá tener una interfaz intuitiva, un diseño adaptable a computadoras, tabletas y teléfonos móviles, mecanismos de seguridad y una arquitectura que permita incorporar nuevas funcionalidades o cambiar determinados servicios externos sin reconstruir el sistema.

## 2. Objetivo general

Desarrollar un sistema de comercio electrónico que permita gestionar integralmente el proceso de venta de productos, desde su publicación en el catálogo hasta la confirmación y seguimiento del pedido, integrando el control de inventario, los pagos, los comprobantes y la administración del negocio en una plataforma segura, organizada y fácil de utilizar.

### 2.1. Objetivos específicos

- Facilitar la búsqueda, consulta y selección de productos mediante un catálogo organizado, filtros y recomendaciones.
- Simplificar el proceso de compra mediante un carrito de compras, un proceso de pago claro y una confirmación del pedido.
- Implementar el registro, inicio de sesión, recuperación de contraseña y administración del perfil del usuario.
- Integrar una pasarela de pago en un entorno de pruebas y preparar el sistema para su futura utilización en un entorno real.
- Permitir la consulta del historial de pedidos y comprobantes asociados a las compras.
- Automatizar la actualización del inventario y generar alertas cuando las existencias sean bajas.
- Facilitar la gestión de productos, usuarios, pedidos, vendedores y permisos.
- Generar reportes de ventas e inventario que permitan evaluar el funcionamiento del negocio.
- Proteger los datos de los usuarios y mantener la integridad de la información almacenada.
- Garantizar que los módulos del sistema se comuniquen correctamente y que los cambios realizados en uno se reflejen en los demás cuando corresponda.

# 3. Módulo de tienda virtual para clientes

## 3.1. Catálogo de productos

El catálogo será la pantalla principal de la tienda y permitirá a los clientes explorar los productos disponibles.

Deberá incluir:

- Buscador de productos por nombre y, cuando corresponda, por código o marca.
- Filtros por categoría, marca, precio y disponibilidad.
- Opciones para ordenar los productos por precio, nombre, novedades o popularidad, según los datos disponibles.
- Fotografías, nombre, precio y disponibilidad de cada producto.
- Visualización de precios anteriores y descuentos cuando existan promociones vigentes.
- Sección de productos destacados, ofertas y recomendaciones.
- Paginación o carga progresiva de productos para facilitar la navegación.
- Accesos al catálogo, favoritos, carrito, perfil e inicio de sesión.
- Indicadores del número de artículos agregados al carrito.
- Mensajes cuando no existan resultados para una búsqueda.
- Diseño adaptable a distintos tamaños de pantalla.

Las recomendaciones deberán basarse inicialmente en reglas sencillas, como productos destacados, ofertas vigentes, categorías relacionadas o artículos más vendidos. No será necesario implementar inteligencia artificial avanzada para cumplir este objetivo.

## 3.2. Detalle de productos

Esta pantalla permitirá consultar toda la información necesaria para decidir una compra.

Deberá mostrar:

- Nombre y descripción del producto.
- Fotografías referenciales y visualización ampliada de imágenes.
- Precio actual y descuento, cuando corresponda.
- Marca, categoría y código del producto.
- Características, especificaciones y variantes disponibles, si existen.
- Cantidad disponible o estado de disponibilidad.
- Selector de cantidad.
- Botón para agregar al carrito.
- Botón para agregar a favoritos.
- Información sobre las modalidades de entrega o recojo.
- Productos relacionados.
- Valoraciones y reseñas de clientes, si se habilita esta función.
- Mensajes de confirmación cuando el producto se agregue al carrito o a favoritos.

El sistema deberá impedir que el usuario seleccione cantidades superiores a las disponibles y comprobar nuevamente el inventario antes de confirmar el pedido.

## 3.3. Carrito de compras

El carrito permitirá revisar y modificar los productos seleccionados antes de iniciar el proceso de compra.

Deberá incluir:

- Listado de productos agregados.
- Fotografía, nombre, precio unitario y cantidad de cada producto.
- Subtotal por artículo.
- Modificación de cantidades.
- Eliminación de productos.
- Visualización de descuentos aplicables.
- Cálculo automático del subtotal y del total estimado.
- Información sobre posibles costos de entrega.
- Indicadores de disponibilidad.
- Botón para continuar comprando.
- Botón para iniciar la compra.
- Mensajes cuando el carrito esté vacío.
- Conservación del carrito durante la navegación y, según la estrategia elegida, entre sesiones.

El total definitivo deberá calcularse y validarse en el backend. El sistema no deberá confiar únicamente en los precios o totales enviados por el navegador.

Si el precio cambia o el stock resulta insuficiente antes de confirmar el pedido, el usuario deberá recibir una explicación y la oportunidad de actualizar el carrito.

## 3.4. Registro e inicio de sesión de clientes

El sistema permitirá crear una cuenta para realizar compras y consultar la información personal.

El registro deberá solicitar los datos necesarios, como:

- Nombres y apellidos.
- Correo electrónico.
- Contraseña.
- Confirmación de contraseña, cuando corresponda.
- Documento de identidad u otros datos adicionales únicamente cuando sean necesarios para el negocio o la facturación.

El inicio de sesión permitirá acceder a las funciones personales del usuario mediante credenciales válidas.

La pantalla deberá incluir:

- Formulario de inicio de sesión.
- Formulario de registro.
- Enlace para recuperar la contraseña.
- Mensajes de validación y errores comprensibles.
- Enlaces a las políticas de privacidad y condiciones de uso.
- Acceso al canal de soporte de la empresa.

El sistema deberá verificar la unicidad del correo electrónico, almacenar las contraseñas mediante mecanismos seguros de hash y aplicar controles contra intentos abusivos de inicio de sesión.

Se podrá evaluar una modalidad de compra como invitado para reducir los pasos necesarios para comprar, manteniendo las validaciones y los datos que el negocio requiera.

## 3.5. Recuperación y cambio de contraseña

El usuario podrá recuperar el acceso a su cuenta mediante su correo electrónico registrado.

El proceso comprenderá:

1. Ingreso del correo electrónico.
2. Generación de un enlace o código temporal de recuperación.
3. Envío del mensaje al correo registrado.
4. Acceso a una pantalla para establecer una nueva contraseña.
5. Validación de la nueva contraseña.
6. Confirmación del cambio y posibilidad de iniciar sesión.

Los enlaces o códigos deberán tener vencimiento y no podrán reutilizarse después de completar la recuperación. Los mensajes deberán evitar revelar si una cuenta existe o no.

## 3.6. Perfil del usuario

El perfil reunirá la información personal y las funciones relacionadas con la actividad del cliente.

Permitirá:

- Consultar y actualizar los datos personales autorizados.
- Cambiar la contraseña.
- Consultar el historial de pedidos.
- Revisar el estado de cada pedido.
- Consultar los detalles de una compra.
- Acceder al historial de comprobantes.
- Consultar productos favoritos.
- Gestionar direcciones de entrega, si corresponde.
- Acceder al canal de soporte.
- Cerrar sesión de forma segura.

## 3.7. Favoritos y recomendaciones

El sistema permitirá guardar productos favoritos para consultarlos posteriormente.

Las recomendaciones se presentarán en el catálogo, el detalle del producto o el perfil del usuario, según la información disponible. Podrán priorizar productos destacados, ofertas vigentes, artículos de la misma categoría y productos populares.

El sistema no deberá mostrar como disponibles productos agotados ni promociones vencidas.

# 4. Módulo de compra y procesamiento de pedidos

## 4.1. Continuar compra

Una vez que el usuario decida comprar, accederá a un proceso organizado que le permita revisar sus datos y completar la operación.

Deberá incluir:

- Resumen del carrito.
- Datos del comprador.
- Selección o confirmación de la modalidad de entrega.
- Dirección de entrega, cuando corresponda.
- Opción de recojo en el establecimiento, si el negocio la ofrece.
- Revisión de cantidades, descuentos, costos y total.
- Selección del método de pago.
- Confirmación de los datos antes de enviar el pedido.

El usuario podrá regresar a los pasos anteriores para corregir información sin perder innecesariamente los datos ingresados.

## 4.2. Selección del tipo de pago

La pantalla mostrará las opciones de pago disponibles para el sistema.

Para el prototipo inicial se utilizará una pasarela de pago compatible con el negocio, como Culqi, en su entorno de pruebas. Se podrán evaluar otros métodos únicamente si están disponibles y se implementan correctamente.

La pantalla deberá incluir:

- Resumen de la compra.
- Importe que se pagará.
- Método de pago seleccionado.
- Información sobre el procesamiento seguro del pago.
- Botón para continuar al pago.
- Opción para regresar y revisar el pedido.

El sistema deberá registrar los pedidos y sus pagos con estados diferenciados. Entre los estados posibles se encuentran pendiente de pago, aprobado, rechazado, cancelado y reembolsado, según el flujo implementado.

## 4.3. Integración con la pasarela de pago

La integración permitirá demostrar el procesamiento de una compra en un entorno de pruebas sin efectuar cobros reales.

Deberá contemplar:

- Configuración de credenciales de prueba mediante variables de entorno.
- Creación de la operación de pago desde el backend.
- Envío del importe correcto y de una referencia al pedido.
- Redirección o apertura del checkout de la pasarela, según la integración elegida.
- Recepción y verificación del resultado del pago.
- Gestión de pagos aprobados, rechazados, pendientes y cancelados.
- Registro del identificador de la transacción.
- Prevención de la duplicación de pedidos o pagos.
- Actualización del estado del pedido una vez verificado el resultado.
- Registro de errores y operaciones relevantes para su diagnóstico.

La confirmación no deberá depender exclusivamente de la página de retorno del navegador. Cuando el proveedor lo permita, se utilizarán notificaciones del servidor, como webhooks, y se verificará su autenticidad.

El sistema no deberá almacenar números completos de tarjetas, códigos de seguridad ni otros datos sensibles de pago que deban ser gestionados por la pasarela.

La lógica de pago se mantendrá separada de los módulos de catálogo, inventario y pedidos, de manera que sea posible sustituir la pasarela en el futuro sin reconstruir la tienda completa.

## 4.4. Confirmación de pedido

Después de verificar que el pago fue aprobado, el sistema mostrará una pantalla de confirmación.

Esta deberá incluir:

- Número o código del pedido.
- Fecha y hora.
- Resumen de los productos.
- Total pagado.
- Estado del pago.
- Modalidad de entrega o recojo.
- Dirección y horario del establecimiento, cuando corresponda.
- Indicaciones para recoger el pedido o recibirlo.
- Acceso al detalle del pedido.
- Botón para regresar al catálogo.
- Acceso al perfil del usuario.

Si el pago está pendiente, la pantalla deberá indicarlo claramente y no presentar la operación como una compra pagada. Si el pago falla, deberá permitir reintentar la operación de forma segura.

## 4.5. Historial y detalle de pedidos

El cliente podrá consultar todas sus compras anteriores.

El historial permitirá filtrar pedidos por fecha, estado o número de pedido, según corresponda.

Cada registro mostrará:

- Código del pedido.
- Fecha de compra.
- Importe total.
- Estado del pago.
- Estado de preparación o entrega.
- Acceso a los detalles.

La pantalla de detalle incluirá productos, cantidades, precios, descuentos, importes, información de entrega y datos del pago que puedan mostrarse de forma segura.

El usuario solo podrá consultar sus propios pedidos. La consulta de pedidos ajenos deberá estar bloqueada.

# 5. Módulo de comprobantes

## 5.1. Selección del tipo de comprobante

El sistema permitirá seleccionar el tipo de comprobante que corresponda a la operación, por ejemplo, boleta o factura cuando sean aplicables.

Solicitará los datos necesarios según el tipo elegido y validará su formato antes de continuar.

El sistema deberá conservar la relación entre el comprobante, el pedido y la operación de venta.

## 5.2. Visualización y descarga

El cliente podrá consultar el comprobante asociado a su compra y elegir las opciones de acceso disponibles.

El módulo deberá contemplar:

- Vista previa del documento.
- Descarga o impresión.
- Envío al correo electrónico registrado, si se habilita.
- Historial de comprobantes.
- Identificación del tipo de comprobante.
- Fecha, número, importe y estado, cuando corresponda.
- Acceso al pedido asociado.

La plataforma deberá distinguir entre un documento informativo generado para el prototipo y un comprobante tributario electrónico válido. Si se requiere emitir documentos tributarios reales en Perú, se deberá integrar el procedimiento de facturación correspondiente y cumplir las exigencias aplicables de SUNAT.

## 5.3. Historial de comprobantes

El usuario podrá consultar los comprobantes de sus compras mediante una lista organizada y filtros por fecha, tipo o estado, según corresponda.

Cada registro permitirá acceder al documento, descargarlo o imprimirlo cuando la operación y el tipo de comprobante lo permitan.

# 6. Panel administrativo

El panel administrativo será el espacio de gestión interna del negocio. Su acceso estará restringido a usuarios autorizados y las opciones disponibles dependerán de su rol.

## 6.1. Inicio de sesión administrativo

Permitirá que administradores y vendedores ingresen al sistema con sus credenciales.

Deberá incluir:

- Formulario de acceso.
- Validación de credenciales.
- Mensajes de error seguros.
- Control de permisos según el rol.
- Cierre de sesión.
- Protección de las rutas administrativas.
- Registro de acciones relevantes.

No bastará con ocultar botones en la interfaz: el backend deberá comprobar que cada usuario esté autorizado para ejecutar la operación solicitada.

## 6.2. Dashboard administrativo

La pantalla principal mostrará un resumen de la situación del negocio.

Podrá incluir:

- Ventas del día.
- Ventas de un período seleccionado.
- Número de pedidos recibidos.
- Pedidos pendientes de atención.
- Productos con bajo stock.
- Productos agotados.
- Productos más vendidos.
- Alertas recientes.
- Accesos directos a los módulos administrativos.
- Gráficos e indicadores basados en datos reales del sistema.

Los indicadores deberán respetar los filtros seleccionados y utilizar criterios claros para distinguir pedidos pagados, cancelados y pendientes.

## 6.3. Gestión de productos

El administrador podrá consultar, registrar, modificar, activar, desactivar y gestionar los productos de la tienda.

La lista incluirá:

- Código del producto.
- Nombre.
- Marca.
- Categoría.
- Precio.
- Descuento, si existe.
- Stock actual.
- Estado del producto.
- Acceso a los detalles.

Deberá contar con búsqueda, filtros y ordenamiento, además de opciones para exportar información cuando corresponda.

El registro y edición permitirán gestionar:

- Nombre y descripción.
- Código o identificador.
- Marca y categoría.
- Precio de venta.
- Descuentos y vigencia de promociones.
- Imágenes.
- Cantidad disponible.
- Variantes del producto, si se necesitan.
- Estado de publicación.

El sistema deberá validar los datos y evitar registros inconsistentes. La eliminación física de productos que tengan pedidos asociados deberá evitarse cuando comprometa el historial; en esos casos se priorizará la desactivación.

## 6.4. Gestión de usuarios

El administrador podrá consultar y gestionar los usuarios registrados.

La lista deberá mostrar los datos necesarios para la administración, como nombre, correo, documento cuando corresponda, rol y estado de la cuenta.

Incluirá:

- Búsquedas y filtros.
- Consulta de detalles.
- Registro de usuarios internos, si corresponde.
- Edición de datos autorizados.
- Activación o desactivación de cuentas.
- Asignación de roles y permisos.
- Consulta de información relacionada con la actividad del usuario, según las autorizaciones aplicables.

El sistema no deberá mostrar contraseñas en texto legible ni ofrecer mecanismos para recuperarlas directamente desde el panel.

## 6.5. Gestión de pedidos

El administrador y los vendedores autorizados podrán consultar los pedidos realizados por los clientes.

La lista incluirá:

- Código del pedido.
- Fecha.
- Cliente.
- Importe.
- Estado del pago.
- Estado del pedido.
- Modalidad de entrega.
- Usuario responsable de la atención, si corresponde.

Permitirá buscar, filtrar y consultar detalles.

Los estados de preparación y entrega deberán actualizarse mediante transiciones válidas. Por ejemplo, un pedido pagado podrá pasar a preparación y posteriormente a listo para recoger o entregado.

Los pedidos cancelados, rechazados o pendientes deberán recibir un tratamiento coherente con el estado real del pago y las reglas del negocio.

## 6.6. Detalle y atención de pedidos

La vista detallada mostrará:

- Datos del pedido y del cliente.
- Productos, cantidades y precios.
- Descuentos e importe total.
- Información de pago.
- Estado de preparación y entrega.
- Comprobante asociado.
- Historial de cambios relevantes.
- Acciones permitidas según el rol.

El sistema deberá conservar la trazabilidad de las modificaciones importantes, especialmente aquellas relacionadas con pagos, cancelaciones y cambios de estado.

## 6.7. Gestión de inventario

El módulo permitirá consultar las existencias y mantener un registro consistente del movimiento de productos.

Deberá incluir:

- Stock actual por producto.
- Entradas de inventario.
- Salidas de inventario.
- Ajustes autorizados.
- Historial de movimientos.
- Productos agotados.
- Productos con bajo stock.
- Filtros por categoría, marca y estado.
- Acceso a los detalles del producto.

Las operaciones deberán registrar la fecha, el producto, la cantidad, el tipo de movimiento y el usuario responsable cuando corresponda.

El stock deberá actualizarse conforme a las reglas definidas para las ventas, cancelaciones y devoluciones. Las operaciones concurrentes deberán protegerse para evitar existencias negativas o ventas duplicadas.

## 6.8. Alertas de inventario

El sistema generará alertas cuando los productos alcancen el nivel mínimo de stock configurado o se agoten.

La pantalla mostrará:

- Producto afectado.
- Stock actual.
- Stock mínimo.
- Fecha de generación de la alerta.
- Estado de la alerta.
- Acceso al detalle del producto.
Opciones autorizadas para revisar o actualizar el inventario.

Podrá incluir sugerencias de reposición basadas en el stock mínimo y en el historial de ventas, sin realizar compras automáticas a proveedores.

6.9. Reportes de ventas

El módulo permitirá analizar las ventas registradas en el sistema.

Deberá incluir filtros por:

Fecha o período.

Producto.

Categoría.

Vendedor, cuando corresponda.

Estado de la operación.

Tipo de venta, si se manejan distintas modalidades.

Los reportes podrán mostrar:

Total vendido.

Número de operaciones.

Productos más vendidos.

Ventas por período.

Ventas por categoría.

Ventas por vendedor, cuando corresponda.

Descuentos aplicados.

Operaciones canceladas y devoluciones, cuando estén implementadas.

Se deberá diferenciar el total de pedidos del importe efectivamente cobrado. Las operaciones pendientes, rechazadas o canceladas no deberán contabilizarse como ventas pagadas.

Los resultados podrán visualizarse en tablas y gráficos, imprimirse o exportarse a Excel.

6.10. Configuración de roles y permisos

El sistema diferenciará, como mínimo, los roles de administrador y vendedor.

El administrador podrá gestionar las autorizaciones de los usuarios internos, dentro de las reglas de seguridad del sistema.

Los permisos podrán definir el acceso a:

Productos.

Inventario.

Pedidos.

Clientes.

Reportes.

Configuración de usuarios.

Exportación de información.

Operaciones administrativas sensibles.

El vendedor solo tendrá acceso a las funciones que necesite para sus tareas. Las restricciones se verificarán tanto en la interfaz como en el backend.

6.11. Exportación de información

Los módulos que lo requieran permitirán exportar información en formatos apropiados, como Excel o CSV.

La exportación deberá respetar los filtros aplicados, los permisos del usuario y la estructura de los datos. Los documentos no deberán incluir información personal innecesaria.

7. Módulo de soporte y comunicación

La plataforma dispondrá de medios para facilitar la comunicación entre los clientes y la empresa.

Podrá incluir:

Correo electrónico de contacto.

Acceso al canal oficial de WhatsApp.

Información de atención.

Dirección del establecimiento.

Consultas relacionadas con pedidos.

Mensajes sobre el estado de la compra.

Envío de correos de recuperación de contraseña y notificaciones implementadas.

Los mensajes deberán utilizar datos actualizados y no mostrar información de pedidos a personas no autorizadas.

8. Reglas de negocio fundamentales

El sistema deberá respetar reglas comunes en todos los módulos:

No se podrá completar una compra con productos inexistentes o cantidades superiores al stock disponible.

Los precios, descuentos, impuestos aplicables y totales deberán calcularse o validarse en el backend.

Un pedido no se considerará pagado hasta que el pago haya sido verificado.

La recepción repetida de una notificación de pago no deberá generar cobros, pedidos o movimientos de inventario duplicados.

El stock deberá actualizarse de manera consistente con las ventas, cancelaciones y devoluciones.

Cada cliente solo podrá acceder a sus propios datos, pedidos y comprobantes.

Los vendedores solo podrán ejecutar operaciones autorizadas.

Las operaciones administrativas importantes deberán quedar registradas cuando corresponda.

Los reportes deberán utilizar los mismos criterios de cálculo que los módulos de pedidos y pagos.

Los comprobantes deberán mantener su relación con la operación que los originó.

Los productos desactivados no deberán aparecer como disponibles para nuevas compras.

Los errores de comunicación con servicios externos deberán tratarse sin marcar incorrectamente una compra como exitosa.

Las operaciones que afecten simultáneamente al pedido, al pago y al inventario deberán diseñarse para mantener la coherencia de los datos.

9. Requisitos no funcionales

9.1. Seguridad

Contraseñas almacenadas mediante hash seguro.

Validación de permisos en el backend.

Protección de sesiones y rutas privadas.

Uso de HTTPS en el entorno de producción.

Validación de entradas y consultas seguras a la base de datos.

Gestión de credenciales mediante variables de entorno.

Protección de secretos y exclusión de archivos sensibles del repositorio.

Verificación de notificaciones de pago.

Registro de errores sin exponer credenciales ni datos sensibles.

Copias de seguridad y procedimientos de recuperación para el entorno real.

9.2. Usabilidad y accesibilidad

Interfaz clara, consistente y fácil de aprender.

Diseño adaptable a computadoras y dispositivos móviles.

Formularios con etiquetas y validaciones comprensibles.

Mensajes claros de éxito, advertencia y error.

Navegación coherente entre módulos.

Contraste y tamaño de texto adecuados.

Uso de controles accesibles mediante teclado cuando corresponda.

Prevención de errores y confirmación para operaciones delicadas.

9.3. Rendimiento

Consultas eficientes a la base de datos.

Paginación en listados extensos.

Carga optimizada de imágenes.

Índices en los campos consultados con frecuencia.

Tratamiento adecuado de errores y tiempos de espera.

Evitar cálculos innecesarios o consultas repetitivas.

Los objetivos de rendimiento deberán establecerse mediante pruebas con una cantidad representativa de productos, pedidos y usuarios.

9.4. Mantenibilidad y escalabilidad

Código organizado por módulos y responsabilidades.

Separación entre interfaz, lógica de negocio y acceso a datos.

Servicios externos integrados mediante componentes independientes.

Configuración separada del código.

Documentación de instalación y funcionamiento.

Control de versiones.

Pruebas automatizadas para las operaciones principales.

Posibilidad de ampliar los módulos sin reescribir toda la aplicación.

9.5. Integridad y disponibilidad de los datos

Relaciones correctamente definidas entre usuarios, productos, pedidos, pagos y comprobantes.

Restricciones para evitar registros inválidos.

Transacciones de base de datos cuando una operación requiera actualizar varios registros relacionados.

Tratamiento de errores sin dejar información parcialmente actualizada.

Estrategia de copias de seguridad para el entorno real.

10. Arquitectura y tecnologías

La implementación deberá respetar las tecnologías y la estructura que se hayan definido para el proyecto. Antes de introducir nuevas herramientas, se deberá revisar el sistema existente.

La arquitectura deberá separar, conceptualmente, las siguientes responsabilidades:

Frontend: catálogo, detalle de productos, carrito, formularios, perfil del cliente y panel administrativo.

Backend: autenticación, autorización, catálogo, pedidos, inventario, pagos, comprobantes, notificaciones y reportes.

Base de datos: usuarios, roles, productos, categorías, marcas, imágenes o sus referencias, existencias, movimientos de inventario, pedidos, detalles de pedido, pagos, comprobantes y registros necesarios para el funcionamiento.

Servicios externos: pasarela de pago, correo electrónico y, si corresponde, proveedor de facturación electrónica.

La comunicación entre frontend y backend deberá utilizar mecanismos definidos y consistentes. Las operaciones críticas no deberán depender únicamente de validaciones del navegador.

La base de datos existente deberá conservarse. Los cambios de estructura deberán realizarse únicamente cuando sean necesarios, estén justificados y hayan sido autorizados. Antes de modificar tablas o relaciones, se deberá evaluar el impacto en los datos y en los módulos que ya funcionan.

La integración con la pasarela de pago deberá estar desacoplada del resto del sistema para facilitar un cambio futuro de proveedor.

11. Pruebas y criterios de aceptación

El sistema no se considerará terminado únicamente porque las pantallas estén diseñadas. Sus principales funcionalidades deberán estar conectadas y verificadas.

Se deberán realizar pruebas de los siguientes escenarios:

Registrar una cuenta e iniciar sesión.

Recuperar la contraseña.

Buscar y filtrar productos.

Consultar los detalles de un producto.

Agregar, modificar y eliminar productos del carrito.

Impedir la compra de cantidades no disponibles.

Completar una compra de prueba con un pago aprobado.

Gestionar pagos rechazados, pendientes o cancelados.

Evitar la duplicación de pedidos y movimientos.

Actualizar el stock según las reglas de negocio.

Consultar pedidos y comprobantes desde el perfil del cliente.

Restringir el acceso a pedidos de otros usuarios.

Registrar y modificar productos desde el panel administrativo.

Aplicar correctamente los permisos de administrador y vendedor.

Generar alertas de stock mínimo.

Generar reportes y comprobar sus cálculos.

Exportar información con filtros aplicados.

Comprobar el comportamiento de la aplicación en distintos tamaños de pantalla.

Verificar que los errores de servicios externos no generen confirmaciones falsas.

Cada prueba deberá contar con un resultado esperado y un resultado obtenido. Los errores encontrados deberán corregirse y volver a probarse.

12. Alcance por etapas de desarrollo

Para evitar desarrollar demasiadas funcionalidades al mismo tiempo, el sistema se implementará progresivamente.

Etapa 1. Base del sistema

Revisar el proyecto y la base de datos existentes.

Identificar módulos, tecnologías y funcionalidades actuales.

Definir las relaciones y reglas de negocio necesarias.

Organizar la estructura del código.

Implementar autenticación, roles y permisos básicos.

Etapa 2. Tienda virtual

Desarrollar el catálogo.

Implementar búsquedas y filtros.

Crear el detalle de productos.

Implementar favoritos.

Desarrollar el carrito de compras.

Validar cantidades, precios y disponibilidad.

Etapa 3. Compras y pagos

Implementar el proceso de compra.

Registrar pedidos y sus detalles.

Integrar la pasarela en modo de pruebas.

Verificar los resultados de los pagos.

Gestionar estados del pedido.

Implementar la confirmación y las notificaciones básicas.

Etapa 4. Perfil y comprobantes

Implementar el perfil del cliente.

Desarrollar el historial y detalle de pedidos.

Implementar la visualización y descarga de documentos.

Incorporar el envío de correos cuando corresponda.

Preparar la integración de facturación electrónica si forma parte del alcance definitivo.

Etapa 5. Administración

Implementar el dashboard.

Desarrollar la gestión de productos.

Implementar la gestión de usuarios y pedidos.

Desarrollar el inventario y las alertas.

Implementar reportes y exportaciones.

Aplicar y probar los permisos de cada rol.

Etapa 6. Integración y validación

Verificar las conexiones entre todos los módulos.

Corregir errores.

Ejecutar pruebas funcionales y de seguridad.

Comprobar los cálculos de ventas y existencias.

Optimizar la interfaz.

Documentar el funcionamiento del sistema.

13. Resultado final esperado

Al finalizar el desarrollo, se deberá contar con una plataforma integrada de comercio electrónico en la que el cliente pueda consultar productos, registrarse, comprar, realizar pagos de prueba, revisar sus pedidos y acceder a sus comprobantes.

El administrador podrá controlar los productos, usuarios, pedidos, inventario, permisos y reportes, mientras que los vendedores tendrán acceso a las operaciones autorizadas para sus funciones.

Los módulos deberán compartir información de manera coherente, evitando inconsistencias entre los pedidos, los pagos y las existencias. La interfaz deberá ser clara y adaptable, y las operaciones sensibles deberán contar con validaciones de seguridad.

El sistema deberá estar preparado para evolucionar mediante nuevas funcionalidades, cambios de pasarela de pago e integraciones adicionales, sin exigir una reconstrucción completa de la plataforma.

Para la sustentación académica, deberá poder demostrarse un recorrido funcional completo: búsqueda de un producto, incorporación al carrito, creación del pedido, pago de prueba, confirmación, actualización del inventario y consulta del resultado desde el panel administrativo.

El éxito del proyecto se medirá por la integración real de sus funcionalidades, la consistencia de los datos, la seguridad, la facilidad de uso y la capacidad de demostrar los procesos principales, no por la cantidad de pantallas desarrolladas.
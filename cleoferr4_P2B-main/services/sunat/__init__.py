"""Paquete SUNAT (Fase 2.2): consulta de documentos y comprobantes.

Los módulos de negocio importan SOLO base.py (tipos neutrales) y
registro.py (fábrica). Nada de simulado.py / real.py / apisperu.py fuera
del paquete: lo vigila tests/test_arquitectura.py con análisis de imports
(ast), igual que services/pasarela.
"""
from .base import (ComprobanteResultado, ComprobanteSolicitud,
                   ConsultaDocumento, EstadoComprobanteSunat, ProveedorSunat)
from .registro import crear_proveedor_sunat

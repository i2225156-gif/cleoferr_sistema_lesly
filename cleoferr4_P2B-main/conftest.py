"""Configuración canónica del entorno de tests.

Se ejecuta ANTES de que los módulos de test importen app (que instancia la
pasarela en el arranque). PAGO_MODO solo admite 'simulado' (decisión del
propietario); el .env de producción debe corregirse aparte.
"""
import os

os.environ["PAGO_MODO"] = "simulado"

# Sistema CLEOFERR

## Configuración local

La aplicación necesita dos conexiones PostgreSQL de Supabase: una para
autenticación y otra para la tienda.

1. Copia `.env.example` como `.env`.
2. Completa `SECRET_KEY`, `DATABASE_URI_AUTH` y `DATABASE_URI_TIENDA` con los
	valores de tus proyectos Supabase.
3. Instala las dependencias y arranca desde esta carpeta:

```powershell
python -m pip install -r requirements.txt
python app.py
```

No subas `.env` al repositorio: contiene credenciales.

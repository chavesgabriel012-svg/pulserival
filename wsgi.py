"""Punto de entrada del servidor web para un hosting que lo busca solo.

Vercel busca una instancia de Flask llamada `app` en `wsgi.py` (entre otros
nombres) y con eso despliega todo el servidor como una sola función, sin
configuración extra. Este archivo existe únicamente para eso: la aplicación de
verdad está en `pulserival/web/app.py`, que es la misma que levanta gunicorn.

En Vercel el alta NO va a una base SQLite —el disco es efímero y se perdería—
sino a un YAML en el repositorio. Eso lo decide `pulserival/web/deposito.py`,
que en Vercel usa 'github' por defecto. Ver docs/09-deploy.md.
"""
from pulserival.web.app import crear_app

app = crear_app()

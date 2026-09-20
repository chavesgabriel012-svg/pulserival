# Atajos para operar PulseRival sin acordarse de comandos largos.
# Uso: make demo, make prueba, make ciclo, ...

PY ?= python3

.PHONY: ayuda instalar init demo ciclo recolectar reporte prueba dataset costos limpiar

ayuda:
	@echo "PulseRival — comandos disponibles"
	@echo ""
	@echo "  make instalar    instala las 2 librerías necesarias"
	@echo "  make demo        prueba TODO el flujo con datos de ejemplo (sin claves, sin costo)"
	@echo "  make init        crea la base de datos real"
	@echo "  make ciclo       recolecta + genera borradores (lo que corre el cron)"
	@echo "  make recolectar  solo traer anuncios"
	@echo "  make prueba      corre los tests"
	@echo "  make dataset     exporta el dataset de ediciones (Fase 2)"
	@echo "  make costos      cuánto se gastó en IA"
	@echo "  make limpiar     borra archivos temporales y la base de la demo"
	@echo ""
	@echo "  Todo lo demás:   $(PY) -m pulserival.cli --help"

instalar:
	$(PY) -m pip install -r requirements.txt

init:
	$(PY) -m pulserival.cli init

demo:
	$(PY) -m pulserival.cli demo

ciclo:
	$(PY) -m pulserival.cli ciclo

recolectar:
	$(PY) -m pulserival.cli recolectar

prueba:
	$(PY) -m unittest discover -s tests -t . -v

dataset:
	$(PY) -m pulserival.cli dataset

costos:
	$(PY) -m pulserival.cli costos

limpiar:
	rm -rf datos/demo.db datos/crudo salida/*.html salida/*.txt salida/*.jsonl
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

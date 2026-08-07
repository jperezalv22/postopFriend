# postopFriend — atajos. Todo funciona igual sin make; ver el README.
#
# En Windows sin make instalado, use el comando de la derecha directamente.

PY := .venv/Scripts/python.exe
ifeq ($(OS),)
  PY := .venv/bin/python
endif

.PHONY: ayuda setup doctor run test ingest eval metricas modelos voz limpiar

ayuda:
	@echo "setup     preparar el entorno desde cero (setup.ps1 / setup.sh)"
	@echo "doctor    diagnostico de entorno en 10 s"
	@echo "run       arrancar el servidor en http://127.0.0.1:8000"
	@echo "test      pruebas rapidas, sin API (<5 s)"
	@echo "ingest    reconstruir el indice RAG desde dataset/textos"
	@echo "eval      correr las evaluaciones y escribir evals/results/"
	@echo "metricas  regenerar la tabla de metricas del README desde los logs"
	@echo "modelos   catalogo vivo de Groq (evidencia de la compuerta G3)"
	@echo "voz       probar TTS y STT de ida y vuelta, sin microfono"

setup:
	@echo "Windows:  .\\setup.ps1"
	@echo "Linux/mac: bash setup.sh"

doctor:
	$(PY) scripts/doctor.py

run:
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

test:
	$(PY) -m pytest tests/ -q

ingest:
	$(PY) scripts/build_index.py --limpiar

eval:
	$(PY) evals/run_triage_eval.py
	$(PY) evals/run_rag_eval.py
	$(PY) evals/run_safety_eval.py

metricas:
	$(PY) scripts/report_metrics.py

modelos:
	$(PY) scripts/check_models.py

voz:
	$(PY) scripts/probar_voz.py

limpiar:
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"

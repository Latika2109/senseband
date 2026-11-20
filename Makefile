PY ?= .venv/bin/python
SCENARIO ?= resting
DURATION ?= 600
SPEED ?= 1
SIMFLAGS ?=

.PHONY: setup sim sim-stream sim-list edge app test lint train

setup:            ## create .venv (Python 3.11) and install dev deps
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) numpy scipy pyyaml pydantic fastapi "uvicorn[standard]" httpx reportlab cryptography onnxruntime lightgbm scikit-learn onnxmltools skl2onnx wfdb pytest ruff pre-commit

sim:              ## edge service on simulated sensors: make sim SCENARIO=fall SPEED=1  (API on :8080)
	SENSEBAND_SIM=1 PYTHONPATH=edge:ml $(PY) -m senseband_edge --sim \
		--scenario $(SCENARIO) --speed $(SPEED) --db :memory: $(SIMFLAGS)

sim-stream:       ## raw simulated sensor stream only (Milestone 0 CLI): make sim-stream SCENARIO=jogging
	SENSEBAND_SIM=1 PYTHONPATH=edge:ml $(PY) -m senseband_edge.hw.sim \
		--scenario $(SCENARIO) --duration $(DURATION) --speed $(SPEED)

sim-list:         ## list available scenarios
	PYTHONPATH=edge:ml $(PY) -m senseband_edge.hw.sim --list

edge:             ## edge service on the Pi (real sensors)
	PYTHONPATH=edge:ml $(PY) -m senseband_edge --hw

app:              ## PWA dev server (proxies /api and /ws to the edge on :8080: run `make sim` in another terminal)
	cd app && npm install && npm run dev

lint:
	$(PY) -m ruff check edge ml

test: lint        ## all Python tests (edge + ml). App: (cd app && npm test)  Cloud: (cd cloud && npm test)
	SENSEBAND_SIM=1 $(PY) -m pytest

train:            ## train all models (downloads datasets on first run): writes ml/models/*.onnx + metrics.json
	$(PY) -I -c "import sys; sys.path[:0]=['ml','edge']; from senseband_train.prepare import main; main(['all'])"
	$(PY) -I -c "import sys; sys.path[:0]=['ml','edge']; from senseband_train.__main__ import main; sys.argv=['senseband_train','all']; sys.exit(main())"

bench:            ## inference latency of every ONNX model on THIS machine (run it on the Pi 3 too)
	PYTHONPATH=ml:edge $(PY) -m senseband_train.bench

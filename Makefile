VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: setup models build up dev down clean

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found - install it (brew install ffmpeg / choco install ffmpeg) then re-run make setup"; exit 1; }
	@command -v espeak-ng >/dev/null 2>&1 || command -v espeak >/dev/null 2>&1 || { echo "espeak-ng not found - install it (brew install espeak-ng / apt install espeak-ng) then re-run make setup"; exit 1; }
	@if [ -d frontend ]; then cd frontend && npm install; fi

models:
	$(PY) -c "from audiobookify.engines.chatterbox import ChatterboxEngine; e = ChatterboxEngine(); e.load('cpu'); e.teardown(); print('Chatterbox weights cached.')"
	$(PY) -c "from audiobookify.phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor; e = Wav2Vec2PhonemeExtractor(); e.load('cpu'); e.teardown(); print('Phoneme recognition weights cached.')"

build:
	cd frontend && npm run build

up: build
	mkdir -p .run
	$(VENV)/bin/uvicorn audiobookify.api.app:create_app --factory --host 127.0.0.1 --port 8000 & echo $$! > .run/api.pid
	$(VENV)/bin/audiobookify worker & echo $$! > .run/worker.pid
	@echo "audiobookify running at http://127.0.0.1:8000 - 'make down' to stop."

dev:
	mkdir -p .run
	$(VENV)/bin/uvicorn audiobookify.api.app:create_app --factory --reload --host 127.0.0.1 --port 8000 & echo $$! > .run/api.pid
	$(VENV)/bin/audiobookify worker & echo $$! > .run/worker.pid
	cd frontend && npm run dev & echo $$! > .run/frontend.pid
	@echo "dev stack running - api http://127.0.0.1:8000, frontend http://127.0.0.1:5173 - 'make down' to stop."

down:
	@for f in .run/*.pid; do [ -f "$$f" ] && kill "$$(cat "$$f")" 2>/dev/null; rm -f "$$f"; done
	@echo "stopped."

clean:
	rm -rf $(VENV) build dist src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache

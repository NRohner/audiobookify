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
	@echo "no frontend yet - 'build' arrives with the GUI (M4)"

up:
	@echo "no server yet - 'up' arrives with the GUI (M4); use 'audiobookify generate ...' directly for now"

dev:
	@echo "no dev server yet - 'dev' arrives with the GUI (M4)"

down:
	@echo "nothing running - 'down' arrives with the GUI (M4)"

clean:
	rm -rf $(VENV) build dist src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache

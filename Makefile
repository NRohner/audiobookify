VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# On WSL, npm/node on PATH can resolve to a Windows install (via interop with
# /mnt/c/...), which fails on WSL-style paths. Prefer a native nvm-installed node
# when present so `make build`/`make dev` work the same on WSL as on macOS/Linux.
# Use nvm's default alias (not the alphabetically-first version dir) so this
# matches whatever `nvm use default` gives you in an interactive shell -
# picking the wrong installed version here breaks on APIs newer versions add
# (e.g. node:crypto.getRandomValues, missing before Node 17.4).
NVM_DEFAULT := $(shell cat $(HOME)/.nvm/alias/default 2>/dev/null)
NVM_NODE_BIN := $(firstword $(wildcard $(HOME)/.nvm/versions/node/v$(NVM_DEFAULT)*/bin))
ifneq ($(NVM_NODE_BIN),)
export PATH := $(NVM_NODE_BIN):$(PATH)
endif

.PHONY: setup models build up dev app-dev app down clean check-not-running

setup:
	python3.12 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found - install it (brew install ffmpeg / choco install ffmpeg) then re-run make setup"; exit 1; }
	@command -v espeak-ng >/dev/null 2>&1 || command -v espeak >/dev/null 2>&1 || { echo "espeak-ng not found - install it (brew install espeak-ng / apt install espeak-ng) then re-run make setup"; exit 1; }
	@if [ -d frontend ]; then cd frontend && npm install; fi

models:
	$(PY) -c "from audiobookify.engines.turbo import ChatterboxTurboEngine; e = ChatterboxTurboEngine(); e.load(); e.teardown(); print('Chatterbox Turbo weights cached.')"
	$(PY) -c "from audiobookify.phoneme.wav2vec2 import Wav2Vec2PhonemeExtractor; e = Wav2Vec2PhonemeExtractor(); e.load('cpu'); e.teardown(); print('Phoneme recognition weights cached.')"

build:
	cd frontend && npm run build


# Refuses to start a second stack on top of a live one: a prior `make up`/`make dev`
# whose port was already taken fails silently, leaving the *old* (possibly stale-code)
# process as the one actually serving requests while its pidfile gets overwritten by
# the new, dead one. That masks code changes behind a server that never picked them up.
check-not-running:
	@for f in .run/api.pid .run/worker.pid .run/frontend.pid; do \
		if [ -f "$$f" ] && kill -0 "$$(cat "$$f")" 2>/dev/null; then \
			echo "audiobookify already running (pid $$(cat "$$f") from $$f) - run 'make down' first."; \
			exit 1; \
		fi; \
	done

up: check-not-running build
	mkdir -p .run
	HF_HUB_OFFLINE=1 $(VENV)/bin/uvicorn audiobookify.api.app:create_app --factory --host 127.0.0.1 --port 8000 & echo $$! > .run/api.pid
	HF_HUB_OFFLINE=1 $(VENV)/bin/audiobookify worker & echo $$! > .run/worker.pid
	@echo "audiobookify running at http://127.0.0.1:8000 - 'make down' to stop."

dev: check-not-running
	mkdir -p .run
	HF_HUB_OFFLINE=1 $(VENV)/bin/uvicorn audiobookify.api.app:create_app --factory --reload --host 127.0.0.1 --port 8000 & echo $$! > .run/api.pid
	HF_HUB_OFFLINE=1 $(VENV)/bin/audiobookify worker & echo $$! > .run/worker.pid
	cd frontend && npm run dev & echo $$! > .run/frontend.pid
	@echo "dev stack running - api http://127.0.0.1:8000, frontend http://127.0.0.1:5173 - 'make down' to stop."

# Native window around the same API + worker. Quitting the app stops both.
app-dev: check-not-running
	cd desktop && cargo tauri dev

# Builds desktop/target/release/bundle/macos/Audiobookify.app. It runs this checkout's
# .venv, so keep the repo where it is.
app:
	cd desktop && cargo tauri build
	@echo "built desktop/target/release/bundle/macos/Audiobookify.app"

down:
	@for f in .run/*.pid; do [ -f "$$f" ] && kill "$$(cat "$$f")" 2>/dev/null; rm -f "$$f"; done
	@command -v lsof >/dev/null 2>&1 && lsof -ti:5173 | xargs kill 2>/dev/null; true
	@echo "stopped."

clean:
	rm -rf $(VENV) build dist src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache

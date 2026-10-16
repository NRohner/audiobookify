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

.PHONY: setup models build up dev app down clean

setup:
	python3.12 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found - install it (brew install ffmpeg / choco install ffmpeg) then re-run make setup"; exit 1; }
	@if [ -d frontend ]; then cd frontend && npm install; fi

models:
	$(PY) -c "from audiobookify.engines.turbo import ChatterboxTurboEngine; e = ChatterboxTurboEngine(); e.load(); e.teardown(); print('Chatterbox Turbo weights cached.')"

build:
	cd frontend && npm run build


# The desktop app with hot reload. Data lives in ~/Library/Application Support/com.audiobookify.app.
dev:
	cd desktop && cargo tauri dev

# Builds desktop/target/release/bundle/macos/Audiobookify.app. It runs this checkout's
# .venv, so keep the repo where it is.
app:
	cd desktop && cargo tauri build
	@echo "built desktop/target/release/bundle/macos/Audiobookify.app"

up: app
	open desktop/target/release/bundle/macos/Audiobookify.app

# Stops a Vite dev server left behind by an interrupted `make dev`.
down:
	@command -v lsof >/dev/null 2>&1 && lsof -ti:5173 | xargs kill 2>/dev/null; true
	@echo "stopped."

clean:
	rm -rf $(VENV) build dist src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache

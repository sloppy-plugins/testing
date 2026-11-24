.DEFAULT_GOAL := help
SHELL := /bin/sh
PYTHON ?= python3
SLOPPY ?= sloppy
PYPI_INDEX ?= https://pypi.org/simple/
ARGS ?=
TEST_ARGS ?=
SDK_SOURCE ?=
PLUGIN_VERSION := $(shell $(PYTHON) -c 'import json, pathlib; print(json.loads(pathlib.Path("plugin.json").read_text())["plugin"]["version"])')
-include local.mk
export PYTHONWARNINGS := error
export PYTHONDONTWRITEBYTECODE := 1
export PIP_INDEX_URL := $(PYPI_INDEX)
export SDK_SOURCE
PY := $(CURDIR)/.venv/bin/python
ifeq ($(OS),Windows_NT)
PY := $(CURDIR)/.venv/Scripts/python.exe
endif

.PHONY: help deps build package install installed-deps enable disable run dev test lint format format-check check clean

help: ## List available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z0-9-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

deps: ## Install the SDK and TUI dependencies into this checkout's .venv
	$(PYTHON) manage.py deps

build: package ## Build the distributable plugin directory

package: ## Stage a clean package without environments or caches
	$(PYTHON) manage.py package

install: package ## Install the package and provision its private Python environment
	"$(SLOPPY)" plugins install "$(CURDIR)/build/package"
	$(MAKE) installed-deps

installed-deps: ## Set up or repair dependencies for an already installed copy
	$(PYTHON) manage.py installed-deps --cli "$(SLOPPY)"

enable: ## Trust the installed adapter and enable its service mirroring
	"$(SLOPPY)" plugins enable "testing@$(PLUGIN_VERSION)"

disable: ## Disable the installed adapter and its service mirroring
	"$(SLOPPY)" plugins disable "testing@$(PLUGIN_VERSION)"

run: ## Run the installed plugin (ARGS=--list works without a service)
	"$(SLOPPY)" "testing@$(PLUGIN_VERSION)" $(ARGS)

dev: ## Run this checkout directly, using its .venv
	"$(PY)" testing.py $(ARGS)

test: ## Run standalone unit/TUI tests; external service integration is optional
	"$(PY)" -W error -m unittest discover -s . -p 'test_*.py' -v $(TEST_ARGS)

lint: ## Check Python source and tests
	"$(PY)" -m ruff check .

format: ## Format Python source and tests
	"$(PY)" -m ruff format .

format-check: ## Check Python formatting without changing files
	"$(PY)" -m ruff format --check .

check: lint format-check test ## Run all plugin checks

clean: ## Remove generated packages and Python caches, preserving .venv
	$(PYTHON) manage.py clean

PYTHON ?= python3
NPM ?= npm

.PHONY: check test backend-test frontend-install frontend-check frontend-build assets-check

check: backend-test frontend-check frontend-build assets-check

test: backend-test frontend-check

backend-test:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

frontend-install:
	$(NPM) --prefix frontend ci

frontend-check:
	$(NPM) --prefix frontend run check

frontend-build:
	$(NPM) --prefix frontend run build

assets-check:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src $(PYTHON) -c 'from gate_bridge.dashboard import missing_dashboard_assets; missing = missing_dashboard_assets(); assert not missing, missing'

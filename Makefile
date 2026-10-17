PY := .venv/bin/python

.PHONY: setup run demo-site run-demo seed test

setup:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements.txt
	$(PY) -m playwright install chromium
	@test -f .env || cp .env.example .env
	@echo "Setup done. Run: make run"

run:
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Demo: 3 terminals -> `make demo-site`, `make run-demo`, `make seed`
demo-site:
	$(PY) scripts/demo_site.py

run-demo:
	ALLOW_PRIVATE_HOSTS=1 MIN_INTERVAL_MINUTES=1 DOMAIN_MIN_GAP_S=2 DOMAIN_JITTER_S=1 \
	CONFIRM_DELAY_S=6 MAX_RETRIES=2 RETRY_BASE_S=1 ERROR_ALERT_AFTER=2 \
	$(PY) -m uvicorn app.main:app --port 8000

seed:
	$(PY) scripts/seed_demo.py

test:
	$(PY) -m pytest -q

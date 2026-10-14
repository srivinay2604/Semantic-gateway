.PHONY: dev test lint format typecheck up down clean install

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
install:
	pip install -e ".[dev]"

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------
dev:
	uvicorn cachegate.main:app --reload --host 0.0.0.0 --port 8000 \
		--log-level info --factory

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-api:
	pytest tests/api/ -v

# ---------------------------------------------------------------------------
# Lint / Format / Type check
# ---------------------------------------------------------------------------
lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

format-check:
	ruff format --check src/ tests/

typecheck:
	mypy src/

# Run all checks in CI order
check: lint format-check typecheck

# ---------------------------------------------------------------------------
# Docker Compose
# ---------------------------------------------------------------------------
up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f gateway

# ---------------------------------------------------------------------------
# Benchmarks (Phase 9)
# ---------------------------------------------------------------------------
bench:
	@echo "Benchmarks are scaffolded for Phase 9. Run: cd benchmarks && locust -f locustfile.py"

# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true

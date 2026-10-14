"""Pricing table loader and cost calculation based on spec §11.4."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from cachegate.settings import settings

logger = structlog.get_logger(__name__)


class PricingTable:
    """Loads config/pricing.yaml and computes cost for models."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        path = Path(config_path or settings.pricing_config_path)
        self._pricing: dict[str, dict[str, float]] = {}
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data: dict[str, Any] = yaml.safe_load(f) or {}
                self._pricing = data.get("models", {})
                logger.info("pricing_table_loaded", models_count=len(self._pricing))
            except Exception as exc:
                logger.warning("pricing_table_load_error", path=str(path), error=str(exc))
        else:
            logger.warning("pricing_table_file_not_found", path=str(path))

    def get_rates(self, model: str) -> tuple[float, float]:
        """Return (input_rate_per_1m, output_rate_per_1m) for a model."""
        entry = self._pricing.get(model)
        if entry is None:
            # Check if model has a provider prefix or alias
            for key, val in self._pricing.items():
                if model.endswith(key) or key.endswith(model):
                    return float(val.get("input_per_1m", 0.0)), float(val.get("output_per_1m", 0.0))
            return 0.0, 0.0
        return float(entry.get("input_per_1m", 0.0)), float(entry.get("output_per_1m", 0.0))

    def calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate USD cost for the given token counts."""
        input_rate, output_rate = self.get_rates(model)
        cost = (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000.0
        return round(cost, 6)

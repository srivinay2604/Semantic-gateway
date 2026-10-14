"""Unit tests for PricingTable and cost calculations."""

from __future__ import annotations

from cachegate.usage.pricing import PricingTable


def test_pricing_table_loads_models() -> None:
    pricing = PricingTable("config/pricing.yaml")
    input_rate, output_rate = pricing.get_rates("gpt-4o-mini")
    assert input_rate == 0.15
    assert output_rate == 0.60


def test_pricing_table_mock_is_zero_cost() -> None:
    pricing = PricingTable("config/pricing.yaml")
    input_rate, output_rate = pricing.get_rates("mock/echo")
    assert input_rate == 0.0
    assert output_rate == 0.0


def test_calculate_cost() -> None:
    pricing = PricingTable("config/pricing.yaml")
    # 1000 prompt tokens * $0.15/1M + 2000 completion tokens * $0.60/1M
    # = 0.00015 + 0.0012 = 0.00135
    cost = pricing.calculate_cost("gpt-4o-mini", prompt_tokens=1000, completion_tokens=2000)
    assert cost == 0.00135


def test_unknown_model_returns_zero() -> None:
    pricing = PricingTable("config/pricing.yaml")
    cost = pricing.calculate_cost("nonexistent-model", prompt_tokens=100, completion_tokens=100)
    assert cost == 0.0

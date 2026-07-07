"""Running cost estimate. Not billing — a guardrail for the ~$200 budget.

Tokens are tracked per provider (openrouter / groq / openai / gemini). Rates come
from the config's pricing_usd_per_million_tokens as `<provider>_input` /
`<provider>_output`; a missing rate is treated as 0 (e.g. free models cost $0).
"""
from __future__ import annotations
from collections import defaultdict


class CostTracker:
    def __init__(self, pricing: dict):
        self.pricing = pricing or {}
        # provider -> [input_tokens, output_tokens]
        self.tokens = defaultdict(lambda: [0, 0])
        self.tavily_calls = 0
        self.reports_done = 0
        self.events = []

    def _rate(self, key: str) -> float:
        return float(self.pricing.get(key, 0.0))

    def add_llm(self, provider: str, in_tok: int, out_tok: int, note: str = ""):
        self.tokens[provider][0] += int(in_tok or 0)
        self.tokens[provider][1] += int(out_tok or 0)
        self.events.append({"type": provider, "in": in_tok, "out": out_tok, "note": note})

    def add_search(self, n: int = 1, note: str = ""):
        self.tavily_calls += n
        self.events.append({"type": "tavily", "calls": n, "note": note})

    @property
    def total_usd(self) -> float:
        m = 1_000_000
        total = self.tavily_calls * self._rate("tavily_per_search")
        for provider, (ti, to) in self.tokens.items():
            total += ti / m * self._rate(f"{provider}_input")
            total += to / m * self._rate(f"{provider}_output")
        return round(total, 4)

    @property
    def per_report_usd(self) -> float:
        return round(self.total_usd / self.reports_done, 4) if self.reports_done else 0.0

    def summary(self) -> dict:
        return {
            "tokens_by_provider": {p: {"in": t[0], "out": t[1]}
                                   for p, t in self.tokens.items()},
            "tavily_calls": self.tavily_calls,
            "reports_done": self.reports_done,
            "total_usd": self.total_usd,
            "per_report_usd": self.per_report_usd,
        }

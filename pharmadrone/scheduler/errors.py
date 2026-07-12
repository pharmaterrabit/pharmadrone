"""Safe scheduler error taxonomy."""
from __future__ import annotations
import re

ERROR_TYPES = {
    "temporary network failure", "rate limit", "authentication failure",
    "invalid response", "source schema change", "validation failure",
    "database failure", "budget limit", "unknown failure",
}


class SchedulerError(RuntimeError):
    def __init__(self, message: str, error_class: str = "unknown failure", *, retryable: bool = False):
        super().__init__(message)
        self.error_class = error_class if error_class in ERROR_TYPES else "unknown failure"
        self.retryable = retryable


def classify_error(message: str) -> tuple[str, bool]:
    text = (message or "").lower()
    if any(x in text for x in ("429", "rate limit")):
        return "rate limit", True
    if any(x in text for x in ("timeout", "connection failed", "temporar", "network", "502", "503", "504")):
        return "temporary network failure", True
    if any(x in text for x in ("401", "403", "unauthorized", "forbidden", "api key")):
        return "authentication failure", False
    if any(x in text for x in ("invalid json", "not valid json", "invalid response")):
        return "invalid response", False
    if any(x in text for x in ("schema", "missing expected field", "unexpected response shape")):
        return "source schema change", False
    if any(x in text for x in ("validation", "missing source id", "rejected")):
        return "validation failure", False
    if any(x in text for x in ("database", "postgres", "constraint", "transaction")):
        return "database failure", False
    if "budget" in text or "cap reached" in text:
        return "budget limit", False
    return "unknown failure", False


def safe_summary(value: str, limit: int = 240) -> str:
    text = re.sub(r"(?:postgres(?:ql)?://|postgresql\+psycopg://)[^\s]+", "[database URL redacted]", str(value or ""))
    text = re.sub(r"(?i)(api[_-]?key|token|password)=([^&\s]+)", r"\1=[redacted]", text)
    return " ".join(text.split())[:limit]

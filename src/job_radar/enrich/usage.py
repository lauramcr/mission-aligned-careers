"""Measure every Claude Code call: tokens, Claude Code's cost estimate, duration.

Claude Code reports usage with each `--output-format json` answer. The tool keeps one record
per call so runs can show their cost and `--limit` can be tuned to the user's plan.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Usage:
    """Tokens, cost estimate and duration of one model call, as Claude Code reports them."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0  # Claude Code's own estimate; with a subscription it is not billed
    duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        """All tokens of the call, including cached ones."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Usage":
        """Read the usage block of a Claude Code JSON answer (missing fields count as 0)."""
        usage = payload.get("usage") or {}

        def count(key: str) -> int:
            value = usage.get(key)
            return int(value) if isinstance(value, int | float) else 0

        return cls(
            input_tokens=count("input_tokens"),
            output_tokens=count("output_tokens"),
            cache_read_tokens=count("cache_read_input_tokens"),
            cache_write_tokens=count("cache_creation_input_tokens"),
            cost_usd=float(payload.get("total_cost_usd") or 0.0),
            duration_ms=int(payload.get("duration_ms") or 0),
        )


@dataclass(frozen=True)
class CallRecord:
    """One model call: what for, about which company, which model, whether it worked, usage."""

    purpose: str  # facts | resolve | names
    subject: str  # company id, name, ...
    model: str
    ok: bool
    usage: Usage


@dataclass(frozen=True)
class UsageSummary:
    """A run's model usage in totals, for reports and `radar doctor`."""

    calls: int
    failed: int
    total_tokens: int
    cost_usd: float
    by_purpose: dict[str, tuple[int, int]]  # purpose -> (calls, tokens)

    def per_call(self, purpose: str) -> int:
        """Average tokens per call for one purpose (facts, names, resolve)."""
        calls, tokens = self.by_purpose.get(purpose, (0, 0))
        return round(tokens / calls) if calls else 0

    def describe(self) -> str:
        """The usage in one readable line, e.g. "12 Claude calls · ~48,000 tokens"."""
        if not self.calls:
            return "no Claude calls"
        parts = [
            f"{self.calls} Claude calls" + (f" ({self.failed} failed)" if self.failed else ""),
            f"~{self.total_tokens:,} tokens",
        ]
        parts += [f"~{self.per_call(p):,} tokens per {p} call" for p in sorted(self.by_purpose)]
        parts.append(f"Claude Code cost estimate ${self.cost_usd:.2f}")
        return " · ".join(parts)


@dataclass
class UsageRecorder:
    """Collects every call of a run; saved to the llm_calls table at the end."""

    records: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> None:
        """Record one call."""
        self.records.append(record)

    def summary(self) -> UsageSummary:
        """Totals for the calls recorded so far."""
        by_purpose: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in self.records:
            by_purpose[r.purpose][0] += 1
            by_purpose[r.purpose][1] += r.usage.total_tokens
        return UsageSummary(
            calls=len(self.records),
            failed=sum(not r.ok for r in self.records),
            total_tokens=sum(r.usage.total_tokens for r in self.records),
            cost_usd=round(sum(r.usage.cost_usd for r in self.records), 4),
            by_purpose={k: (v[0], v[1]) for k, v in by_purpose.items()},
        )

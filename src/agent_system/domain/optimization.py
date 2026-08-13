from __future__ import annotations

from typing import Literal, Self

from pydantic import model_validator

from agent_system.domain.values import DomainModel

OptimizationMetric = Literal["fare", "duration", "stops", "departure_time"]
OptimizationDirection = Literal["minimize", "maximize"]
OptimizationBudgetRelation = Literal["ignore", "at_most", "near_limit"]


_LEGACY_SORT_PREFERENCES = {
    "cheapest": ("fare", "minimize"),
    "shortest": ("duration", "minimize"),
    "fewest_stops": ("stops", "minimize"),
    "earliest": ("departure_time", "minimize"),
    "latest": ("departure_time", "maximize"),
}


class OptimizationPreference(DomainModel):
    """A validated, provider-independent objective for ordering flight options."""

    metric: OptimizationMetric
    direction: OptimizationDirection
    budget_relation: OptimizationBudgetRelation = "ignore"

    @model_validator(mode="after")
    def validate_budget_relationship(self) -> Self:
        if self.metric == "fare" and self.direction == "maximize":
            if self.budget_relation == "ignore":
                raise ValueError(
                    "maximizing fare requires a budget ceiling or near-budget relationship"
                )
        elif self.budget_relation != "ignore":
            raise ValueError("budget relationships are only supported for fare optimization")
        return self


def optimization_preference(
    *,
    metric: OptimizationMetric,
    direction: OptimizationDirection,
    budget_relation: OptimizationBudgetRelation = "ignore",
) -> OptimizationPreference:
    """Create a canonical preference and make fare-maximization budget-safe."""

    if metric == "fare" and direction == "maximize" and budget_relation == "ignore":
        budget_relation = "at_most"
    return OptimizationPreference(
        metric=metric,
        direction=direction,
        budget_relation=budget_relation,
    )


def legacy_sort_preference(value: str) -> OptimizationPreference:
    """Translate the pre-optimization sort field without changing its meaning."""

    try:
        metric, direction = _LEGACY_SORT_PREFERENCES[value]
    except KeyError as exc:
        raise ValueError(f"unsupported legacy sort preference: {value}") from exc
    return optimization_preference(metric=metric, direction=direction)


__all__ = [
    "OptimizationBudgetRelation",
    "OptimizationDirection",
    "OptimizationMetric",
    "OptimizationPreference",
    "legacy_sort_preference",
    "optimization_preference",
]

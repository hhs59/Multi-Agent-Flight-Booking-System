from __future__ import annotations

import os
from dataclasses import dataclass


def _environment_bool(name: str) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return False

    normalized = raw_value.strip().casefold()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be one of: 1, 0, true, false, yes, no")


@dataclass(frozen=True)
class FeatureSettings:
    """Server-owned rollout controls for the backend improvement phases."""

    trip_discovery_enabled: bool = False
    flexible_search_enabled: bool = False
    flight_ranking_enabled: bool = False
    travel_preferences_enabled: bool = False
    destination_recommendations_enabled: bool = False
    places_llm_ranking_enabled: bool = False
    places_llm_generation_enabled: bool = False
    dynamic_location_resolution_enabled: bool = False
    trip_inspiration_enabled: bool = False
    semantic_updates_enabled: bool = False

    def __post_init__(self) -> None:
        """Reject rollout combinations that cannot produce a valid contract.

        LLM ranking is an optional refinement of destination recommendations. It
        must never be enabled independently, because doing so would allow an
        otherwise disabled feature to invoke an LLM provider.
        """

        if self.places_llm_ranking_enabled and not self.destination_recommendations_enabled:
            raise ValueError(
                "places_llm_ranking_enabled requires destination_recommendations_enabled"
            )
        if self.places_llm_generation_enabled and not self.destination_recommendations_enabled:
            raise ValueError(
                "places_llm_generation_enabled requires destination_recommendations_enabled"
            )
        if self.dynamic_location_resolution_enabled and not self.trip_discovery_enabled:
            raise ValueError("dynamic_location_resolution_enabled requires trip_discovery_enabled")
        if self.trip_inspiration_enabled and not self.trip_discovery_enabled:
            raise ValueError("trip_inspiration_enabled requires trip_discovery_enabled")
        if self.trip_inspiration_enabled and not self.dynamic_location_resolution_enabled:
            raise ValueError(
                "trip_inspiration_enabled requires dynamic_location_resolution_enabled"
            )

    @classmethod
    def from_environment(cls) -> FeatureSettings:
        return cls(
            trip_discovery_enabled=_environment_bool("TRIP_DISCOVERY_ENABLED"),
            flexible_search_enabled=_environment_bool("FLEXIBLE_SEARCH_ENABLED"),
            flight_ranking_enabled=_environment_bool("FLIGHT_RANKING_ENABLED"),
            travel_preferences_enabled=_environment_bool("TRAVEL_PREFERENCES_ENABLED"),
            destination_recommendations_enabled=_environment_bool(
                "DESTINATION_RECOMMENDATIONS_ENABLED"
            ),
            places_llm_ranking_enabled=_environment_bool("PLACES_LLM_RANKING_ENABLED"),
            places_llm_generation_enabled=_environment_bool("PLACES_LLM_GENERATION_ENABLED"),
            dynamic_location_resolution_enabled=_environment_bool(
                "DYNAMIC_LOCATION_RESOLUTION_ENABLED"
            ),
            trip_inspiration_enabled=_environment_bool("TRIP_INSPIRATION_ENABLED"),
            semantic_updates_enabled=_environment_bool("LLM_SEMANTIC_UPDATES_ENABLED"),
        )

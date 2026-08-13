from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from importlib.resources import files
from typing import Self

from agent_system.domain.locations import AirportLocation, GeoPoint
from agent_system.domain.orchestration import PlanningLocationCandidate
from agent_system.domain.trip_discovery import LocationKind, LocationReference


def normalize_vietnamese_alias(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold().strip())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_marks.replace("đ", "d").split())


def _compact_place(value: str) -> str:
    return value.replace(" ", "").replace("-", "")


def _bounded_edit_distance(left: str, right: str, limit: int) -> int:
    """Return edit distance, stopping early when the bounded limit is exceeded."""

    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


_FUZZY_CONVERSATION_WORDS = frozenset(
    {
        "dau",
        "cho",
        "noi",
        "nao",
        "duoc",
        "mien",
        "la",
        "cung",
        "tiep",
        "di",
    }
)


def _fuzzy_distance_limit(value: str, alias: str) -> int:
    """Allow small typos while avoiding guesses for very short place names."""

    if min(len(value), len(alias)) < 3:
        return 0
    return 1 if max(len(value), len(alias)) <= 6 else 2


@dataclass(frozen=True)
class LocationMention:
    start: int
    end: int
    reference: LocationReference
    matched_text: str
    alias: str
    edit_distance: int = 0

    @property
    def is_fuzzy(self) -> bool:
        return self.edit_distance > 0


@dataclass(frozen=True)
class _CityRecord:
    city_id: str
    country_code: str
    name_en: str
    name_vi: str
    aliases: tuple[str, ...]
    airport_codes: tuple[str, ...]


@dataclass(frozen=True)
class _CountryRecord:
    country_code: str
    name_en: str
    name_vi: str
    aliases: tuple[str, ...]
    city_ids: tuple[str, ...]


class AirportCatalog:
    def __init__(
        self,
        airports: tuple[AirportLocation, ...],
        aliases: dict[str, str],
        *,
        schema_version: int,
        cities: tuple[_CityRecord, ...] = (),
        countries: tuple[_CountryRecord, ...] = (),
        strict_location_data: bool = False,
    ) -> None:
        if schema_version < 1:
            raise ValueError("airport catalog schema version must be positive")
        self.schema_version = schema_version
        self._airports = {airport.iata_code: airport for airport in airports}
        self._aliases: dict[str, str] = {}
        for alias, iata_code in aliases.items():
            normalized = normalize_vietnamese_alias(alias)
            existing = self._aliases.get(normalized)
            if existing is not None and existing != iata_code:
                raise ValueError(f"duplicate airport alias: {alias}")
            if iata_code not in self._airports:
                raise ValueError(f"airport alias points to unknown code: {iata_code}")
            self._aliases[normalized] = iata_code
        self._cities = {city.city_id: city for city in cities}
        self._countries = {country.country_code: country for country in countries}
        self._strict_location_data = strict_location_data
        self._city_aliases = self._index_entity_aliases(
            (
                alias,
                city.city_id,
                "city",
            )
            for city in cities
            for alias in (*city.aliases, city.name_en, city.name_vi)
        )
        self._country_aliases = self._index_entity_aliases(
            (
                alias,
                country.country_code,
                "country",
            )
            for country in countries
            for alias in (*country.aliases, country.name_en, country.name_vi)
        )

    @staticmethod
    def _index_entity_aliases(
        values: tuple[tuple[str, str, str], ...] | object,
    ) -> dict[str, str]:
        indexed: dict[str, str] = {}
        for alias, identifier, kind in values:  # type: ignore[union-attr]
            normalized = normalize_vietnamese_alias(alias)
            if not normalized:
                continue
            existing = indexed.get(normalized)
            if existing is not None and existing != identifier:
                raise ValueError(f"duplicate {kind} alias: {alias}")
            indexed[normalized] = identifier
        return indexed

    @staticmethod
    def _name_pair(raw: object, field: str) -> tuple[str, str]:
        if not isinstance(raw, dict):
            raise ValueError(f"{field} must contain localized names")
        english = str(raw.get("en", "")).strip()
        vietnamese = str(raw.get("vi", english)).strip()
        if not english or not vietnamese:
            raise ValueError(f"{field} must contain non-empty localized names")
        return english, vietnamese

    @classmethod
    def from_package_data(cls) -> Self:
        """Deprecated v1 compatibility loader; discovery uses the v2 catalog explicitly."""
        return cls.from_v1_package_data()

    @classmethod
    def from_v1_package_data(cls) -> Self:
        resource = files("agent_system.providers").joinpath("data/vn_airports.v1.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("airports"), list):
            raise ValueError("airport catalog payload is malformed")
        airports: list[AirportLocation] = []
        aliases: dict[str, str] = {}
        for raw in payload["airports"]:
            airport = AirportLocation(
                iata_code=raw["iata_code"],
                city_name_en=raw["city_name_en"],
                city_name_vi=raw["city_name_vi"],
                airport_name_en=raw["airport_name_en"],
                airport_name_vi=raw["airport_name_vi"],
                timezone=raw["timezone"],
                coordinates=GeoPoint(
                    latitude=raw["latitude"],
                    longitude=raw["longitude"],
                ),
            )
            airports.append(airport)
            for alias in (
                *raw.get("aliases", []),
                airport.iata_code,
                airport.city_name_en,
                airport.city_name_vi,
                airport.airport_name_en,
                airport.airport_name_vi,
            ):
                existing = aliases.get(alias)
                if existing is not None and existing != airport.iata_code:
                    raise ValueError(f"duplicate airport alias: {alias}")
                aliases[alias] = airport.iata_code
        return cls(
            tuple(airports),
            aliases,
            schema_version=int(payload.get("schema_version", 0)),
        )

    @classmethod
    def from_v2_package_data(cls) -> Self:
        resource = files("agent_system.providers").joinpath("data/airports.v2.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("airport v2 catalog payload is malformed")
        raw_airports = payload.get("airports")
        raw_cities = payload.get("cities")
        raw_countries = payload.get("countries")
        if not all(isinstance(value, list) for value in (raw_airports, raw_cities, raw_countries)):
            raise ValueError("airport v2 catalog requires airports, cities, and countries")
        airports: list[AirportLocation] = []
        aliases: dict[str, str] = {}
        for raw in raw_airports:
            if not isinstance(raw, dict):
                raise ValueError("airport v2 entries must be objects")
            name_en, name_vi = cls._name_pair(raw.get("names"), "airport names")
            airport_name_en, airport_name_vi = cls._name_pair(
                raw.get("airport_names"),
                "airport names",
            )
            coordinates = raw.get("coordinates")
            if not isinstance(coordinates, dict):
                raise ValueError("airport coordinates must be an object")
            airport = AirportLocation(
                iata_code=raw["iata_code"],
                city_name_en=name_en,
                city_name_vi=name_vi,
                airport_name_en=airport_name_en,
                airport_name_vi=airport_name_vi,
                timezone=raw["timezone"],
                coordinates=GeoPoint(
                    latitude=coordinates["latitude"],
                    longitude=coordinates["longitude"],
                ),
            )
            airports.append(airport)
            for alias in (
                *raw.get("aliases", []),
                airport.iata_code,
                airport.airport_name_en,
                airport.airport_name_vi,
            ):
                normalized = normalize_vietnamese_alias(alias)
                existing = aliases.get(normalized)
                if existing is not None and existing != airport.iata_code:
                    raise ValueError(f"duplicate airport alias: {alias}")
                aliases[alias] = airport.iata_code

        airport_codes = {airport.iata_code for airport in airports}
        cities: list[_CityRecord] = []
        for raw in raw_cities:
            if not isinstance(raw, dict):
                raise ValueError("city v2 entries must be objects")
            name_en, name_vi = cls._name_pair(raw.get("names"), "city names")
            codes = tuple(str(code).strip().upper() for code in raw.get("airport_codes", ()))
            if not codes or len(codes) > 5 or not set(codes).issubset(airport_codes):
                raise ValueError("city airport codes must reference one to five known airports")
            cities.append(
                _CityRecord(
                    city_id=str(raw["city_id"]).strip(),
                    country_code=str(raw["country_code"]).strip().upper(),
                    name_en=name_en,
                    name_vi=name_vi,
                    aliases=tuple(str(alias) for alias in raw.get("aliases", ())),
                    airport_codes=codes,
                )
            )

        city_ids = {city.city_id for city in cities}
        countries: list[_CountryRecord] = []
        for raw in raw_countries:
            if not isinstance(raw, dict):
                raise ValueError("country v2 entries must be objects")
            name_en, name_vi = cls._name_pair(raw.get("names"), "country names")
            country_code = str(raw["country_code"]).strip().upper()
            supported = tuple(str(city_id).strip() for city_id in raw.get("supported_city_ids", ()))
            if not supported or not set(supported).issubset(city_ids):
                raise ValueError("country city IDs must reference known cities")
            countries.append(
                _CountryRecord(
                    country_code=country_code,
                    name_en=name_en,
                    name_vi=name_vi,
                    aliases=tuple(str(alias) for alias in raw.get("aliases", ())),
                    city_ids=supported,
                )
            )

        country_codes = {country.country_code for country in countries}
        if any(city.country_code not in country_codes for city in cities):
            raise ValueError("city country codes must reference known countries")
        if any(
            airport_city not in {city.city_id for city in cities}
            for airport_city in (
                str(raw.get("city_id", "")).strip() for raw in raw_airports if isinstance(raw, dict)
            )
        ):
            raise ValueError("airport city IDs must reference known cities")

        return cls(
            tuple(airports),
            aliases,
            schema_version=int(payload.get("schema_version", 0)),
            cities=tuple(cities),
            countries=tuple(countries),
            strict_location_data=True,
        )

    def get(self, iata_code: str) -> AirportLocation:
        normalized = iata_code.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("airport lookup requires a three-letter IATA code")
        try:
            return self._airports[normalized]
        except KeyError as exc:
            if self.schema_version == 1:
                raise ValueError(f"unknown Vietnam airport: {normalized}") from exc
            raise ValueError(f"unknown airport: {normalized}") from exc

    def resolve(self, value: str) -> AirportLocation:
        normalized = normalize_vietnamese_alias(value)
        try:
            return self._airports[self._aliases[normalized]]
        except KeyError as exc:
            raise ValueError(f"unknown airport or alias: {value}") from exc

    def _v2(self) -> AirportCatalog:
        if self._strict_location_data:
            return self
        return type(self).from_v2_package_data()

    def _airport_reference(
        self, code: str, *, normalized_name: str | None = None
    ) -> LocationReference:
        airport = self._airports[code]
        city = self._cities.get(
            next(
                (city_id for city_id, item in self._cities.items() if code in item.airport_codes),
                "",
            )
        )
        return LocationReference(
            kind=LocationKind.AIRPORT,
            normalized_name=normalized_name or airport.iata_code,
            airport_candidates=(airport.iata_code,),
            country_code=city.country_code if city else None,
            city_id=city.city_id if city else None,
        )

    def resolve_location(self, value: str) -> LocationReference:
        catalog = self._v2()
        normalized = normalize_vietnamese_alias(value)
        if not normalized:
            return LocationReference(kind=LocationKind.UNKNOWN, normalized_name="unknown")
        if (
            len(normalized) == 3
            and normalized.isalpha()
            and normalized.upper() in catalog._airports
        ):
            return catalog._airport_reference(
                normalized.upper(), normalized_name=normalized.upper()
            )
        if normalized in catalog._aliases:
            code = catalog._aliases[normalized]
            return catalog._airport_reference(code, normalized_name=value.strip())
        if normalized in catalog._city_aliases:
            city = catalog._cities[catalog._city_aliases[normalized]]
            return LocationReference(
                kind=LocationKind.CITY,
                normalized_name=city.name_en,
                airport_candidates=city.airport_codes,
                country_code=city.country_code,
                city_id=city.city_id,
            )
        if normalized in catalog._country_aliases:
            country = catalog._countries[catalog._country_aliases[normalized]]
            return LocationReference(
                kind=LocationKind.COUNTRY,
                normalized_name=country.name_en,
                country_code=country.country_code,
            )
        fuzzy_mentions = catalog.find_mentions(value)
        if (
            len(fuzzy_mentions) == 1
            and fuzzy_mentions[0].start == 0
            and fuzzy_mentions[0].end == len(normalized)
        ):
            return fuzzy_mentions[0].reference
        return LocationReference(
            kind=LocationKind.UNKNOWN,
            normalized_name=" ".join(value.strip().split())[:160] or "unknown",
        )

    def find_mentions(self, value: str) -> tuple[LocationMention, ...]:
        catalog = self._v2()
        normalized = normalize_vietnamese_alias(value)
        candidates: list[tuple[int, int, int, int, LocationMention]] = []
        aliases = (
            (0, catalog._aliases),
            (1, catalog._city_aliases),
            (2, catalog._country_aliases),
        )
        for priority, mapping in aliases:
            for alias in mapping:
                if not alias:
                    continue
                pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"
                for match in re.finditer(pattern, normalized):
                    raw_fragment = value[match.start() : match.end()]
                    is_iata_alias = (
                        priority == 0 and len(alias) == 3 and alias.upper() in catalog._airports
                    )
                    if (
                        is_iata_alias
                        and normalized.strip() != alias
                        and not (raw_fragment.isascii() and raw_fragment.isupper())
                    ):
                        continue
                    candidates.append(
                        (
                            match.start(),
                            0,
                            -(match.end() - match.start()),
                            priority,
                            LocationMention(
                                start=match.start(),
                                end=match.end(),
                                reference=catalog.resolve_location(alias),
                                matched_text=raw_fragment,
                                alias=alias,
                            ),
                        )
                    )
        selected: list[LocationMention] = []
        exact_aliases = {alias for _, mapping in aliases for alias in mapping}
        max_alias_words = max(
            (alias.count(" ") + 1 for alias in exact_aliases),
            default=1,
        )
        tokens = list(re.finditer(r"\w+", normalized))
        for start_index, start_token in enumerate(tokens):
            for end_index in range(
                start_index,
                min(len(tokens), start_index + max_alias_words),
            ):
                end_token = tokens[end_index]
                candidate_text = normalized[start_token.start() : end_token.end()]
                if candidate_text in exact_aliases:
                    continue
                candidate_words = set(candidate_text.split())
                if candidate_words and candidate_words.issubset(_FUZZY_CONVERSATION_WORDS):
                    continue
                matches: list[tuple[int, int, str, str]] = []
                if any(
                    re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", candidate_text)
                    for alias in exact_aliases
                ):
                    continue
                for priority, mapping in aliases:
                    for alias, identifier in mapping.items():
                        compact_candidate = _compact_place(candidate_text)
                        compact_alias = _compact_place(alias)
                        limit = _fuzzy_distance_limit(compact_candidate, compact_alias)
                        distance = min(
                            _bounded_edit_distance(candidate_text, alias, limit),
                            _bounded_edit_distance(compact_candidate, compact_alias, limit),
                        )
                        if distance <= limit:
                            matches.append((distance, priority, alias, identifier))
                if not matches:
                    continue
                best_distance = min(item[0] for item in matches)
                best_matches = [item for item in matches if item[0] == best_distance]
                identifiers = {item[3] for item in best_matches}
                if len(identifiers) != 1:
                    continue
                best_match_distance, priority, alias, _ = min(
                    best_matches,
                    key=lambda item: (item[1], -len(item[2])),
                )
                candidates.append(
                    (
                        start_token.start(),
                        1,
                        -(end_token.end() - start_token.start()),
                        priority,
                        LocationMention(
                            start=start_token.start(),
                            end=end_token.end(),
                            reference=catalog.resolve_location(alias),
                            matched_text=candidate_text,
                            alias=alias,
                            edit_distance=best_match_distance,
                        ),
                    )
                )
        occupied: list[tuple[int, int]] = []
        for start, _, negative_length, _, mention in sorted(
            candidates, key=lambda item: (item[0], item[1], item[2], item[3])
        ):
            end = start - negative_length
            if any(
                start < occupied_end and end > occupied_start
                for occupied_start, occupied_end in occupied
            ):
                continue
            selected.append(mention)
            occupied.append((start, end))
        return tuple(sorted(selected, key=lambda mention: mention.start))

    def supported_city_references(self, country_code: str) -> tuple[LocationReference, ...]:
        catalog = self._v2()
        country = catalog._countries.get(country_code.strip().upper())
        if country is None:
            return ()
        return tuple(
            LocationReference(
                kind=LocationKind.CITY,
                normalized_name=catalog._cities[city_id].name_en,
                airport_candidates=catalog._cities[city_id].airport_codes,
                country_code=catalog._cities[city_id].country_code,
                city_id=city_id,
            )
            for city_id in country.city_ids
        )

    def airport_references(self, *, limit: int = 10) -> tuple[LocationReference, ...]:
        catalog = self._v2()
        return tuple(
            catalog._airport_reference(code, normalized_name=code)
            for code in list(catalog._airports)[:limit]
        )

    def planning_candidates(self, *, limit: int = 100) -> tuple[PlanningLocationCandidate, ...]:
        if limit < 1:
            return ()
        catalog = self._v2()
        candidates: list[PlanningLocationCandidate] = []
        for code, airport in catalog._airports.items():
            candidates.append(
                PlanningLocationCandidate(
                    candidate_id=f"airport:{code}",
                    kind="airport",
                    canonical_name=airport.airport_name_en,
                    airport_codes=(code,),
                )
            )
        for city in catalog._cities.values():
            candidates.append(
                PlanningLocationCandidate(
                    candidate_id=f"city:{city.city_id}",
                    kind="city",
                    canonical_name=city.name_en,
                    airport_codes=city.airport_codes,
                )
            )
        for country in catalog._countries.values():
            airport_codes = tuple(
                code
                for city_id in country.city_ids
                for code in catalog._cities[city_id].airport_codes
            )
            candidates.append(
                PlanningLocationCandidate(
                    candidate_id=f"country:{country.country_code}",
                    kind="country",
                    canonical_name=country.name_en,
                    airport_codes=airport_codes,
                )
            )
        return tuple(candidates[:limit])

    def resolve_planning_candidate(self, candidate_id: str) -> LocationReference | None:
        normalized = candidate_id.strip()
        catalog = self._v2()
        candidate = next(
            (item for item in catalog.planning_candidates() if item.candidate_id == normalized),
            None,
        )
        if candidate is None:
            return None
        if candidate.kind == "airport":
            return catalog._airport_reference(
                candidate.airport_codes[0], normalized_name=candidate.canonical_name
            )
        if candidate.kind == "city":
            city_id = normalized.removeprefix("city:")
            city = catalog._cities.get(city_id)
            if city is None:
                return None
            return LocationReference(
                kind=LocationKind.CITY,
                normalized_name=city.name_en,
                airport_candidates=city.airport_codes,
                country_code=city.country_code,
                city_id=city.city_id,
            )
        country_code = normalized.removeprefix("country:").upper()
        country = catalog._countries.get(country_code)
        if country is None:
            return None
        return LocationReference(
            kind=LocationKind.COUNTRY,
            normalized_name=country.name_en,
            country_code=country.country_code,
        )

    def planning_candidate_id(self, reference: LocationReference) -> str | None:
        catalog = self._v2()
        if reference.kind is LocationKind.AIRPORT and len(reference.airport_candidates) == 1:
            candidate_id = f"airport:{reference.airport_candidates[0]}"
        elif reference.kind is LocationKind.CITY and reference.city_id:
            candidate_id = f"city:{reference.city_id}"
        elif reference.kind is LocationKind.COUNTRY and reference.country_code:
            candidate_id = f"country:{reference.country_code}"
        else:
            return None
        resolved = catalog.resolve_planning_candidate(candidate_id)
        if resolved is None or resolved.kind is not reference.kind:
            return None
        if resolved.airport_candidates != reference.airport_candidates:
            return None
        if resolved.country_code != reference.country_code or resolved.city_id != reference.city_id:
            return None
        return candidate_id

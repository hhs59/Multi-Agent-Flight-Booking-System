#!/usr/bin/env python3
"""Run the normalized provider contract over the fixed Vietnam route matrix."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr

from agent_system.domain.flights import FlightSearchCriteria, RepriceStatus
from agent_system.domain.values import ExecutionMode
from agent_system.providers.clock import FixedClock
from agent_system.providers.contracts import FlightProvider
from agent_system.providers.errors import ProviderError
from agent_system.providers.mock.flights import MockFlightProvider
from agent_system.providers.registry import ProviderRegistry, build_provider_registry
from agent_system.providers.settings import ProviderSettings

ROUTES = ("HAN-SGN", "HAN-DAD", "SGN-DAD", "SGN-PQC", "HAN-CXR")
EXPECTED_CARRIERS = ("VN", "VJ", "QH", "VU")
HORIZONS = (30, 90)
DAY_KINDS = ("weekday", "weekend")


@dataclass(frozen=True)
class ProbeScenario:
    route: str
    departure_date: date
    horizon_days: int
    day_kind: str


def _next_matching_day(start: date, day_kind: str) -> date:
    target = {"weekday": 1, "weekend": 5}[day_kind]
    return start + timedelta(days=(target - start.weekday()) % 7)


def build_scenarios(as_of: date) -> tuple[ProbeScenario, ...]:
    return tuple(
        ProbeScenario(
            route=route,
            departure_date=_next_matching_day(
                as_of + timedelta(days=horizon),
                day_kind,
            ),
            horizon_days=horizon,
            day_kind=day_kind,
        )
        for route in ROUTES
        for horizon in HORIZONS
        for day_kind in DAY_KINDS
    )


class AdapterProbe:
    def __init__(self, provider: FlightProvider) -> None:
        self.provider = provider

    async def run(self, scenario: ProbeScenario) -> dict:
        origin, destination = scenario.route.split("-")
        criteria = FlightSearchCriteria(
            origin=origin,
            destination=destination,
            departure_date=scenario.departure_date,
            currency="VND",
        )
        started = time.perf_counter()
        errors: list[str] = []
        offers = ()
        reprice = {
            "status": "not_run",
            "price_changed": None,
            "offer_expiry_minutes": None,
        }
        try:
            page = await self.provider.search(
                criteria,
                correlation_id=f"probe-{scenario.route}-{scenario.departure_date}",
            )
            offers = page.offers
            if offers and self.provider.capabilities().can_reprice:
                first = offers[0]
                repriced = await self.provider.reprice(
                    first.metadata.provider_offer_id,
                    first,
                    correlation_id=f"probe-reprice-{scenario.route}",
                )
                reprice = {
                    "status": (
                        "consistent"
                        if repriced.status is RepriceStatus.UNCHANGED
                        else repriced.status.value
                    ),
                    "price_changed": repriced.status is RepriceStatus.CHANGED,
                    "offer_expiry_minutes": (
                        int(
                            (
                                first.metadata.expires_at - first.metadata.retrieved_at
                            ).total_seconds()
                            // 60
                        )
                        if first.metadata.expires_at is not None
                        else None
                    ),
                }
        except ProviderError as exc:
            errors.append(type(exc).__name__)
        elapsed_ms = Decimal(str((time.perf_counter() - started) * 1000)).quantize(Decimal("0.001"))
        fares = []
        for offer in offers:
            taxes = sum(
                (price.taxes_and_fees.amount for price in offer.passenger_pricing),
                Decimal("0"),
            )
            baggage = (
                f"{offer.baggage.checked_weight_kg} kg"
                if offer.baggage.checked_weight_kg is not None
                else (
                    f"{offer.baggage.checked_pieces} piece(s)"
                    if offer.baggage.checked_pieces is not None
                    else "Not returned"
                )
            )
            fares.append(
                {
                    "carrier": offer.validating_carrier,
                    "flight_number": offer.segments[0].flight_number,
                    "total_price": str(offer.total.amount),
                    "currency": offer.total.currency,
                    "taxes_and_fees": str(taxes),
                    "checked_baggage": baggage,
                    "fare_conditions": (offer.fare_conditions.description or "Not returned"),
                }
            )
        returned_carriers = sorted({fare["carrier"] for fare in fares})
        return {
            "route": scenario.route,
            "departure_date": scenario.departure_date.isoformat(),
            "horizon_days": scenario.horizon_days,
            "day_kind": scenario.day_kind,
            "flight_count": len(fares),
            "returned_carriers": returned_carriers,
            "expected_carriers_present": [
                code for code in EXPECTED_CARRIERS if code in returned_carriers
            ],
            "expected_carriers_missing": [
                code for code in EXPECTED_CARRIERS if code not in returned_carriers
            ],
            "fares": fares,
            "latency_ms": str(elapsed_ms),
            "latency_kind": "local adapter wall-clock measurement",
            "errors": errors,
            "reprice": reprice,
        }


async def build_report(provider: FlightProvider, as_of: date) -> dict:
    probe = AdapterProbe(provider)
    results = [await probe.run(scenario) for scenario in build_scenarios(as_of)]
    declared = provider.capabilities()
    capabilities = {
        "search": "implemented" if declared.can_search else "not implemented",
        "reprice": "implemented" if declared.can_reprice else "not implemented",
        "booking": "implemented" if declared.can_book else "not implemented",
        "cancellation": "implemented" if declared.can_cancel else "not implemented",
        "refund": "implemented" if declared.can_refund else "not implemented",
        "hold": "implemented" if declared.can_hold else "not enabled in this probe",
        "ancillaries": ("implemented" if declared.supports_ancillaries else "not implemented"),
    }
    mode = provider.environment
    return {
        "schema_version": 2,
        "provider": provider.name,
        "environment": mode.value,
        "is_live": mode is ExecutionMode.PRODUCTION,
        "as_of": as_of.isoformat(),
        "routes": list(ROUTES),
        "horizons_days": list(HORIZONS),
        "day_kinds": list(DAY_KINDS),
        "point_of_sale": "VN",
        "settlement_currency": "VND",
        "capabilities": capabilities,
        "commercial_requirements": {
            "accreditation": "not applicable" if mode is ExecutionMode.MOCK else "Not measured yet",
            "consolidator": "not applicable" if mode is ExecutionMode.MOCK else "Not measured yet",
            "kyc": "not applicable" if mode is ExecutionMode.MOCK else "Not measured yet",
            "contract": "not applicable" if mode is ExecutionMode.MOCK else "Not measured yet",
        },
        "results": results,
    }


def render_markdown(report: dict) -> str:
    results = report["results"]
    errors = sum(len(result["errors"]) for result in results)
    measured_provider = report["provider"]
    is_mock = measured_provider == "mock"
    mock_evidence = (
        "Complete deterministic adapter matrix below" if is_mock else "Not measured in this report"
    )
    duffel_evidence = (
        "Adapter implemented; not measured in this report"
        if measured_provider != "duffel"
        else "Sandbox adapter matrix measured below"
    )
    summary_description = (
        "The matrix below executes the actual normalized MockFlightProvider. Its inventory is "
        "synthetic and is not evidence of airline inventory or production availability."
        if is_mock
        else f"The matrix below executes the {measured_provider.title()} sandbox adapter. "
        "Sandbox results are technical evidence only and do not approve production ticketing "
        "or servicing."
    )
    mock_search_evidence = "Measured offline" if is_mock else "Not measured in this report"
    duffel_search_evidence = "Measured in sandbox" if measured_provider == "duffel" else "Not measured yet"
    lines = [
        "# Vietnam Provider Coverage",
        "",
        f"- **Report schema:** {report['schema_version']}",
        f"- **Probe as of:** {report['as_of']}",
        "- **Production provider decision:** **NOT APPROVED**",
        "- **Reason:** no live candidate has complete measured coverage plus accepted ticketing, settlement, and servicing readiness.",
        "",
        "Consumer airline sites are manual recall references only. They are not scraped or treated as bookable API inventory.",
        "",
        "## Candidate Status",
        "",
        "| Candidate | Technical evidence | Commercial evidence | Production status |",
        "|---|---|---|---|",
        f"| Mock | {mock_evidence} | Not applicable | Rejected for live use |",
        f"| Duffel | {duffel_evidence} | Not measured yet | Not approved |",
        "| Travelport/local consolidator | Not measured yet | Discovery not started | Not approved |",
        "",
        f"## {measured_provider.title()} Provider Summary",
        "",
        summary_description,
        "",
        f"- Point of sale: `{report['point_of_sale']}`",
        f"- Settlement currency: `{report['settlement_currency']}`",
        f"- Scenarios completed: `{len(results) - errors}` of `{len(results)}`",
        f"- Errors: `{errors}`",
        f"- Reprice: actual {measured_provider} adapter pricing path",
        "",
        "### Capabilities",
        "",
        "| Operation | Declared capability |",
        "|---|---|",
    ]
    lines.extend(
        f"| {operation} | {value} |" for operation, value in report["capabilities"].items()
    )
    lines.extend(
        [
            "",
            "### Route/Date Matrix",
            "",
            "| Route | Date | Horizon | Day | Flights | Carriers | Missing VN/VJ/QH/VU | Latency |",
            "|---|---:|---:|---|---:|---|---|---:|",
        ]
    )
    for result in results:
        missing = ", ".join(result["expected_carriers_missing"]) or "None"
        lines.append(
            f"| {result['route']} | {result['departure_date']} | "
            f"{result['horizon_days']} days | {result['day_kind']} | "
            f"{result['flight_count']} | {', '.join(result['returned_carriers'])} | "
            f"{missing} | {result['latency_ms']} ms (local) |"
        )
    lines.extend(
        [
            "",
            "### Representative Fares",
            "",
            "The first 30-day weekday scenario is shown for each route. Amounts preserve the provider-returned VND currency.",
            "",
            "| Route | Carrier/flight | Total | Taxes/fees | Baggage | Fare conditions |",
            "|---|---|---:|---:|---|---|",
        ]
    )
    representative = {
        result["route"]: result
        for result in reversed(results)
        if result["horizon_days"] == 30 and result["day_kind"] == "weekday"
    }
    for route in ROUTES:
        for fare in representative[route]["fares"]:
            lines.append(
                f"| {route} | {fare['carrier']} {fare['flight_number']} | "
                f"{fare['total_price']} {fare['currency']} | "
                f"{fare['taxes_and_fees']} {fare['currency']} | "
                f"{fare['checked_baggage']} | {fare['fare_conditions']} |"
            )
    lines.extend(
        [
            "",
            "## Adapter and Commercial Limits",
            "",
            "| Requirement | Mock | Duffel | Travelport/local |",
            "|---|---|---|---|",
            f"| Search/reprice | {mock_search_evidence} | {duffel_search_evidence} | Not measured yet |",
            "| Booking/cancellation/refund | Not implemented | Not implemented in Phase 3 | Not measured yet |",
            "| Hold and ancillaries | Scenario-tested hold; no ancillaries | Not enabled in Phase 3 | Not measured yet |",
            "| Point of sale/currency | Synthetic VN/VND | Not measured yet | Not measured yet |",
            "| Accreditation/consolidator/KYC | Not applicable | Not measured yet | Not measured yet |",
            "| Contract and settlement | Not applicable | Not measured yet | Not measured yet |",
            "",
            "Provider repricing retrieves current offer state from the selected adapter. Production booking requires a reviewed encrypted or access-controlled durable design.",
            "",
            "## Reproduce",
            "",
            "```bash",
            f"python scripts/provider_probe.py --provider {measured_provider} --as-of {report['as_of']} --format markdown --output docs/provider-coverage-vn.md",
            "```",
            "",
            "Credentialed sandbox evidence uses `--provider duffel` with `DUFFEL_ACCESS_TOKEN` and remains excluded from default CI. A provider remains unapproved until the same matrix and commercial requirements are reviewed.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "duffel"), default="mock")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _provider_from_args(name: str, as_of: date) -> tuple[FlightProvider, ProviderRegistry | None]:
    clock = FixedClock(datetime.combine(as_of, datetime.min.time(), tzinfo=UTC))
    if name == "mock":
        return MockFlightProvider(clock=clock), None
    if name != "duffel":
        raise ValueError(f"unsupported probe provider: {name}")
    access_token = os.environ.get("DUFFEL_ACCESS_TOKEN")
    if not access_token:
        raise ValueError("DUFFEL_ACCESS_TOKEN is required for the Duffel probe")
    settings = ProviderSettings(
        execution_mode=ExecutionMode.SANDBOX,
        flight_provider="duffel",
        weather_provider="unavailable",
        payment_provider="unavailable",
        notification_provider="unavailable",
        duffel_access_token=SecretStr(access_token),
    )
    registry = build_provider_registry(settings, clock=clock)
    return registry.flight, registry


async def _run(args: argparse.Namespace) -> str:
    provider, registry = _provider_from_args(args.provider, args.as_of)
    try:
        report = await build_report(provider, args.as_of)
    finally:
        if registry is not None:
            await registry.aclose()
    return (
        json.dumps(report, indent=2, ensure_ascii=True) + "\n"
        if args.format == "json"
        else render_markdown(report)
    )


def main() -> int:
    args = parse_args()
    rendered = asyncio.run(_run(args))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import pytest
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from agent_system.domain.flights import (
    CabinClass,
    FlightOffer,
    FlightSearchCriteria,
    PassengerMix,
)
from agent_system.domain.values import ExecutionMode, Money, ProviderMetadata


def test_passenger_mix_defaults_and_validation():
    mix = PassengerMix()
    assert mix.adults == 1
    assert mix.children == 0
    assert mix.infants == 0
    assert mix.total == 1

    custom_mix = PassengerMix(adults=2, children=1, infants=1)
    assert custom_mix.total == 4


def test_money_formatting_and_math():
    vnd = Money(amount=Decimal("2500000"), currency="VND")
    assert vnd.amount == Decimal("2500000")
    assert vnd.currency == "VND"

    eur = Money(amount=Decimal("103.50"), currency="EUR")
    assert eur.amount == Decimal("103.50")
    assert eur.currency == "EUR"


def test_flight_search_criteria_creation():
    today = date.today()
    criteria = FlightSearchCriteria(
        origin="HAN",
        destination="SIN",
        departure_date=today + timedelta(days=14),
        cabin=CabinClass.ECONOMY,
        passengers=PassengerMix(adults=1),
    )
    assert criteria.origin == "HAN"
    assert criteria.destination == "SIN"
    assert criteria.cabin == CabinClass.ECONOMY
    assert criteria.return_date is None

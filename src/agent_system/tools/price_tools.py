import logging
import os
import random
import sqlite3
from datetime import date, timedelta

import numpy as np
from sklearn import linear_model

from agent_system.models import PriceHistory, PricePrediction

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("PRICE_DB_PATH", "data/price_history.db")

_conn: sqlite3.Connection | None = None


def _db() -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    global _conn
    if _conn is None:
        parent = os.path.dirname(DB_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH)
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                origin        TEXT NOT NULL,
                destination   TEXT NOT NULL,
                date          TEXT NOT NULL,
                price_usd     REAL NOT NULL,
                source        TEXT NOT NULL DEFAULT 'amadeus',
                recorded_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(origin, destination, date)
            )
            """
        )
        _conn.commit()
        logger.info("Initialized price history DB at %s", DB_PATH)
    return _conn, _conn.cursor()


# Price storage
async def store_prices_bulk(
    entries: list[tuple[str, str, str, float, str]],
) -> None:
    if not entries:
        return
    conn, cursor = _db()
    cursor.executemany(
        "INSERT OR REPLACE INTO price_history "
        "(origin, destination, date, price_usd, source) VALUES (?, ?, ?, ?, ?)",
        entries,
    )
    conn.commit()
    logger.info("Stored %d price observations", len(entries))


# Mock price-history generation
_MOCK_BASE_PRICES: dict[str, float] = {
    "HAN-NRT": 400.0,
    "SGN-NRT": 400.0,
    "SGN-SIN": 130.0,
    "HAN-ICN": 380.0,
    "SGN-BKK": 95.0,
    "HAN-DAD": 60.0,
}


def _seed_mock_prices(
    origin: str,
    destination: str,
    days: int = 30,
    source: str = "mock"
) -> list[PriceHistory]:
    route = f"{origin}-{destination}"
    base = _MOCK_BASE_PRICES.get(route, 300.0)
    rng = random.Random(f"{origin}-{destination}-{days}")
    trend_slope = rng.uniform(-2.0, 2.0)

    today = date.today()
    history: list[PriceHistory] = []

    for i in range(days):
        d = today - timedelta(days=i)
        trend = trend_slope * (days - 1 - i)
        noise = base * rng.uniform(-0.10, 0.10)
        price = base + trend + noise
        if d.weekday() in (1, 2):
            price *= 0.95
        price = round(max(price, 10.0), 2)
        date_str = d.isoformat()
        history.append(PriceHistory(date=date_str, price_usd=price, source=source))

    logger.info(
        "Generated %d mock price points for %s (base=$%.0f, slope=$%.2f/day)",
        days,
        route,
        base,
        trend_slope,
    )
    return history


# Public tool: get price history
def get_price_history(
    origin: str,
    destination: str,
    days: int = 30,
    mock: bool = False,
) -> list[PriceHistory]:
    if mock:
        return _seed_mock_prices(origin, destination, days)

    conn, cursor = _db()
    cursor.execute(
        "SELECT date, price_usd, source FROM price_history "
        "WHERE origin=? AND destination=? ORDER BY date DESC LIMIT ?",
        (origin, destination, days),
    )
    rows = cursor.fetchall()

    if not rows:
        logger.info(
            "No price history for %s->%s — seeding mock data",
            origin,
            destination,
        )
        return _seed_mock_prices(origin, destination, days, source="synthetic")

    return [
        PriceHistory(date=row[0], price_usd=float(row[1]), source=row[2])
        for row in rows
    ]


def analyze_price_trend(
    history: list[PriceHistory],
    current_price: float,
    departure_date: date | None = None,
) -> PricePrediction:
    prices = [h.price_usd for h in history]
    avg_price = sum(prices) / len(prices)
    sorted_prices = sorted(prices)
    percentile = sum(1 for p in sorted_prices if p <= current_price) / len(prices) * 100
    percentile = int(round(percentile))

    recent = prices[:7]
    if len(recent) < 2:
        trend = "stable"
        slope = 0.0
    else:
        x = np.arange(len(recent)).reshape(-1, 1)
        reg = linear_model.LinearRegression().fit(x, recent)
        slope = reg.coef_[0]
        if slope > 0.5:
            trend = "rising"
        elif slope < -0.5:
            trend = "falling"
        else:
            trend = "stable"

    if percentile < 25:
        prediction = "buy_now"
        confidence = 0.85
    elif percentile <= 50:
        if trend == "falling":
            prediction = "neutral"
            confidence = 0.5
        elif trend == "rising":
            prediction = "buy_now"
            confidence = 0.6
        else:
            prediction = "neutral"
            confidence = 0.5
    elif percentile <= 75:
        if trend == "falling":
            prediction = "wait"
            confidence = 0.6
        else:
            prediction = "neutral"
            confidence = 0.5
    else:
        prediction = "wait"
        confidence = 0.75

    if len(recent) < 2:
        prediction = "neutral"
        confidence = 0.3

    reasoning = (
        f"Current price ${current_price:.0f} is at the {percentile}th percentile "
        f"of {len(prices)} historical points (avg ${avg_price:.0f}). "
        f"7-day trend: {trend} (slope ${slope:+.2f}/day). "
        f"Recommendation: {prediction.replace('_', ' ')} (confidence {confidence:.0%})."
    )

    return PricePrediction(
        current_price=current_price,
        average_price=round(avg_price, 2),
        price_percentile=percentile,
        trend=trend,
        prediction=prediction,
        confidence=confidence,
        reasoning=reasoning,
        historical_data=history,
    )

import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from typing_extensions import Literal


def _validate_iata(v: str) -> str:
    if len(v) != 3 or not v.isalpha():
        raise ValueError(f"'{v}' is not a valid 3-letter IATA code")
    return v.upper()


class FlightSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str
    destination: str
    departure_date: datetime.date
    return_date: Optional[datetime.date] = None
    flexible_dates: bool = False
    passengers: int = Field(default=1)
    budget_usd: Optional[float] = Field(default=None, gt=0)
    preferred_time: Literal["morning", "afternoon", "evening", "any"] = "any"
    max_stops: int = Field(default=2, ge=0)
    priority: Literal["price", "comfort", "speed", "balanced"] = "balanced"

    @field_validator("origin", "destination")
    @classmethod
    def iata_code(cls, v: str) -> str:
        return _validate_iata(v)


class BookingDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_number: str
    passenger_name: str
    passenger_email: EmailStr
    passport_number: str
    phone: Optional[str] = None


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["search", "advise", "book", "unclear"]
    flight_query: Optional[FlightSearchQuery] = None
    booking_details: Optional[BookingDetails] = None
    reasoning: str
    needs_clarification: bool
    clarification_question: Optional[str] = None
    language: Literal["en", "vi"]


class FlightResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_number: str
    airline: str
    airline_name: str
    departure: datetime.datetime
    arrival: datetime.datetime
    duration_minutes: int
    stops: int
    price_usd: float
    seats_available: Optional[int] = None
    weather_at_dest: str = "Weather data unavailable"


class FlightSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[FlightResult]
    total_found: int
    search_params: dict[str, Any]


class PriceHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: datetime.date
    price_usd: float
    source: str


class PricePrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_price: float
    average_price: float
    price_percentile: int = Field(ge=0, le=100)
    trend: Literal["rising", "falling", "stable"]
    prediction: Literal["buy_now", "wait", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    historical_data: list[PriceHistory]


class FlightPriceAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight: FlightResult
    price_analysis: PricePrediction


class PriceIntelligenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flights: list[FlightPriceAnalysis]
    best_deal: Optional[FlightResult] = None
    summary: str


class AirlineReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    overall_rating: float = Field(ge=0.0, le=5.0)
    categories: dict[str, float]
    sentiment_summary: str
    sample_positive: list[str]
    sample_negative: list[str]
    total_reviews_analyzed: int


class ReviewAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviews: dict[str, AirlineReview]
    comparison: str
    recommendation: str


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_number: str
    passenger_name: str
    passenger_email: EmailStr
    passport_number: str
    phone: Optional[str] = None


class BookingConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_code: str
    flight_number: str
    route: str
    departure: datetime.datetime
    arrival: datetime.datetime
    passenger_name: str
    passenger_email: EmailStr
    price_usd: float
    status: Literal["confirmed", "pending", "failed"]
    ticket_email_sent: bool
    booking_timestamp: datetime.datetime


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    label: str
    airline: str
    text: str
    sentiment: Optional[Literal["positive", "neutral", "negative"]] = None


class TripAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    best_flight: Optional[FlightResult] = None
    price_advice: str
    weather_advice: str
    airline_advice: str
    total_cost_estimate: Optional[float] = None
    action_items: list[str]
    disclaimer: str
    citations: list[Citation] = Field(default_factory=list)


class GenerationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    faithfulness: float
    hallucination_rate: float
    judge_scores: dict[str, float]


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: str
    plan: TaskPlan
    trip_advice: Optional[TripAdvice] = None
    flight_results: Optional[FlightSearchOutput] = None
    price_intelligence: Optional[PriceIntelligenceOutput] = None
    review_analysis: Optional[ReviewAnalysisOutput] = None
    booking_confirmation: Optional[BookingConfirmation] = None
    retrieval_context: Optional[list[Citation]] = None
    tokens_used: int = 0
    errors: list[str] = Field(default_factory=list)
    language: Literal["en", "vi"]

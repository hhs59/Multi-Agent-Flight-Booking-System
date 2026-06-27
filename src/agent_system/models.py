from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from typing_extensions import Literal

class FlightSearchQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')

    origin : str
    destination : str
    passengers : int = Field(default=1)
    preferred_time : Literal['morning', 'afternoon', 'evening', 'any'] = 'any'
    priority : Literal['price', 'comfort', 'speed', 'balanced'] = 'balanced'
    departure_date : str
    return_date : Optional[str] = None
    flexible_dates : bool = Field(default=False)
    budget_usd : Optional[float] = None
    max_stops : int = Field(default=2)


class BookingDetails(BaseModel):
    model_config = ConfigDict(extra='forbid')

    flight_number : str
    passenger_name : str
    passenger_email : str
    passport_number : str
    phone : Optional[str] = None

class FlightResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    flight_number : str
    airline : str
    airline_name : str
    departure : str
    arrival : str
    duration_minutes : int
    stops : int
    price_usd : float
    seats_available : Optional[int] = None
    weather_at_dest : str

class PriceHistory(BaseModel):
    model_config = ConfigDict(extra='forbid')

    date : str
    price_usd : float
    source : str

class BookingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    flight_number : str
    passenger_name : str
    passenger_email : str
    passport_number : str
    phone : Optional[str] = None

class BookingConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')

    confirmation_code : str
    flight_number : str
    route : str
    departure : str
    arrival : str
    passenger_name : str
    price_usd : float
    status : Literal['confirmed', 'pending', 'failed']
    ticket_email_sent : bool
    booking_timestamp : str

class AirlineReview(BaseModel):
    model_config =  ConfigDict(extra='forbid')

    source : str
    overall_rating : float
    categories : dict[str, float]
    sentiment_summary : str
    sample_positive : list[str]
    sample_negative : list[str]
    total_reviews_analyzed : int

class TaskPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')

    flight_query : Optional[FlightSearchQuery] = None
    booking_details : Optional[BookingDetails] = None
    intent : Literal['search', 'advise', 'book', 'unclear']
    reasoning : str
    needs_clarification : bool
    clarification_question : Optional[str] = None
    language : Literal['en', 'vi']

class FlightSearchOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    results : list[FlightResult]
    total_found : int
    search_params : dict

class PricePrediction(BaseModel):
    model_config = ConfigDict(extra='forbid')

    average_price : float
    historical_data : list[PriceHistory]
    current_price : float
    price_percentile : int
    trend : Literal['rising', 'falling', 'stable']
    prediction : Literal['buy_now', 'wait', 'neutral']
    confidence : float
    reasoning : str

class PriceIntelligenceOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    
    flights : list[dict]
    best_deal : FlightResult
    summary : str

class ReviewAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    reviews : dict[str, AirlineReview]
    comparison : str
    recommendation : str

class TripAdvice(BaseModel):
    model_config = ConfigDict(extra='forbid')

    best_flight : FlightResult
    summary : str
    price_advice : str
    weather_advice : str
    airline_advice : str
    total_cost_estimate : float
    action_items : list[str]
    disclaimer : str

class AgentResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    plan : TaskPlan
    trip_advice : Optional[TripAdvice]
    flight_results : Optional[FlightSearchOutput]
    price_intelligence : Optional[PriceIntelligenceOutput]
    review_analysis : Optional[ReviewAnalysisOutput]
    booking_confirmation : Optional[BookingConfirmation]
    response : str
    tokens_used : int
    errors : list[str]
    language : Literal['en', 'vi']

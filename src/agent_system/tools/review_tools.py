import logging

from agent_system.models import AirlineReview

logger = logging.getLogger(__name__)

#Generate data by LLM
_POSITIVE_WORDS = frozenset({
    "excellent", "great", "good", "comfortable", "friendly", "helpful",
    "clean", "spacious", "delicious", "punctual", "on time", "smooth",
    "professional", "amazing", "wonderful", "fantastic", "impressed",
    "recommend", "love", "best", "enjoyable", "pleasant", "efficient",
})

_NEGATIVE_WORDS = frozenset({
    "terrible", "bad", "poor", "uncomfortable", "rude", "delayed", "late",
    "dirty", "cramped", "expensive", "overpriced", "worst", "awful",
    "disappointing", "slow", "crowded", "broken", "cold", "stale",
    "never", "hate", "avoid", "unprofessional", "lost", "cancelled",
})

_CATEGORY_KEYWORDS: dict[str, frozenset[str]] = {
    "seat": frozenset({"seat", "legroom", "recline", "comfort", "spacious", "cramped", "width"}),
    "service": frozenset({"crew", "staff", "service", "attendant", "friendly", "rude", "helpful", "professional"}),
    "food": frozenset({"food", "meal", "drink", "snack", "catering", "menu", "breakfast", "lunch", "dinner", "delicious", "stale"}),
    "punctuality": frozenset({"delay", "late", "on time", "punctual", "cancelled", "schedule", "boarding", "departure", "arrival"}),
    "value": frozenset({"price", "value", "expensive", "cheap", "overpriced", "affordable", "cost", "worth", "ticket"}),
}

_MOCK_REVIEWS: dict[str, list[str]] = {
    "VJ": [
        "Cheap fares but seats are very cramped, legroom is tight for tall people.",
        "Flight was delayed by 2 hours, poor punctuality. Staff were friendly though.",
        "Great value for money but the food was stale and overpriced.",
        "Crew was helpful and professional. Clean cabin but no entertainment.",
        "Terrible experience, flight cancelled without notice. Will avoid next time.",
        "Affordable ticket, on time departure. Meal was basic but okay.",
        "Seats uncomfortable, poor service from staff. Not worth the cheap price.",
        "Smooth boarding process, friendly attendants. Great budget option.",
    ],
    "VN": [
        "Excellent service from the crew, very professional and attentive.",
        "Comfortable seats with good legroom. Food was delicious and well presented.",
        "Punctual departure and arrival. Great overall experience.",
        "Good value for the price. Clean cabin and friendly staff.",
        "Meal was cold and the coffee tasted bad. Disappointing catering.",
        "Spacious seats, smooth flight. Highly recommend Vietnam Airlines.",
        "Slight delay but crew kept us informed. Professional service throughout.",
        "Best airline in Vietnam. Wonderful food and comfortable seats.",
    ],
    "NH": [
        "Amazing service, ANA staff are incredibly polite and professional.",
        "Seat comfort is excellent, spacious legroom. Great Japanese meal onboard.",
        "Punctual as always, on time arrival. Efficient boarding process.",
        "Excellent food, delicious Japanese cuisine. Clean and modern cabin.",
        "Wonderful experience, friendly crew. Best service I have had on any airline.",
        "Slightly expensive but worth it. Comfortable and punctual.",
        "Good entertainment system, comfortable seat. Smooth and enjoyable flight.",
        "Impressed by the cleanliness and attention to detail. Fantastic airline.",
    ],
    "JL": [
        "Great service, Japan Airlines crew are always professional and courteous.",
        "Comfortable seats, excellent legroom. Food was fantastic, authentic Japanese.",
        "On time departure and arrival. Very punctual and efficient.",
        "Excellent value for a premium airline. Clean and modern aircraft.",
        "Wonderful meal, delicious food. Friendly and helpful staff throughout.",
        "One of the best airlines. Spacious seats and amazing service.",
        "Smooth flight, professional crew. Highly recommend Japan Airlines.",
        "Slight delay but overall great experience. Comfortable and clean.",
    ],
    "SQ": [
        "World class service, Singapore Airlines sets the standard for excellence.",
        "Incredible seat comfort, spacious and luxurious. Best business class.",
        "Punctual and efficient as always. Smooth boarding and departure.",
        "Amazing food, gourmet meals. Excellent value for the quality provided.",
        "Friendly and professional crew. Clean, modern, comfortable cabin.",
        "Best airline I have flown. Wonderful service and delicious food.",
        "Expensive but worth every dollar. Fantastic experience start to finish.",
        "Impressed by the attention to detail. Highly recommend Singapore Airlines.",
    ],
    "TG": [
        "Good service, Thai Airways crew are friendly and welcoming.",
        "Comfortable seats, decent legroom. Thai food was delicious.",
        "Generally punctual. Smooth flight with professional staff.",
        "Good value for money. Clean cabin and tasty meals.",
        "Average experience, seats could be more comfortable. Food was okay.",
        "Friendly crew, smooth boarding. Decent option for the price.",
        "Flight was delayed, poor punctuality. Staff were apologetic though.",
        "Solid airline, good service. Recommend Thai Airways for regional flights.",
    ],
    "KE": [
        "Great service, Korean Air crew are professional and attentive.",
        "Comfortable seats with good legroom. Korean meal was delicious.",
        "On time departure, punctual arrival. Efficient and smooth.",
        "Good value, comfortable cabin. Clean and modern aircraft.",
        "Professional crew, tasty food. Highly recommend Korean Air.",
        "Seats were cramped on the A330. Food was good though.",
        "Smooth flight, friendly staff. Great experience overall.",
        "Slight delay but crew kept us informed. Good service throughout.",
    ],
    "OZ": [
        "Good service from Asiana crew, friendly and helpful.",
        "Comfortable seats, decent legroom. Food was well prepared.",
        "Punctual departure, on time arrival. Smooth and efficient.",
        "Good value for money. Clean cabin and professional staff.",
        "Average comfort, seats felt cramped. Meal was decent though.",
        "Friendly crew, smooth boarding. Asiana is a solid choice.",
        "Delayed flight, poor punctuality. Staff were nice about it.",
        "Good airline for the price. Recommend Asiana for Korea routes.",
    ],
}


#Get airline reviews, right now the function just use the mock reviews.
async def get_airline_reviews(airline_code: str, mock: bool = False) -> list[str]:
    if mock:
        return _MOCK_REVIEWS.get(airline_code, [])

    logger.info(
        "Using curated review data for %s — real review provider not configured",
        airline_code,
    )
    return _MOCK_REVIEWS.get(airline_code, [])


def _classify_review_category(text: str) -> str:
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in text_lower)
    best_cat = max(scores, key=scores.get)
    return best_cat if scores[best_cat] > 0 else "service"


def _score_sentiment(text: str) -> float:
    text_lower = text.lower()
    pos = sum(1 for w in _POSITIVE_WORDS if w in text_lower)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in text_lower)
    if pos + neg == 0:
        return 0.5
    return pos / (pos + neg)


def analyze_reviews(
    reviews: list[str],
    airline_code: str = "UNKNOWN",
) -> AirlineReview:
    if not reviews:
        return AirlineReview(
            source=airline_code,
            overall_rating=0.0,
            categories={},
            sentiment_summary="No reviews available.",
            sample_positive=[],
            sample_negative=[],
            total_reviews_analyzed=0,
        )

    sentiments = [_score_sentiment(r) for r in reviews]
    avg_sentiment = sum(sentiments) / len(sentiments)
    overall_rating = round(1.0 + avg_sentiment * 4.0, 1)

    category_scores: dict[str, list[float]] = {}
    positive_samples: list[str] = []
    negative_samples: list[str] = []

    for review, sentiment in zip(reviews, sentiments, strict=True):
        cat = _classify_review_category(review)
        category_scores.setdefault(cat, []).append(sentiment)
        if sentiment >= 0.6 and len(positive_samples) < 3:
            positive_samples.append(review)
        elif sentiment <= 0.4 and len(negative_samples) < 3:
            negative_samples.append(review)

    categories = {
        cat: round(sum(scores) / len(scores) * 5.0, 1)
        for cat, scores in category_scores.items()
    }

    positive_count = sum(1 for s in sentiments if s >= 0.6)
    negative_count = sum(1 for s in sentiments if s <= 0.4)
    neutral_count = len(sentiments) - positive_count - negative_count

    sentiment_summary = (
        f"{positive_count} positive, {neutral_count} neutral, {negative_count} negative "
        f"out of {len(reviews)} reviews."
    )

    return AirlineReview(
        source=airline_code,
        overall_rating=overall_rating,
        categories=categories,
        sentiment_summary=sentiment_summary,
        sample_positive=positive_samples,
        sample_negative=negative_samples,
        total_reviews_analyzed=len(reviews),
    )


def compare_airlines(
    reviews_dict: dict[str, AirlineReview],
    priority: str = "balanced",
) -> tuple[str, str]:
    if not reviews_dict:
        return "No airlines to compare.", "Unable to recommend — no review data."

    ranked = sorted(reviews_dict.items(), key=lambda kv: kv[1].overall_rating, reverse=True)

    if priority == "price":
        key = "value"
    elif priority == "comfort":
        key = "seat"
    elif priority == "speed":
        key = "punctuality"
    else:
        key = None

    if key and key in ranked[0][1].categories:
        ranked = sorted(
            reviews_dict.items(),
            key=lambda kv: kv[1].categories.get(key, 0),
            reverse=True,
        )

    best_code, best_review = ranked[0]
    names = ", ".join(f"{code} ({r.overall_rating}/5)" for code, r in ranked)

    comparison = (
        f"Airline ratings: {names}. "
        f"{best_code} ranks highest overall at {best_review.overall_rating}/5."
    )

    recommendation = (
        f"Based on {priority} priority, {best_code} is recommended "
        f"with a rating of {best_review.overall_rating}/5. "
        f"{best_review.sentiment_summary}"
    )

    return comparison, recommendation

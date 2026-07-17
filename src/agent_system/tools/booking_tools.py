import datetime
import logging
import os
import random
import string

from agent_system.models import BookingConfirmation, BookingDetails
from agent_system.tools.flight_tools import find_mock_flight

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _mask_passport(passport: str) -> str:
    if len(passport) <= 2:
        return "***"
    return f"{passport[0]}{'*' * (len(passport) - 2)}{passport[-1]}"


def generate_confirmation_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=6))


async def create_booking(
    flight_number: str,
    passenger_details: BookingDetails | dict,
    mock: bool = False,
) -> BookingConfirmation:
    if isinstance(passenger_details, dict):
        passenger_details = BookingDetails(**passenger_details)

    logger.info(
        "Booking request: flight=%s passenger=%s email=%s passport=%s",
        flight_number,
        passenger_details.passenger_name,
        _mask_email(passenger_details.passenger_email),
        _mask_passport(passenger_details.passport_number),
    )

    flight = find_mock_flight(flight_number)
    if flight is None:
        logger.warning("Booking failed — unknown flight %s", flight_number)
        return BookingConfirmation(
            confirmation_code="",
            flight_number=flight_number,
            route="UNKNOWN",
            departure=datetime.datetime.now(),
            arrival=datetime.datetime.now(),
            passenger_name=passenger_details.passenger_name,
            passenger_email=passenger_details.passenger_email,
            price_usd=0.0,
            status="failed",
            ticket_email_sent=False,
            booking_timestamp=datetime.datetime.now(),
        )

    payment = await process_payment(flight.price_usd, mock=mock)
    if payment.get("status") != "succeeded":
        logger.warning("Payment failed for flight %s", flight_number)
        return BookingConfirmation(
            confirmation_code="",
            flight_number=flight_number,
            route=f"{flight.departure}→{flight.arrival}",
            departure=flight.departure,
            arrival=flight.arrival,
            passenger_name=passenger_details.passenger_name,
            passenger_email=passenger_details.passenger_email,
            price_usd=flight.price_usd,
            status="failed",
            ticket_email_sent=False,
            booking_timestamp=datetime.datetime.now(),
        )

    confirmation = BookingConfirmation(
        confirmation_code=generate_confirmation_code(),
        flight_number=flight_number,
        route=f"{flight.airline_name} {flight_number}",
        departure=flight.departure,
        arrival=flight.arrival,
        passenger_name=passenger_details.passenger_name,
        passenger_email=passenger_details.passenger_email,
        price_usd=flight.price_usd,
        status="confirmed",
        ticket_email_sent=False,
        booking_timestamp=datetime.datetime.now(),
    )

    email_sent = await send_confirmation_email(
        confirmation,
        passenger_email=passenger_details.passenger_email,
        mock=mock,
    )
    confirmation = confirmation.model_copy(update={"ticket_email_sent": email_sent})

    logger.info(
        "Booking confirmed: code=%s flight=%s email_sent=%s",
        confirmation.confirmation_code,
        flight_number,
        email_sent,
    )
    return confirmation


async def process_payment(
    amount: float,
    currency: str = "USD",
    mock: bool = False,
) -> dict:
    if mock:
        logger.info("Mock payment: $%.2f %s — succeeded", amount, currency)
        return {
            "id": "mock_pay_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12)),
            "status": "succeeded",
            "amount": amount,
            "currency": currency,
        }

    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        logger.warning("STRIPE_SECRET_KEY not set — using mock payment")
        return {
            "id": "mock_pay_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12)),
            "status": "succeeded",
            "amount": amount,
            "currency": currency,
        }

    logger.info("Stripe payment: $%.2f %s", amount, currency)
    try:
        import stripe

        stripe.api_key = stripe_key
        intent = stripe.PaymentIntent.create(
            amount=int(amount * 100),
            currency=currency.lower(),
            metadata={"source": "flight_booking"},
        )
        return {
            "id": intent.id,
            "status": intent.status,
            "amount": amount,
            "currency": currency,
        }
    except Exception as exc:
        logger.error("Stripe payment failed: %s", exc)
        return {"id": "", "status": "failed", "amount": amount, "currency": currency}


async def send_confirmation_email(
    booking: BookingConfirmation,
    passenger_email: str | None = None,
    mock: bool = False,
) -> bool:
    email = passenger_email or booking.passenger_email
    if not email:
        logger.warning("No email address — skipping confirmation email")
        return False

    if mock:
        logger.info("Mock email sent to %s for booking %s", _mask_email(email), booking.confirmation_code)
        return True

    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    if not sendgrid_key:
        logger.warning("SENDGRID_API_KEY not set — using mock email")
        logger.info("Mock email sent to %s for booking %s", _mask_email(email), booking.confirmation_code)
        return True

    logger.info("SendGrid email to %s for booking %s", _mask_email(email), booking.confirmation_code)
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email="noreply@flightadvisor.ai",
            to_emails=email,
            subject=f"Flight Confirmation {booking.confirmation_code}",
            html_content=(
                f"<h2>Booking Confirmed</h2>"
                f"<p>Confirmation: {booking.confirmation_code}</p>"
                f"<p>Flight: {booking.flight_number}</p>"
                f"<p>Passenger: {booking.passenger_name}</p>"
                f"<p>Price: ${booking.price_usd:.2f}</p>"
            ),
        )
        client = SendGridAPIClient(sendgrid_key)
        client.send(message)
        logger.info("Email sent to %s", _mask_email(email))
        return True
    except Exception as exc:
        logger.error("SendGrid email failed: %s", exc)
        return False

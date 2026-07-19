import logging
import re

from agent_system.models import BookingDetails
from agent_system.tools.booking_tools import create_booking
from agent_system.tools.flight_tools import find_mock_flight

logger = logging.getLogger(__name__)

_CONFIRM_PATTERN = re.compile(
    r"\b(yes|confirm|book\s+it|proceed|sure|đặt|xác\s+nhận|đồng\s+ý)\b",
    re.IGNORECASE,
)


def _is_confirmation(query: str) -> bool:
    return bool(_CONFIRM_PATTERN.search(query))


def _resolve_flight(state: dict):
    selected = state.get("selected_flight")
    if selected is not None:
        if isinstance(selected, dict):
            return find_mock_flight(selected.get("flight_number", ""))
        return selected

    plan = state.get("plan")
    if plan and plan.booking_details and plan.booking_details.flight_number:
        return find_mock_flight(plan.booking_details.flight_number)

    return None


def _resolve_passenger_details(state: dict) -> BookingDetails | None:
    pending = state.get("pending_booking")
    if isinstance(pending, dict):
        try:
            return BookingDetails(**pending)
        except Exception:
            pass

    plan = state.get("plan")
    if plan and plan.booking_details:
        return plan.booking_details

    return None


async def booking_node(state: dict) -> dict:
    query = state.get("query", "")
    mock = state.get("mock_mode", True)
    plan = state.get("plan")
    language = plan.language if plan else "en"

    flight = _resolve_flight(state)
    if flight is None:
        msg = (
            "Which flight would you like to book? Please provide a flight number or select a flight from the results."
            if language == "en"
            else "Bạn muốn đặt chuyến bay nào? Vui lòng cung cấp số hiệu chuyến bay hoặc chọn từ kết quả tìm kiếm."
        )
        return {"final_response": msg}

    passenger = _resolve_passenger_details(state)
    if passenger is None or not passenger.passenger_email or not passenger.passport_number:
        msg = (
            f"To book flight {flight.flight_number} ({flight.airline_name}), "
            f"please provide: passenger name, email, and passport number."
            if language == "en"
            else f"Để đặt chuyến bay {flight.flight_number} ({flight.airline_name}), "
            f"vui lòng cung cấp: họ tên hành khách, email và số hộ chiếu."
        )
        return {"final_response": msg}

    pending = state.get("pending_booking")
    if pending is None and not _is_confirmation(query):
        pending_data = passenger.model_dump()
        msg = (
            f"Please confirm your booking:\n"
            f"Flight: {flight.flight_number} ({flight.airline_name})\n"
            f"Route: {flight.airline_name}\n"
            f"Price: ${flight.price_usd:.2f}\n"
            f"Passenger: {passenger.passenger_name}\n"
            f"Email: {passenger.passenger_email}\n\n"
            f"Reply 'yes' to confirm."
            if language == "en"
            else f"Vui lòng xác nhận đặt vé:\n"
            f"Chuyến bay: {flight.flight_number} ({flight.airline_name})\n"
            f"Giá: ${flight.price_usd:.2f}\n"
            f"Hành khách: {passenger.passenger_name}\n"
            f"Email: {passenger.passenger_email}\n\n"
            f"Trả lời 'xác nhận' để tiếp tục."
        )
        return {"final_response": msg, "pending_booking": pending_data}

    try:
        confirmation = await create_booking(
            flight_number=flight.flight_number,
            passenger_details=passenger,
            mock=mock,
        )
    except Exception as exc:
        logger.error("Booking failed: %s", exc)
        return {
            "final_response": f"Booking failed: {exc}" if language == "en" else f"Đặt vé thất bại: {exc}",
            "errors": [f"booking: {exc}"],
            "pending_booking": None,
        }

    if confirmation.status != "confirmed":
        msg = (
            f"Booking could not be completed (status: {confirmation.status}). "
            f"Please try again or contact support."
            if language == "en"
            else f"Không thể hoàn tất đặt vé (trạng thái: {confirmation.status}). "
            f"Vui lòng thử lại hoặc liên hệ hỗ trợ."
        )
        return {
            "final_response": msg,
            "booking_confirmation": confirmation,
            "pending_booking": None,
            "errors": [f"booking: status={confirmation.status}"],
        }

    msg = (
        f"Booking confirmed!\n"
        f"Confirmation code: {confirmation.confirmation_code}\n"
        f"Flight: {confirmation.flight_number}\n"
        f"Passenger: {confirmation.passenger_name}\n"
        f"Price: ${confirmation.price_usd:.2f}\n"
        f"Email sent: {'yes' if confirmation.ticket_email_sent else 'no'}"
        if language == "en"
        else f"Đặt vé thành công!\n"
        f"Mã xác nhận: {confirmation.confirmation_code}\n"
        f"Chuyến bay: {confirmation.flight_number}\n"
        f"Hành khách: {confirmation.passenger_name}\n"
        f"Giá: ${confirmation.price_usd:.2f}\n"
        f"Email đã gửi: {'có' if confirmation.ticket_email_sent else 'không'}"
    )

    logger.info("Booking confirmed: %s for %s", confirmation.confirmation_code, flight.flight_number)

    return {
        "booking_confirmation": confirmation,
        "final_response": msg,
        "pending_booking": None,
    }

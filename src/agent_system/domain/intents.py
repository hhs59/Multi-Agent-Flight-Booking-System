from enum import StrEnum


class AgentIntent(StrEnum):
    SEARCH_FLIGHTS = "search_flights"
    TRIP_DISCOVERY = "trip_discovery"
    TRIP_INSPIRATION = "trip_inspiration"
    SEARCH_INSPIRATION_OPTION = "search_inspiration_option"
    ADVISE = "advise"
    START_BOOKING = "start_booking"
    CONFIRM_BOOKING = "confirm_booking"
    MANAGE_BOOKING = "manage_booking"
    CREATE_WATCH = "create_watch"
    MANAGE_WATCH = "manage_watch"
    UPDATE_PROFILE = "update_profile"
    UNCLEAR = "unclear"

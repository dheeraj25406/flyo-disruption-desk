from dataclasses import dataclass


DEMO_EVENT_PREFIX = "demo-india-case-"
INDIAN_SERVICE_DATES = ("2026-10-28", "2026-10-29", "2026-10-30")

INDIAN_ROUTES = (
    ("HYD", "DEL"),
    ("BLR", "BOM"),
    ("MAA", "HYD"),
    ("DEL", "BLR"),
    ("BOM", "COK"),
    ("CCU", "DEL"),
    ("PNQ", "AMD"),
    ("AMD", "GOI"),
    ("COK", "BLR"),
    ("GOI", "MAA"),
)

PASSENGER_NAMES = (
    "Aarav Sharma", "Priya Reddy", "Arjun Patel", "Ananya Iyer", "Rahul Nair",
    "Meera Banerjee", "Vikram Singh", "Kavya Menon", "Rohan Deshmukh", "Ishita Das",
    "Aditya Rao", "Sneha Kulkarni", "Karthik Subramanian", "Aisha Khan", "Devika Pillai",
    "Manav Bhatia", "Tanvi Joshi", "Siddharth Mukherjee", "Nisha Fernandes", "Harsh Venkatesh",
    "Pooja Choudhary", "Neil D'Souza", "Lakshmi Krishnan", "Kabir Sethi", "Riya Chatterjee",
)

AIRLINE_CODES = ("AI", "6E", "UK", "SG", "IX", "QP")
AIRLINE_NAMES = (
    "Air India", "IndiGo", "Vistara", "SpiceJet", "Air India Express", "Akasa Air",
)
DISRUPTION_STATUSES = (
    "cancel", "delay", "cancel", "delay", "cancel", "delay", "cancel", "delay",
    "missed_connection", "cancel", "delay", "cancel", "delay", "cancel", "delay",
    "cancel", "delay", "missed_connection", "cancel", "cancel", "missed_connection",
    "delay", "cancel", "delay", "missed_connection",
)


@dataclass(frozen=True)
class DemoDisruption:
    event_id: str
    flight_id: str
    airline_name: str
    booking_ref: str
    passenger_name: str
    status: str
    reason: str
    origin: str
    destination: str
    service_date: str
    passenger_constraints: dict
    has_tight_connection: bool


def demo_disruptions() -> tuple[DemoDisruption, ...]:
    records = []
    for index, (passenger_name, status) in enumerate(zip(PASSENGER_NAMES, DISRUPTION_STATUSES)):
        route_index = index % len(INDIAN_ROUTES)
        day_index = index // len(INDIAN_ROUTES)
        origin, destination = INDIAN_ROUTES[route_index]
        flight_id = "DEMO-{}-{:04d}".format(AIRLINE_CODES[index % len(AIRLINE_CODES)], 2101 + index)
        constraints = {"max_departure_shift_minutes": 360}
        if index == 3:
            constraints["max_departure_shift_minutes"] = 30
        elif index == 22:
            constraints["max_departure_shift_minutes"] = 0
        records.append(DemoDisruption(
            event_id="{}{:02d}".format(DEMO_EVENT_PREFIX, index + 1),
            flight_id=flight_id,
            airline_name=AIRLINE_NAMES[index % len(AIRLINE_NAMES)],
            booking_ref="INDEMO{:04d}".format(index + 1),
            passenger_name=passenger_name,
            status=status,
            reason={
                "cancel": (
                    "Synthetic operational cancellation",
                    "Synthetic weather-related cancellation",
                    "Synthetic aircraft rotation cancellation",
                ),
                "delay": (
                    "Synthetic significant departure delay",
                    "Synthetic inbound-aircraft delay",
                    "Synthetic weather-related delay",
                ),
                "missed_connection": (
                    "Synthetic missed connection after an arrival delay",
                    "Synthetic short connection window",
                ),
            }[status][index % {
                "cancel": 3,
                "delay": 3,
                "missed_connection": 2,
            }[status]],
            origin=origin,
            destination=destination,
            service_date=INDIAN_SERVICE_DATES[day_index],
            passenger_constraints=constraints,
            has_tight_connection=status == "missed_connection",
        ))
    return tuple(records)

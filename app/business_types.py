"""Vocabulary presets per kind of business.

The database stores four generic rating columns - quality, service, environment,
value - and each business type only decides what they are *called*. That way a salon
and a restaurant share one schema, one dashboard and one set of statistics, while a
guest still sees words that make sense for the place they are standing in.

`unit` is what one tag is stuck to: a table, a counter, a treatment room.
"""

TYPES = {
    "restaurant": {
        "label": "Restaurant, café or bar",
        "unit": "Table",
        "customer": "guest",
        "categories": {
            "quality": "Food",
            "service": "Service",
            "environment": "Ambience",
            "value": "Value",
        },
        "chips_good": ["Great food", "Friendly staff", "Fast service", "Nice ambience",
                       "Good value", "Very clean"],
        "chips_bad": ["Slow service", "Food quality", "Order was wrong", "Cleanliness",
                      "Too expensive", "Staff attitude"],
    },
    "retail": {
        "label": "Shop or retail store",
        "unit": "Counter",
        "customer": "customer",
        "categories": {
            "quality": "Products",
            "service": "Staff",
            "environment": "Store",
            "value": "Value",
        },
        "chips_good": ["Great products", "Helpful staff", "Quick billing", "Well organised",
                       "Good prices", "Very clean"],
        "chips_bad": ["Long queue", "Product quality", "Out of stock", "Staff attitude",
                      "Too expensive", "Hard to find things"],
    },
    "salon": {
        "label": "Salon, spa or barber",
        "unit": "Station",
        "customer": "client",
        "categories": {
            "quality": "Result",
            "service": "Staff",
            "environment": "Cleanliness",
            "value": "Value",
        },
        "chips_good": ["Happy with result", "Skilled staff", "On time", "Very clean",
                       "Relaxing", "Good value"],
        "chips_bad": ["Not what I asked for", "Long wait", "Cleanliness", "Staff attitude",
                      "Too expensive", "Felt rushed"],
    },
    "clinic": {
        "label": "Clinic, hospital or dentist",
        "unit": "Room",
        "customer": "patient",
        "categories": {
            "quality": "Care",
            "service": "Staff",
            "environment": "Facilities",
            "value": "Value",
        },
        "chips_good": ["Doctor listened", "Caring staff", "Seen on time", "Very clean",
                       "Clear explanation", "Fair billing"],
        "chips_bad": ["Long wait", "Felt rushed", "Cleanliness", "Staff attitude",
                      "Billing was unclear", "Hard to get answers"],
    },
    "hotel": {
        "label": "Hotel or guest house",
        "unit": "Room",
        "customer": "guest",
        "categories": {
            "quality": "Room",
            "service": "Staff",
            "environment": "Facilities",
            "value": "Value",
        },
        "chips_good": ["Comfortable room", "Helpful staff", "Quick check-in", "Very clean",
                       "Quiet", "Good value"],
        "chips_bad": ["Room not clean", "Slow check-in", "Noisy", "Staff attitude",
                      "Something was broken", "Too expensive"],
    },
    "office": {
        "label": "Office or service desk",
        "unit": "Desk",
        "customer": "visitor",
        "categories": {
            "quality": "Outcome",
            "service": "Staff",
            "environment": "Facilities",
            "value": "Value",
        },
        "chips_good": ["Problem solved", "Helpful staff", "Quick service", "Clear process",
                       "Comfortable wait", "Well organised"],
        "chips_bad": ["Long wait", "Problem unresolved", "Confusing process", "Staff attitude",
                      "Sent between desks", "Hard to reach anyone"],
    },
    "gym": {
        "label": "Gym or fitness studio",
        "unit": "Station",
        "customer": "member",
        "categories": {
            "quality": "Equipment",
            "service": "Trainers",
            "environment": "Cleanliness",
            "value": "Value",
        },
        "chips_good": ["Good equipment", "Helpful trainers", "Never crowded", "Very clean",
                       "Good classes", "Good value"],
        "chips_bad": ["Equipment broken", "Too crowded", "Cleanliness", "Staff attitude",
                      "Too expensive", "Class was cancelled"],
    },
    "other": {
        "label": "Something else",
        "unit": "Spot",
        "customer": "customer",
        "categories": {
            "quality": "Quality",
            "service": "Service",
            "environment": "Place",
            "value": "Value",
        },
        "chips_good": ["Great experience", "Helpful staff", "Quick service", "Very clean",
                       "Good value", "Would return"],
        "chips_bad": ["Long wait", "Quality", "Cleanliness", "Staff attitude",
                      "Too expensive", "Not as promised"],
    },
}

DEFAULT = "restaurant"

# The four rating columns, in the order they are shown everywhere.
CATEGORY_KEYS = ["quality", "service", "environment", "value"]


def preset(business_type: str | None) -> dict:
    """Vocabulary for a business type, falling back to the generic set."""
    return TYPES.get(business_type or "", TYPES["other"])


def choices() -> list[tuple[str, str]]:
    """(value, label) pairs for the signup and settings dropdowns."""
    return [(key, spec["label"]) for key, spec in TYPES.items()]

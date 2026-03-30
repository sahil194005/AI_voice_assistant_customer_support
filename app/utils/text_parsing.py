from datetime import datetime, timedelta
from dateutil import parser


def parse_date(text: str):
    text = text.lower()
    today = datetime.today()

    if "today" in text:
        return today.strftime("%Y-%m-%d")

    if "tomorrow" in text:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    if "day after tomorrow" in text:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        dt = parser.parse(text, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def normalize_department(text: str):
    text = text.lower()

    mapping = {
        "cardiology": ["cardio", "heart", "cardiologist"],
        "dermatology": ["skin", "derma", "dermatologist"],
        "general": ["general", "physician", "doctor"],
    }

    for standard, keywords in mapping.items():
        for word in keywords:
            if word in text:
                return standard

    return None

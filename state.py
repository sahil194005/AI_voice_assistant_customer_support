sessions = {}


def get_session(phone):
    if phone not in sessions:
        sessions[phone] = {"intent": None, "department": None, "date": None}
    return sessions[phone]


def reset_session(phone):
    sessions.pop(phone, None)

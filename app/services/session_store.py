from typing import Dict, Optional

SessionData = Dict[str, Optional[str]]
_sessions: Dict[str, SessionData] = {}


def get_session(phone: str) -> SessionData:
    if phone not in _sessions:
        _sessions[phone] = {"intent": None, "department": None, "date": None}
    return _sessions[phone]


def reset_session(phone: str) -> None:
    _sessions.pop(phone, None)

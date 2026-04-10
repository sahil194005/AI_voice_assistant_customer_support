from typing import Dict, Optional

SessionData = Dict[str, Optional[str]]
_sessions: Dict[str, SessionData] = {}


def get_session(phone: str) -> SessionData:
    try:
        if phone not in _sessions:
            _sessions[phone] = {"intent": None, "department": None, "date": None, "time": None}
        return _sessions[phone]
    except Exception as error:
        print(f"get_session failed: {error}")
        raise


def reset_session(phone: str) -> None:
    try:
        if phone in _sessions:
            del _sessions[phone]
    except Exception as error:
        print(f"reset_session failed: {error}")
        raise

import asyncio
import base64
import binascii
import contextlib
import json
import os
from dataclasses import dataclass

from deepgram import AsyncDeepgramClient
from deepgram.core import EventType
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import DEEPGRAM_MODEL
from app.services.appointment_service import ALLOWED_DEPARTMENTS, create_appointment
from app.services.llm_service import extract_intent, stream_gwen_response
from app.services.tts_service import stream_tts_ulaw_8k
from app.utils.text_parsing import normalize_department, parse_date

_GREETING = "Hi! How may I help you today?"

# Per-stream booking state keyed by stream_sid
_STREAM_SESSIONS: dict[str, dict] = {}


def _get_session(stream_sid: str) -> dict:
    if stream_sid not in _STREAM_SESSIONS:
        _STREAM_SESSIONS[stream_sid] = {"intent": None, "department": None, "date": None}
    return _STREAM_SESSIONS[stream_sid]


def _clear_session(stream_sid: str) -> None:
    _STREAM_SESSIONS.pop(stream_sid, None)


def _build_deepgram_client() -> AsyncDeepgramClient:
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY is missing. Add it to your .env file.")
    return AsyncDeepgramClient(api_key=api_key)


def _extract_transcript(message) -> str:
    transcript = getattr(message, "transcript", "")
    if isinstance(transcript, str):
        return transcript.strip()
    return ""


def _is_final_turn(message) -> bool:
    return bool(getattr(message, "is_final", False)) or getattr(message, "event", None) == "EndOfTurn"


def _flushable_text(text: str) -> bool:
    clean = text.rstrip()
    if not clean:
        return False
    if clean.endswith((".", "!", "?")):
        return True
    return len(clean) >= 90 and clean.endswith((" ", ",", ";", ":"))


async def _send_twilio_media(websocket: WebSocket, stream_sid: str, audio_chunk: bytes, send_lock: asyncio.Lock) -> None:
    payload = base64.b64encode(audio_chunk).decode("ascii")
    message = {
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": payload},
    }
    async with send_lock:
        await websocket.send_text(json.dumps(message))


async def _speak(text: str, websocket: WebSocket, stream_sid: str, send_lock: asyncio.Lock) -> None:
    """Convert text to speech and stream audio to Twilio."""
    async for audio_chunk in stream_tts_ulaw_8k(text):
        await _send_twilio_media(websocket, stream_sid, audio_chunk, send_lock)


@dataclass(slots=True)
class _StreamState:
    stream_sid: str = ""
    caller_phone: str = ""
    active_response_task: asyncio.Task | None = None


async def process_turn(
    transcript: str,
    stream_sid: str,
    caller_phone: str,
    websocket: WebSocket,
    send_lock: asyncio.Lock,
) -> None:
    """
    Handle one completed user utterance:
      1. Extract intent/entities from transcript.
      2. Update per-stream booking session.
      3. Respond: ask for missing booking parameters, confirm booking,
         or fall back to streaming LLM reply for non-booking queries.
    """
    session = _get_session(stream_sid)

    try:
        intent_data = await extract_intent(transcript)
    except Exception as err:
        print(f"Intent extraction error: {err}")
        intent_data = {}

    # --- update session from extracted data ---
    if not session["intent"] and intent_data.get("intent") and intent_data["intent"] != "unknown":
        session["intent"] = intent_data["intent"]

    if not session["department"] and intent_data.get("department"):
        dept = normalize_department(str(intent_data["department"])) or str(intent_data["department"]).lower()
        if dept in ALLOWED_DEPARTMENTS:
            session["department"] = dept

    if not session["date"] and intent_data.get("date"):
        parsed = parse_date(str(intent_data["date"]))
        if parsed:
            session["date"] = parsed

    # Also try free-text fallback for dept/date when LLM didn't extract them
    if not session["department"]:
        dept = normalize_department(transcript)
        if dept:
            session["department"] = dept

    if not session["date"]:
        parsed = parse_date(transcript)
        if parsed:
            session["date"] = parsed

    # --- decide response ---
    if session["intent"] != "book_appointment":
        # Generic streaming LLM response
        buffer = ""
        try:
            async for token in stream_gwen_response(transcript):
                buffer += token
                if not _flushable_text(buffer):
                    continue
                chunk = buffer.strip()
                buffer = ""
                await _speak(chunk, websocket, stream_sid, send_lock)
            if buffer.strip():
                await _speak(buffer.strip(), websocket, stream_sid, send_lock)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            print(f"LLM stream error: {err}")
        return

    # --- booking state machine ---
    if not session["department"]:
        response_text = "Which department would you like? We have cardiology, dermatology, or general."
    elif session["department"] not in ALLOWED_DEPARTMENTS:
        session["department"] = None
        response_text = "Sorry, that department is not available. Please choose cardiology, dermatology, or general."
    elif not session["date"]:
        response_text = "What date would you like the appointment?"
    else:
        booking_identity = caller_phone or stream_sid
        response_text = create_appointment(booking_identity, session["department"], session["date"])
        _clear_session(stream_sid)

    try:
        await _speak(response_text, websocket, stream_sid, send_lock)
    except asyncio.CancelledError:
        raise
    except Exception as err:
        print(f"TTS/send error: {err}")



async def _handle_twilio_packet(
    *,
    packet: dict,
    websocket: WebSocket,
    send_audio,
    connection,
    send_lock: asyncio.Lock,
    stt_dropped: asyncio.Event,
    state: _StreamState,
) -> bool:
    """
    Handle one Twilio media stream packet.

    Returns True if the loop should continue, False if it should stop.
    """
    event = packet.get("event")

    if event == "media":
        payload = packet.get("media", {}).get("payload")
        if not payload:
            return True
        try:
            audio_chunk = base64.b64decode(payload)
        except (binascii.Error, ValueError):
            return True
        await send_audio(audio_chunk)
        return True

    if event == "start":
        start_data = packet.get("start", {})
        state.stream_sid = start_data.get("streamSid", "")
        custom_parameters = start_data.get("customParameters", {}) or {}
        state.caller_phone = (
            custom_parameters.get("caller_phone")
            or custom_parameters.get("From")
            or custom_parameters.get("from")
            or ""
        )
        if state.stream_sid:
            # Play a greeting immediately once Twilio stream is ready.
            if state.active_response_task and not state.active_response_task.done():
                state.active_response_task.cancel()
            state.active_response_task = asyncio.create_task(
                _speak(_GREETING, websocket, state.stream_sid, send_lock)
            )
        return True

    if event == "stop":
        with contextlib.suppress(Exception):
            await connection.send_close_stream()
        stt_dropped.set()
        return False

    if event:
        print(f"Unhandled Twilio event: {event}")

    return True


def _attach_deepgram_handlers(
    *,
    connection,
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    stt_dropped: asyncio.Event,
    state: _StreamState,
) -> None:
    async def _close_twilio(code: int) -> None:
        with contextlib.suppress(Exception):
            await websocket.close(code=code)

    def on_message(message) -> None:
        transcript = _extract_transcript(message)
        if not transcript:
            return

        if _is_final_turn(message):
            print(f"Final: {transcript}")
            if state.stream_sid:
                if state.active_response_task and not state.active_response_task.done():
                    state.active_response_task.cancel()
                state.active_response_task = asyncio.create_task(
                    process_turn(transcript, state.stream_sid, state.caller_phone, websocket, send_lock)
                )
        else:
            print(f"Interim: {transcript}")

    def on_close(_) -> None:
        stt_dropped.set()
        asyncio.create_task(_close_twilio(code=1011))

    def on_error(error: Exception) -> None:
        stt_dropped.set()
        print(f"Deepgram error: {error}")
        asyncio.create_task(_close_twilio(code=1011))

    connection.on(EventType.MESSAGE, on_message)
    connection.on(EventType.CLOSE, on_close)
    connection.on(EventType.ERROR, on_error)


async def _run_deepgram_twilio_bridge(connection, websocket: WebSocket) -> None:
    stt_dropped = asyncio.Event()
    send_lock = asyncio.Lock()
    state = _StreamState()

    _attach_deepgram_handlers(
        connection=connection,
        websocket=websocket,
        send_lock=send_lock,
        stt_dropped=stt_dropped,
        state=state,
    )

    dg_task = asyncio.create_task(connection.start_listening())
    send_audio = getattr(connection, "_send", connection.send_media)

    try:
        while not stt_dropped.is_set():
            data = await websocket.receive_text()
            try:
                packet = json.loads(data)
            except json.JSONDecodeError:
                continue

            should_continue = await _handle_twilio_packet(
                packet=packet,
                websocket=websocket,
                send_audio=send_audio,
                connection=connection,
                send_lock=send_lock,
                stt_dropped=stt_dropped,
                state=state,
            )
            if not should_continue:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        stt_dropped.set()
    finally:
        if state.active_response_task and not state.active_response_task.done():
            state.active_response_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.active_response_task

        dg_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dg_task

        _clear_session(state.stream_sid)


async def handle_media_stream(websocket: WebSocket):
    """Bridge Twilio media-stream audio with Deepgram STT and voice responses."""
    try:
        await websocket.accept()
        dg_client = _build_deepgram_client()

        # try:
        async with dg_client.listen.v2.connect(
            model=DEEPGRAM_MODEL,
            encoding="mulaw",
            eot_threshold=300,
            sample_rate=8000,
        ) as connection:
            await _run_deepgram_twilio_bridge(connection, websocket)
        # except ApiError as exc:
        #     if getattr(exc, "status_code", None) != 400:
        #         raise
        #     async with dg_client.listen.v2.connect(
        #         model=DEEPGRAM_MODEL,
        #         encoding="mulaw",
        #         sample_rate=8000,
        #     ) as connection:
        #         await _run_deepgram_twilio_bridge(connection, websocket)
    except Exception as error:
        print(f"WebSocket error: {error}")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)

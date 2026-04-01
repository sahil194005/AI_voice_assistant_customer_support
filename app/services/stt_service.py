import asyncio
import base64
import binascii
import contextlib
import json
import os
import time
from dataclasses import dataclass

from deepgram import AsyncDeepgramClient
from deepgram.core import EventType
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import DEEPGRAM_MODEL
from app.services.appointment_service import create_appointment, get_available_departments
from app.services.llm_service import extract_intent, stream_gwen_response
from app.services.tts_service import stream_tts_ulaw_8k

_GREETING = "Hi! How may I help you today?"

# Per-stream booking state keyed by stream_sid
_STREAM_SESSIONS: dict[str, dict] = {}


def _get_session(stream_sid: str) -> dict:
    if stream_sid not in _STREAM_SESSIONS:
        _STREAM_SESSIONS[stream_sid] = {"intent": None, "department": None, "date": None, "time": None}
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
    return len(clean) >= 45 and clean.endswith((" ", ",", ";", ":", "\n"))


async def _extract_intent_with_timeout(
    transcript: str,
    available_departments: list[str],
    timeout_seconds: float = 1.2,
) -> dict:
    try:
        return await asyncio.wait_for(
            extract_intent(transcript, available_departments),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        # Keep the call responsive even if LLM extraction stalls.
        return {"intent": "unknown", "department": None, "date": None, "time": None, "name": None}
    except Exception as err:
        print(f"Intent extraction error: {err}")
        return {"intent": "unknown", "department": None, "date": None, "time": None, "name": None}


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
    last_speech_media_at: float | None = None


@dataclass(slots=True)
class _TurnMetrics:
    stream_sid: str
    transcript: str
    user_speech_end_at: float
    final_transcript_at: float
    intent_started_at: float | None = None
    intent_done_at: float | None = None
    llm_first_token_at: float | None = None
    tts_first_audio_at: float | None = None
    turn_done_at: float | None = None


def _fmt_ms(start: float | None, end: float | None) -> str:
    if start is None or end is None:
        return "NA"
    return f"{(end - start) * 1000:.0f}"


def _log_turn_metrics(metrics: _TurnMetrics) -> None:
    turn_done = metrics.turn_done_at or time.perf_counter()
    print(
        "LATENCY"
        f" stream={metrics.stream_sid or 'unknown'}"
        f" stt_final_ms={_fmt_ms(metrics.user_speech_end_at, metrics.final_transcript_at)}"
        f" intent_ms={_fmt_ms(metrics.intent_started_at, metrics.intent_done_at)}"
        f" llm_first_token_ms={_fmt_ms(metrics.final_transcript_at, metrics.llm_first_token_at)}"
        f" tts_first_audio_ms={_fmt_ms(metrics.final_transcript_at, metrics.tts_first_audio_at)}"
        f" end_to_end_ms={_fmt_ms(metrics.final_transcript_at, turn_done)}"
        f" text={metrics.transcript[:80]!r}"
    )


async def process_turn(
    transcript: str,
    stream_sid: str,
    caller_phone: str,
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    metrics: _TurnMetrics,
) -> None:
    """
    Handle one completed user utterance:
      1. Extract intent/entities from transcript.
      2. Update per-stream booking session.
      3. Respond: ask for missing booking parameters, confirm booking,
         or fall back to streaming LLM reply for non-booking queries.
    """
    session = _get_session(stream_sid)
    turn_started = metrics.final_transcript_at
    available_departments = get_available_departments()

    metrics.intent_started_at = time.perf_counter()
    intent_data = await _extract_intent_with_timeout(transcript, available_departments)
    metrics.intent_done_at = time.perf_counter()

    # --- update session from extracted data ---
    if not session["intent"] and intent_data.get("intent") and intent_data["intent"] != "unknown":
        session["intent"] = intent_data["intent"]

    if not session["department"] and intent_data.get("department"):
        session["department"] = str(intent_data["department"]).lower()

    if not session["date"] and intent_data.get("date"):
        session["date"] = str(intent_data["date"])

    if not session["time"] and intent_data.get("time"):
        session["time"] = str(intent_data["time"])

    # --- decide response ---
    if session["intent"] != "book_appointment":
        # Generic streaming LLM response
        buffer = ""
        first_audio_sent_at = None
        try:
            async for token in stream_gwen_response(transcript):
                if metrics.llm_first_token_at is None and token:
                    metrics.llm_first_token_at = time.perf_counter()
                buffer += token
                if not _flushable_text(buffer):
                    continue
                chunk = buffer.strip()
                buffer = ""
                await _speak(chunk, websocket, stream_sid, send_lock)
                if first_audio_sent_at is None:
                    first_audio_sent_at = time.perf_counter()
                    metrics.tts_first_audio_at = first_audio_sent_at
            if buffer.strip():
                await _speak(buffer.strip(), websocket, stream_sid, send_lock)
                if first_audio_sent_at is None:
                    first_audio_sent_at = time.perf_counter()
                    metrics.tts_first_audio_at = first_audio_sent_at
        except asyncio.CancelledError:
            raise
        except Exception as err:
            print(f"LLM stream error: {err}")
        if first_audio_sent_at is not None:
            print(f"Latency non-booking first audio: {(first_audio_sent_at - turn_started):.3f}s")
        metrics.turn_done_at = time.perf_counter()
        _log_turn_metrics(metrics)
        return

    # --- booking state machine ---
    if not session["department"]:
        if not available_departments:
            response_text = "Sorry, the clinic does not have appointment facilities configured yet."
        else:
            options = ", ".join(available_departments)
            response_text = f"Which department would you like? Available departments are {options}."
    elif session["department"] not in available_departments:
        session["department"] = None
        response_text = "Sorry, the clinic does not have the facility for that department yet."
    elif not session["date"]:
        response_text = "What date would you like the appointment?"
    elif not session["time"]:
        response_text = "What time would you like the appointment?"
    else:
        booking_identity = caller_phone or stream_sid
        response_text = create_appointment(booking_identity, session["department"], session["date"], session["time"])
        _clear_session(stream_sid)

    try:
        await _speak(response_text, websocket, stream_sid, send_lock)
        if metrics.tts_first_audio_at is None:
            metrics.tts_first_audio_at = time.perf_counter()
        print(f"Latency booking response: {(time.perf_counter() - turn_started):.3f}s")
        metrics.turn_done_at = time.perf_counter()
        _log_turn_metrics(metrics)
    except asyncio.CancelledError:
        raise
    except Exception as err:
        print(f"TTS/send error: {err}")
        metrics.turn_done_at = time.perf_counter()
        _log_turn_metrics(metrics)



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
        state.last_speech_media_at = time.perf_counter()
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
                now = time.perf_counter()
                turn_metrics = _TurnMetrics(
                    stream_sid=state.stream_sid,
                    transcript=transcript,
                    user_speech_end_at=state.last_speech_media_at or now,
                    final_transcript_at=now,
                )
                state.active_response_task = asyncio.create_task(
                    process_turn(
                        transcript,
                        state.stream_sid,
                        state.caller_phone,
                        websocket,
                        send_lock,
                        turn_metrics,
                    )
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
        async with dg_client.listen.v2.connect(
            model=DEEPGRAM_MODEL,
            encoding="mulaw",
            sample_rate=8000,
        ) as connection:
            await _run_deepgram_twilio_bridge(connection, websocket)
    except Exception as error:
        print(f"WebSocket error: {error}")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)

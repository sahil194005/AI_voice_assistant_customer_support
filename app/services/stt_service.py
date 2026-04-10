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
from app.services.llm_service import extract_intent
from app.services.tts_service import stream_tts_ulaw_8k

_GREETING = "Hi there! How may I help you today?"

# Per-stream booking state keyed by stream_sid
_STREAM_SESSIONS: dict[str, "_BookingSession"] = {}


@dataclass(slots=True)
class _BookingSession:
    intent: str | None = None
    department: str | None = None
    date: str | None = None
    time: str | None = None


@dataclass(slots=True)
class _IntentData:
    intent: str | None
    department: str | None
    date: str | None
    time: str | None
    name: str | None


def _get_session(stream_sid: str) -> _BookingSession:
    try:
        if stream_sid not in _STREAM_SESSIONS:
            _STREAM_SESSIONS[stream_sid] = _BookingSession()
        return _STREAM_SESSIONS[stream_sid]
    except Exception as error:
        print(f"_get_session failed: {error}")
        raise


def _clear_session(stream_sid: str) -> None:
    try:
        if stream_sid in _STREAM_SESSIONS:
            del _STREAM_SESSIONS[stream_sid]
    except Exception as error:
        print(f"_clear_session failed: {error}")
        raise


def _build_deepgram_client() -> AsyncDeepgramClient:
    try:
        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is missing. Add it to your .env file.")
        return AsyncDeepgramClient(api_key=api_key)
    except Exception as error:
        print(f"_build_deepgram_client failed: {error}")
        raise


def _extract_transcript(message) -> str:
    try:
        transcript = getattr(message, "transcript", "")
        if isinstance(transcript, str):
            return transcript.strip()
        return ""
    except Exception as error:
        print(f"_extract_transcript failed: {error}")
        raise


def _is_final_turn(message) -> bool:
    try:
        return message.event == "EndOfTurn"
        # bool(message.is_final) or
    except Exception as error:
        print(f"_is_final_turn failed: {error}")
        raise


async def _extract_intent_with_timeout(
    transcript: str,
    available_departments: list[str],
    timeout_seconds: float = 20.2,
) -> _IntentData:
    try:
        payload = await asyncio.wait_for(
            extract_intent(transcript, available_departments),
            timeout=timeout_seconds,
        )
        return _IntentData(
            intent=payload["intent"],
            department=payload["department"],
            date=payload["date"],
            time=payload["time"],
            name=payload["name"],
        )
    except Exception as error:
        print(f"_extract_intent_with_timeout failed: {error}")
        raise


async def _send_twilio_media(websocket: WebSocket, stream_sid: str, audio_chunk: bytes, send_lock: asyncio.Lock) -> None:
    try:
        payload = base64.b64encode(audio_chunk).decode("ascii")
        message = {
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": payload},
        }
        async with send_lock:
            await websocket.send_text(json.dumps(message))
    except Exception as error:
        print(f"_send_twilio_media failed: {error}")
        raise


async def _speak(text: str, websocket: WebSocket, stream_sid: str, send_lock: asyncio.Lock) -> None:
    """Convert text to speech and stream audio to Twilio."""
    try:
        async for audio_chunk in stream_tts_ulaw_8k(text):
            await _send_twilio_media(websocket, stream_sid, audio_chunk, send_lock)
    except Exception as error:
        print(f"_speak failed: {error}")
        raise


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
    try:
        if start is None or end is None:
            return "NA"
        return f"{(end - start) * 1000:.0f}"
    except Exception as error:
        print(f"_fmt_ms failed: {error}")
        raise


def _log_turn_metrics(metrics: _TurnMetrics) -> None:
    try:
        turn_done = metrics.turn_done_at
        if turn_done is None:
            turn_done = time.perf_counter()

        stream_id = metrics.stream_sid
        if not stream_id:
            stream_id = "unknown"

        print(
            "LATENCY"
            f" stream={stream_id}"
            f" stt_final_ms={_fmt_ms(metrics.user_speech_end_at, metrics.final_transcript_at)}"
            f" intent_ms={_fmt_ms(metrics.intent_started_at, metrics.intent_done_at)}"
            f" llm_first_token_ms={_fmt_ms(metrics.final_transcript_at, metrics.llm_first_token_at)}"
            f" tts_first_audio_ms={_fmt_ms(metrics.final_transcript_at, metrics.tts_first_audio_at)}"
            f" end_to_end_ms={_fmt_ms(metrics.final_transcript_at, turn_done)}"
            f" text={metrics.transcript[:80]!r}"
        )
    except Exception as error:
        print(f"_log_turn_metrics failed: {error}")
        raise


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
    try:
        session = _get_session(stream_sid)
        turn_started = metrics.final_transcript_at
        available_departments = get_available_departments()

        metrics.intent_started_at = time.perf_counter()
        intent_data = await _extract_intent_with_timeout(transcript, available_departments)
        metrics.intent_done_at = time.perf_counter()
        extracted_intent = intent_data.intent
        non_booking_request = extracted_intent not in (None, "unknown", "book_appointment")

        # --- update session from extracted data ---
        if not session.intent and intent_data.intent and intent_data.intent != "unknown":
            session.intent = intent_data.intent

        if not session.department and intent_data.department:
            session.department = str(intent_data.department).lower()

        if not session.date and intent_data.date:
            session.date = str(intent_data.date)

        if not session.time and intent_data.time:
            session.time = str(intent_data.time)

        # This assistant only supports booking appointments for now.
        if session.intent != "book_appointment":
            session.intent = "book_appointment"

        # --- booking state machine ---
        response_prefix = ""
        if non_booking_request:
            response_prefix = "Right now I can only help with booking appointments. "

        if not session.department:
            if not available_departments:
                response_text = "Sorry, the clinic does not have appointment facilities configured yet."
            else:
                options = ", ".join(available_departments)
                response_text = f"Which department would you like? Available departments are {options}."
        elif session.department not in available_departments:
            session.department = None
            response_text = "Sorry, the clinic does not have the facility for that department yet."
        elif not session.date:
            response_text = "What date would you like the appointment?"
        elif not session.time:
            response_text = "What time would you like the appointment?"
        else:
            booking_identity = caller_phone
            if not booking_identity:
                raise ValueError("Missing caller_phone for appointment booking")
            response_text = create_appointment(booking_identity, session.department, session.date, session.time)
            _clear_session(stream_sid)

        response_text = f"{response_prefix}{response_text}" if response_prefix else response_text

        try:
            await _speak(response_text, websocket, stream_sid, send_lock)
            if metrics.tts_first_audio_at is None:
                metrics.tts_first_audio_at = time.perf_counter()
            print(f"Latency booking response: {(time.perf_counter() - turn_started):.3f}s")
        finally:
            metrics.turn_done_at = time.perf_counter()
            _log_turn_metrics(metrics)
    except Exception as error:
        print(f"process_turn failed: {error}")
        raise


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
    try:
        event = packet["event"]

        if event == "media":
            payload = packet["media"]["payload"]
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
            start_data = packet["start"]
            state.stream_sid = start_data["streamSid"]
            custom_parameters = start_data["customParameters"]
            state.caller_phone = custom_parameters["caller_phone"]
            if state.stream_sid:
                # Play a greeting immediately once Twilio stream is ready.
                if state.active_response_task and not state.active_response_task.done():
                    state.active_response_task.cancel()
                state.active_response_task = asyncio.create_task(
                    _speak(_GREETING, websocket, state.stream_sid, send_lock)
                )
            return True

        if event == "stop":
            await connection.send_close_stream(message="Stream stopped by Twilio")
            stt_dropped.set()
            return False

        if event:
            print(f"Unhandled Twilio event: {event}")

        return True
    except Exception as error:
        print(f"_handle_twilio_packet failed: {error}")
        raise


def _attach_deepgram_handlers(
    *,
    connection,
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    stt_dropped: asyncio.Event,
    state: _StreamState,
) -> None:
    async def _close_twilio(code: int) -> None:
        try:
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=code)
        except Exception as error:
            print(f"_close_twilio failed: {error}")
            raise

    def on_message(message) -> None:
        try:
            transcript = _extract_transcript(message)
            if not transcript:
                return

            if _is_final_turn(message):
                print(f"Final: {transcript}")
                if state.stream_sid:
                    if state.active_response_task and not state.active_response_task.done():
                        state.active_response_task.cancel()
                    now = time.perf_counter()
                    user_speech_end_at = state.last_speech_media_at
                    if user_speech_end_at is None:
                        user_speech_end_at = now
                    turn_metrics = _TurnMetrics(
                        stream_sid=state.stream_sid,
                        transcript=transcript,
                        user_speech_end_at=user_speech_end_at,
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
        except Exception as error:
            print(f"on_message failed: {error}")
            raise

    def on_close(_) -> None:
        try:
            stt_dropped.set()
            asyncio.create_task(_close_twilio(code=1011))
        except Exception as error:
            print(f"on_close failed: {error}")
            raise

    def on_error(error: Exception) -> None:
        try:
            stt_dropped.set()
            print(f"Deepgram error: {error}")
            asyncio.create_task(_close_twilio(code=1011))
        except Exception as caught_error:
            print(f"on_error failed: {caught_error}")
            raise

    try:
        connection.on(EventType.MESSAGE, on_message)
        connection.on(EventType.CLOSE, on_close)
        connection.on(EventType.ERROR, on_error)
    except Exception as error:
        print(f"_attach_deepgram_handlers failed: {error}")
        raise


async def _run_deepgram_twilio_bridge(connection, websocket: WebSocket) -> None:
    try:
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
        send_audio = connection.send_media

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
        finally:
            if state.active_response_task and not state.active_response_task.done():
                state.active_response_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await state.active_response_task

            dg_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dg_task

            _clear_session(state.stream_sid)
    except Exception as error:
        print(f"_run_deepgram_twilio_bridge failed: {error}")
        raise


async def handle_media_stream(websocket: WebSocket):
    """Bridge Twilio media-stream audio with Deepgram STT and voice responses."""
    try:
        await websocket.accept()
        dg_client = _build_deepgram_client()
        async with dg_client.listen.v2.connect(
            model=DEEPGRAM_MODEL,
            encoding="mulaw",
            sample_rate=8000,
            # eager_eot_threshold=0.5,
        ) as connection:
            await _run_deepgram_twilio_bridge(connection, websocket)
    except Exception as error:
        print(f"handle_media_stream failed: {error}")
        raise

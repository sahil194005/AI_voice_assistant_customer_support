import asyncio
import base64
import binascii
import contextlib
import json
import os

from deepgram import AsyncDeepgramClient
from deepgram.core import ApiError, EventType
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import DEEPGRAM_MODEL
from app.services.llm_service import stream_gwen_response
from app.services.tts_service import stream_tts_ulaw_8k


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


async def process_llm_and_speak(
    transcript: str,
    websocket: WebSocket,
    stream_sid: str,
    send_lock: asyncio.Lock,
) -> None:
    buffer = ""
    try:
        async for token in stream_gwen_response(transcript):
            buffer += token
            if not _flushable_text(buffer):
                continue

            text_chunk = buffer.strip()
            buffer = ""
            async for audio_chunk in stream_tts_ulaw_8k(text_chunk):
                await _send_twilio_media(websocket, stream_sid, audio_chunk, send_lock)

        remaining = buffer.strip()
        if remaining:
            async for audio_chunk in stream_tts_ulaw_8k(remaining):
                await _send_twilio_media(websocket, stream_sid, audio_chunk, send_lock)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"Pipeline error: {error}")


async def handle_media_stream(websocket: WebSocket):
    try:
        await websocket.accept()
        dg_client = _build_deepgram_client()

        async def _run_connection(connection) -> None:
            stt_dropped = asyncio.Event()
            send_lock = asyncio.Lock()
            stream_sid = ""
            active_response_task: asyncio.Task | None = None

            async def _close_twilio(code: int) -> None:
                with contextlib.suppress(Exception):
                    await websocket.close(code=code)

            def on_message(message) -> None:
                nonlocal active_response_task
                transcript = _extract_transcript(message)
                if not transcript:
                    return

                if _is_final_turn(message):
                    print(f"Final: {transcript}")
                    if stream_sid:
                        if active_response_task and not active_response_task.done():
                            active_response_task.cancel()
                        active_response_task = asyncio.create_task(
                            process_llm_and_speak(transcript, websocket, stream_sid, send_lock)
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

            dg_task = asyncio.create_task(connection.start_listening())
            send_audio = getattr(connection, "_send", connection.send_media)

            try:
                while not stt_dropped.is_set():
                    data = await websocket.receive_text()
                    try:
                        packet = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    event = packet.get("event")

                    if event == "media":
                        payload = packet.get("media", {}).get("payload")
                        if not payload:
                            continue
                        try:
                            audio_chunk = base64.b64decode(payload)
                        except (binascii.Error, ValueError):
                            continue
                        await send_audio(audio_chunk)
                    elif event == "start":
                        stream_sid = packet.get("start", {}).get("streamSid", "")
                    elif event == "stop":
                        with contextlib.suppress(Exception):
                            await connection.send_close_stream()
                        break

            except WebSocketDisconnect:
                pass
            except Exception:
                stt_dropped.set()
            finally:
                if active_response_task and not active_response_task.done():
                    active_response_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await active_response_task
                dg_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await dg_task

        try:
            async with dg_client.listen.v2.connect(
                model=DEEPGRAM_MODEL,
                encoding="mulaw",
                eot_threshold=300,
                sample_rate=8000,
            ) as connection:
                await _run_connection(connection)
        except ApiError as exc:
            if getattr(exc, "status_code", None) != 400:
                raise
            async with dg_client.listen.v2.connect(
                model=DEEPGRAM_MODEL,
                encoding="mulaw",
                sample_rate=8000,
            ) as connection:
                await _run_connection(connection)
    except Exception as error:
        print(f"WebSocket error: {error}")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)

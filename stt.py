import base64
import asyncio
import binascii
import contextlib
import json
import os
from deepgram import (
    AsyncDeepgramClient
)

from deepgram.core import ApiError, EventType
from fastapi import WebSocket, WebSocketDisconnect
from dotenv import load_dotenv

load_dotenv()


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


async def handle_media_stream(websocket: WebSocket):
    try:
        await websocket.accept()

        dg_client = _build_deepgram_client()

        async def _run_connection(connection) -> None:
            stt_dropped = asyncio.Event()

            async def _close_twilio(code: int) -> None:
                with contextlib.suppress(Exception):
                    await websocket.close(code=code)

            def on_message(message) -> None:
                transcript = _extract_transcript(message)
                if not transcript:
                    return

                if _is_final_turn(message):
                    # Final transcript hook for downstream LLM orchestration.
                    print(f"Final: {transcript}")
                    # asyncio.create_task(process_llm_and_speak(transcript, websocket))
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
                        # Decode Twilio's base64 audio payload
                        payload = packet.get("media", {}).get("payload")
                        if not payload:
                            continue
                        try:
                            audio_chunk = base64.b64decode(payload)
                        except (binascii.Error, ValueError):
                            continue
                        await send_audio(audio_chunk)

                    elif event == "stop":
                        with contextlib.suppress(Exception):
                            await connection.send_close_stream()
                        break

            except WebSocketDisconnect:
                pass
            except Exception:
                stt_dropped.set()
            finally:
                dg_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await dg_task

        model = os.getenv("DEEPGRAM_MODEL", "flux-general-en")
        try:
            async with dg_client.listen.v2.connect(
                model=model,
                encoding="mulaw",
                eot_threshold=300,
                sample_rate=8000,
            ) as connection:
                await _run_connection(connection)
        except ApiError as exc:
            if getattr(exc, "status_code", None) != 400:
                raise
            async with dg_client.listen.v2.connect(
                model=model,
                encoding="mulaw",
                sample_rate=8000,
            ) as connection:
                await _run_connection(connection)
    except Exception as e:
        print(f"WebSocket error: {e}")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)

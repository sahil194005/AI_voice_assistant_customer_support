import base64
import asyncio
import binascii
import contextlib
import json
import os
from deepgram import (
    DeepgramClient
)

from deepgram.core import EventType
from fastapi import WebSocket, WebSocketDisconnect
from dotenv import load_dotenv

load_dotenv()


def _build_deepgram_client() -> DeepgramClient:
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY is missing. Add it to your .env file.")
    return DeepgramClient(api_key=api_key)


def _extract_transcript(message) -> str:
    transcript = getattr(message, "transcript", "")
    if isinstance(transcript, str):
        return transcript.strip()
    return ""


def _is_final_turn(message) -> bool:
    return getattr(message, "event", None) == "EndOfTurn"


async def handle_media_stream(websocket: WebSocket):
    try:
        await websocket.accept()

        dg_client = _build_deepgram_client()

        # 1. Setup Deepgram Connection (using your sample's logic)
        # Twilio media stream is mulaw at 8kHz.
        async with dg_client.listen.v2.connect(
            model=os.getenv("DEEPGRAM_MODEL", "flux-general-en"),
            encoding="mulaw",
            eot_threshold=300,
            sample_rate="8000",
        ) as connection:

            def on_message(message) -> None:
                transcript = _extract_transcript(message)
                if not transcript:
                    return

                if _is_final_turn(message):
                    print(f"Patient (final): {transcript}")
                    # STEP 3 TRIGGER:
                    # asyncio.create_task(process_llm_and_speak(transcript, websocket))
                else:
                    print(f"Patient (interim): {transcript}")

            def on_error(error: Exception) -> None:
                print(f"Deepgram error: {error}")

            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, on_error)

            # Start Deepgram background task
            dg_task = asyncio.create_task(connection.start_listening())

            try:
                while True:
                    # Receive data from Twilio
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
                        await connection.send_media(audio_chunk)

                    elif event == "stop":
                        break

            except WebSocketDisconnect:
                print("Twilio websocket disconnected")
            except Exception as e:
                print(f"Stream error: {e}")
            finally:
                dg_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await dg_task
    except Exception as e:
        print(f"WebSocket error: {e}")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)

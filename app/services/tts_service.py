from typing import AsyncIterator
import os

from elevenlabs.client import AsyncElevenLabs

from app.core.config import ELEVENLABS_MODEL_ID

_ELEVEN_CLIENT: AsyncElevenLabs | None = None


def _get_eleven_client() -> AsyncElevenLabs:
    global _ELEVEN_CLIENT
    if _ELEVEN_CLIENT is None:
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is missing. Add it to your .env file.")
        _ELEVEN_CLIENT = AsyncElevenLabs(api_key=api_key)
    return _ELEVEN_CLIENT


async def stream_tts_ulaw_8k(text: str) -> AsyncIterator[bytes]:
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID is missing. Add it to your .env file.")

    stream = _get_eleven_client().text_to_speech.stream(
        voice_id=voice_id,
        text=text,
        model_id=ELEVENLABS_MODEL_ID,
        output_format="ulaw_8000",
        optimize_streaming_latency=4,
    )

    async for audio_chunk in stream:
        if audio_chunk:
            yield audio_chunk

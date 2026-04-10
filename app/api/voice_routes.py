from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import HTMLResponse

from app.services.stt_service import handle_media_stream

router = APIRouter()


@router.post("/voice")
async def reply(request: Request):
    try:
        form_data = await request.form()
        host = request.url.hostname
        caller_phone = str(form_data["From"])
        call_sid = str(form_data["CallSid"])
        response = f"""
    <Response>
        <Connect>
            <Stream url="wss://{host}/media-stream">
                <Parameter name="caller_phone" value="{caller_phone}" />
                <Parameter name="call_sid" value="{call_sid}" />
            </Stream>
        </Connect>
    </Response>
    """
        return HTMLResponse(content=response, media_type="application/xml")
    except Exception as error:
        print(f"reply failed: {error}")
        raise


@router.websocket("/media-stream")
async def speech_to_text_connection(websocket: WebSocket):
    try:
        await handle_media_stream(websocket)
    except Exception as error:
        print(f"speech_to_text_connection failed: {error}")
        raise



from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import HTMLResponse

from app.services.appointment_service import ALLOWED_DEPARTMENTS, create_appointment
from app.services.llm_service import extract_intent
from app.services.session_store import get_session, reset_session
from app.services.stt_service import handle_media_stream
from app.utils.text_parsing import normalize_department, parse_date

router = APIRouter()


@router.post("/voice")
async def reply(request: Request):
    form_data = await request.form()
    host = request.url.hostname
    caller_phone = str(form_data.get("From", "") or "")
    call_sid = str(form_data.get("CallSid", "") or "")
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


@router.websocket("/media-stream")
async def speech_to_text_connection(websocket: WebSocket):
    await handle_media_stream(websocket)


@router.post("/process-speech")
async def process_speech(request: Request):
    body = await request.body()
    data = parse_qs(body.decode())

    user_speech = data.get("SpeechResult", [""])[0]
    caller = data.get("From", [""])[0]

    print("User said:", user_speech)
    print("Caller ID:", caller)

    session = get_session(caller)
    intent_data = await extract_intent(user_speech)

    print("Extracted:", intent_data)

    if not session["intent"] and intent_data.get("intent"):
        session["intent"] = intent_data.get("intent")

    if not session["department"] and intent_data.get("department"):
        dept = intent_data.get("department") or normalize_department(user_speech)
        if dept:
            session["department"] = dept

    if not session["date"] and intent_data.get("date"):
        raw_date = intent_data.get("date")
        parsed_date = parse_date(raw_date)
        if parsed_date:
            session["date"] = parsed_date

    print("Session:", session)

    if session["intent"] != "book_appointment":
        return Response(
            content="""
            <Response>
                <Say>Sorry, I can only help with appointments right now.</Say>
            </Response>
            """,
            media_type="application/xml",
        )

    if not session["department"]:
        return Response(
            content="""
            <Response>
                <Say>Which department would you like to book?</Say>
                <Gather input="speech" action="/process-speech" method="POST"/>
            </Response>
            """,
            media_type="application/xml",
        )

    if not session["date"]:
        return Response(
            content="""
            <Response>
                <Say>For which date would you like the appointment?</Say>
                <Gather input="speech" action="/process-speech" method="POST"/>
            </Response>
            """,
            media_type="application/xml",
        )

    if session["department"] not in ALLOWED_DEPARTMENTS:
        session["department"] = None
        return Response(
            content="""
            <Response>
                <Say>Sorry, that department is not available.</Say>
            </Response>
            """,
            media_type="application/xml",
        )

    confirmation = create_appointment(caller, session["department"], session["date"])
    reset_session(caller)

    return Response(
        content=f"""
        <Response>
            <Say>{confirmation}</Say>
        </Response>
        """,
        media_type="application/xml",
    )

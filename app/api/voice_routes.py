from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import HTMLResponse

from app.services.appointment_service import create_appointment, get_available_departments
from app.services.llm_service import extract_intent
from app.services.session_store import get_session, reset_session
from app.services.stt_service import handle_media_stream

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
    available_departments = get_available_departments()
    intent_data = await extract_intent(user_speech, available_departments)

    print("Extracted:", intent_data)

    if not session["intent"] and intent_data.get("intent") and intent_data.get("intent") != "unknown":
        session["intent"] = intent_data.get("intent")

    if not session["department"] and intent_data.get("department"):
        session["department"] = intent_data.get("department")

    if not session["date"]:
        llm_date = intent_data.get("date")
        if llm_date:
            session["date"] = llm_date

    if not session["time"]:
        llm_time = intent_data.get("time")
        if llm_time:
            session["time"] = llm_time

    print("Session:", session)

    if session["intent"] and session["intent"] != "book_appointment":
        return Response(
            content="""
            <Response>
                <Say>Sorry, I can only help with appointments right now.</Say>
            </Response>
            """,
            media_type="application/xml",
        )

    if not session["department"]:
        if not available_departments:
            return Response(
                content="""
                <Response>
                    <Say>Sorry, the clinic does not have appointment facilities configured yet.</Say>
                </Response>
                """,
                media_type="application/xml",
            )

        department_options = ", ".join(available_departments)
        return Response(
            content=f"""
            <Response>
                <Say>Which department would you like to book? Available departments are {department_options}.</Say>
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

    if not session["time"]:
        return Response(
            content="""
            <Response>
                <Say>What time would you prefer for the appointment?</Say>
                <Gather input="speech" action="/process-speech" method="POST"/>
            </Response>
            """,
            media_type="application/xml",
        )

    if session["department"] not in available_departments:
        session["department"] = None
        if not available_departments:
            message = "Sorry, the clinic does not have appointment facilities configured yet."
        else:
            message = "Sorry, the clinic does not have the facility for that department yet."
        return Response(
            content=f"""
            <Response>
                <Say>{message}</Say>
            </Response>
            """,
            media_type="application/xml",
        )

    confirmation = create_appointment(
        caller,
        session["department"],
        session["date"],
        session["time"],
    )
    reset_session(caller)

    return Response(
        content=f"""
        <Response>
            <Say>{confirmation}</Say>
        </Response>
        """,
        media_type="application/xml",
    )

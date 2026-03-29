import json

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, Response
from urllib.parse import parse_qs
from llm import extract_intent
from booking import create_appointment
from state import get_session, reset_session
from utils import normalize_department
from utils import parse_date
from dotenv import load_dotenv
from stt import handle_media_stream

app = FastAPI()


@app.post("/voice")
async def reply(request: Request):
    """Initial Twilio Webhook to start the Media Stream"""
    host = request.url.hostname
    response = f"""
    <Response>
        <Connect>
            <Stream url="wss://{host}/media-stream" />
        </Connect>
    </Response>
    """
    return HTMLResponse(content=response, media_type="application/xml")


@app.websocket("/media-stream")
async def speech_to_text_connection(websocket: WebSocket):
    await handle_media_stream(websocket)


@app.post("/process-speech")
async def process_speech(request: Request):
    body = await request.body()
    data = parse_qs(body.decode())

    user_speech = data.get("SpeechResult", [""])[0]
    caller = data.get("From", [""])[0]

    print("User said:", user_speech)
    print("Caller ID:", caller)

    session = get_session(caller)

    # 🔹 Step 1: Extract structured data
    intent_data = await extract_intent(user_speech)

    print("Extracted:", intent_data)

    # 🔹 Step 2: Fill session safely
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

    # 🔹 Step 3: Validate intent
    if session["intent"] != "book_appointment":
        return Response(
            content="""
            <Response>
                <Say>Sorry, I can only help with appointments right now.</Say>
            </Response>
            """,
            media_type="application/xml",
        )

    # 🔹 Step 4: Ask missing info

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
    if session["department"] not in ["cardiology", "dermatology", "general"]:
        session["department"] = None
        return Response(
            content="""
            <Response>
                <Say>Sorry, that department is not available.</Say>
            </Response>
            """,
            media_type="application/xml",
        )
    # 🔹 Step 5: Book appointment
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)

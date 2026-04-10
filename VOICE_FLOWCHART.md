# Voice Call Execution Flowchart

This document describes the function execution order for customer calls.

## Main live call flow (/voice -> /media-stream)

```mermaid
---
id: e8f5431a-fea2-4bc6-8405-a48a6c79ff27
---
flowchart TD
  A[POST /voice<br/>Twilio voice webhook entry] --> B[reply function<br/>Build TwiML stream response]
  B --> C[Return TwiML<br/>Connect Stream to /media-stream]
  C --> D[Twilio opens /media-stream<br/>Starts bidirectional audio stream]
  D --> E[speech_to_text_connection function<br/>Route-level WebSocket handler]
  E --> F[handle_media_stream function<br/>Accept socket and setup STT bridge]

  F --> G[websocket.accept<br/>Accept WebSocket session]
  G --> H[_build_deepgram_client<br/>Create Deepgram client from API key]
  H --> I[Open Deepgram live connection<br/>mulaw, 8k, configured model]
  I --> J[_run_deepgram_twilio_bridge<br/>Main packet-processing loop]

  J --> K[_attach_deepgram_handlers<br/>Register MESSAGE/CLOSE/ERROR callbacks]
  J --> L[while loop<br/>Read Twilio packets via receive_text]

  L --> M{Twilio event type?<br/>start, media, or stop}
  M -->|start| N[_handle_twilio_packet start branch<br/>Save stream metadata and greet caller]
  M -->|media| O[_handle_twilio_packet media branch<br/>Decode base64 audio and send to Deepgram]
  M -->|stop| P[_handle_twilio_packet stop branch<br/>Close stream and break loop]

  O --> Q[on_message callback<br/>Receive transcript events from Deepgram]
  Q --> R{Final turn?<br/>is_final or EndOfTurn}
  R -->|no| S[Interim branch<br/>Log partial transcript only]
  R -->|yes| T[process_turn function<br/>Per-utterance booking logic]

  T --> U[_extract_intent_with_timeout<br/>Call extract_intent safely]
  U --> V[Slot update<br/>Fill intent/department/date/time in session]
  V --> W[Booking-only guard<br/>Force booking flow for non-booking intents]
  W --> X{Required slots complete?<br/>department, date, time}
  X -->|missing department| Y[Compose prompt<br/>Ask for department]
  X -->|missing date| Z[Compose prompt<br/>Ask for date]
  X -->|missing time| AA[Compose prompt<br/>Ask for time]
  X -->|all present| AB[create_appointment + _clear_session<br/>Finalize and reset state]

  Y --> AC[_speak -> stream_tts_ulaw_8k -> _send_twilio_media<br/>Generate and stream bot audio]
  Z --> AC
  AA --> AC
  AB --> AC

  AC --> AD[Twilio playback<br/>Caller hears response audio]
  AD --> L
```

## Function roles (quick reference)

1. reply(request): Twilio voice webhook entrypoint, returns TwiML that starts media streaming.
2. speech_to_text_connection(websocket): WebSocket route wrapper that calls the STT bridge.
3. handle_media_stream(websocket): Accepts WebSocket, opens Deepgram connection, starts runtime loop.
4. _run_deepgram_twilio_bridge(...): Core loop handling Twilio packet intake and stream lifecycle.
5. _attach_deepgram_handlers(...): Registers transcript and stream error/close callbacks.
6. _handle_twilio_packet(...): Handles start/media/stop packets and state updates.
7. process_turn(...): Booking conversation state machine for each final user utterance.
8. _extract_intent_with_timeout(...): Intent/entity extraction with timeout and fallback.
9. _speak(...): Converts response text to audio and sends it back to Twilio.
10. create_appointment(...): Creates final appointment once all required slots are available.
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/customer_support_db")
LLM_PROVIDER = os.getenv("LLM_PROVIDER")
LLM_SYSTEM_PROMPT = os.getenv("LLM_SYSTEM_PROMPT")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")
OLLAMA_URL = os.getenv("OLLAMA_URL")
OLLAMA_SYSTEM_PROMPT = os.getenv(
    "OLLAMA_SYSTEM_PROMPT",
    LLM_SYSTEM_PROMPT,
)
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "flux-general-en")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

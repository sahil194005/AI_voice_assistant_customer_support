import json
from ollama import AsyncClient

# No need for OLLAMA_URL if using AsyncClient, it defaults to localhost:11434
PROMPT_TEMPLATE = """
Extract structured information from user input.

Return ONLY JSON.

Fields:
- intent: (book_appointment | check_availability | unknown)
- department: (cardiology, dermatology, general, null)
- date: (today, tomorrow, monday, tuesday, etc. or null)
- name: (if mentioned, else null)

If not mentioned, return null.

User input: "{input}"
"""


async def extract_intent(user_input: str):
    prompt = PROMPT_TEMPLATE.format(input=user_input)

    try:
        # Use qwen2.5 for its excellent JSON following capabilities
        response = await AsyncClient().chat(
            model="qwen2.5",
            messages=[
                {"role": "user", "content": prompt},
            ],
            # format='json' ensures Qwen strictly outputs a JSON object
            format="json",
        )

        # The content is already a string inside the message
        raw_content = response["message"]["content"]
        print("LLM raw response:", raw_content)

        return json.loads(raw_content)

    except Exception as e:
        print(f"Error parsing LLM response: {e}")
        return {
            "intent": "unknown",
            "department": "unknown",
            "date": None,
            "name": None,
        }


# Example usage:
# result = await extract_intent("I want to book a cardiology appointment for tomorrow, my name is John.")

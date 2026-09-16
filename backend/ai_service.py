import os
import json
from pathlib import Path
from dotenv import load_dotenv
from google import genai

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


GEMINI_MODEL = "gemini-3.6-flash"


SYSTEM_PROMPT = """
You are the AI Route and Risk Analyst for the NE-SLAI
Smart Logistics and Accessibility Intelligence Platform.

Analyze only the route, risk, accessibility and incident
information provided by the backend.

Do not invent:
- roads
- incidents
- distances
- travel times
- weather
- risk values
- locations

Do not calculate a new route.

Explain:
1. Current route condition
2. Main risks
3. Active incidents
4. Why the route has its risk score
5. Comparison with alternative routes
6. Practical logistics considerations

Keep the answer concise and suitable for a logistics dashboard.

Use simple language that a logistics operator can understand.
"""


def analyze_route(route_data):

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set."
        )

    client = genai.Client(
        api_key=api_key
    )

    prompt = f"""
{SYSTEM_PROMPT}

Here is the current NE-SLAI route data:

{json.dumps(route_data, separators=(",", ":"))}

Provide a concise route risk analysis.
"""

    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=prompt
    )

    return interaction.output_text
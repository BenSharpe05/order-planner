"""
agent.py - the agent loop: where the AI model comes in.

The loop:
  1. Send the model the email, the rules, and the descriptions of the tools.
  2. If the model asks for a tool, run it (tools.py) and send back the result.
  3. Repeat until the model gives its final answer, or the step limit is hit.
  4. Re-check the final plan with validate_plan ourselves, so every number
     shown to a colleague comes from code, not from the model.

run_agent() "yields" one event per step, so the screen (app.py) can show
each step live as it happens.

Run from the terminal:
  python agent.py --check   -> test the API key and list available models
  python agent.py A         -> run email A (or B, or C) and print each step
"""

import json
import os
import re
import sys
import time
import tomllib

from openai import OpenAI, RateLimitError

from data import DEMO_NOW, EMAILS, LOCATIONS
from tools import TOOL_SCHEMAS, run_tool, validate_plan

# Gemini's OpenAI-compatible address. Changing provider (Groq, OpenAI, Azure)
# means changing this address, the key and the model name, nothing else.
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.5-flash"

MAX_STEPS = 8         # safety limit on rounds with the model, so it can't loop forever
MAX_TOOL_CALLS = 30   # backstop on the total number of tools run
MAX_RATE_LIMIT_RETRIES = 2   # free tier allows only a few requests a minute


SYSTEM_PROMPT = f"""You are Order Planner, an assistant to a colleague on the trade desk of a
Travis Perkins branch in Glasgow. You turn a contractor's email into a delivery plan
for the colleague to approve. You never contact the customer yourself.

The current time is {DEMO_NOW:%A %d %B %Y, %H:%M}.
Locations: {", ".join(f"{code} = {name}" for code, name in LOCATIONS.items())}.

How to work:
1. Read the email. Work out each item and quantity, any deadline, any budget, and
   what matters most to the customer.
2. Find each item with search_catalog, then check it with get_stock_and_delivery.
3. Build the cheapest plan that meets every requirement. You may split an order
   across locations and delivery services. Each delivery service used is charged
   once, however many items it carries.
4. Call validate_plan with your plan. Give deadlines as 'YYYY-MM-DD HH:MM', and
   pass the customer's delivery limit as max_delivery_charge.
5. If validation fails, revise and validate again. If no plan can meet every
   requirement, do not pretend otherwise: give two or three alternatives, each
   validated, for the colleague to choose between.

Rules:
- Every fact and number must come from a tool. Never invent products, stock,
  prices or times, and never do your own sums for totals: use validate_plan.
- If something is unclear, make a sensible assumption and state it, rather than
  stopping. Examples: how many of an item, or whether the site has power.
- Before each tool call, say in one short sentence what you are checking and why.
- Work in as few steps as possible: when you need several look-ups, request them
  all together in one step (for example, search for every item at once, then check
  stock for every chosen SKU at once).

When you are finished, reply with ONLY a JSON object, no other text:
{{
  "status": "plan_proposed" or "needs_decision",
  "understanding": "one or two sentences on what the customer needs and what matters most",
  "assumptions": ["each assumption you made"],
  "plan": {{"lines": [...], "max_delivery_charge": number or null}} or null,
  "options": [{{"label": "short name", "summary": "one sentence trade-off",
               "lines": [...], "max_delivery_charge": number or null}}],
  "reply_draft": "a short, friendly email to the customer, signed 'Travis Perkins Glasgow'"
}}
Use the same "lines" format as validate_plan. For "plan_proposed", fill "plan" and
leave "options" empty. For "needs_decision", set "plan" to null, fill "options", and
write the reply as a holding message saying a colleague will call to agree the option.
"""


def get_api_key():
    """Read the key from an environment variable, or else from .streamlit/secrets.toml."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        with open(".streamlit/secrets.toml", "rb") as file:
            return tomllib.load(file).get("GEMINI_API_KEY")
    except FileNotFoundError:
        return None


def make_client():
    key = get_api_key()
    if not key:
        raise RuntimeError("No GEMINI_API_KEY found. Add it to .streamlit/secrets.toml.")
    return OpenAI(api_key=key, base_url=BASE_URL)


def email_as_text(email):
    return (
        f"From: {email['sender']}\n"
        f"Received: {email['received']:%a %d %b %H:%M}\n"
        f"Subject: {email['subject']}\n\n"
        f"{email['body']}"
    )


def parse_final_answer(text):
    """Pull the JSON object out of the model's final message."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def check_final_answer(answer):
    """Re-run validate_plan on whatever the model proposed.

    The screen shows these results, never totals typed by the model.
    """
    if answer.get("plan"):
        plan = answer["plan"]
        answer["plan_check"] = validate_plan(plan.get("lines", []), plan.get("max_delivery_charge"))
    for option in answer.get("options") or []:
        option["check"] = validate_plan(option.get("lines", []), option.get("max_delivery_charge"))
    return answer


def run_agent(email, client=None, model=None):
    """Handle one email. Yields events: thought, tool_call, tool_result, final, error."""
    client = client or make_client()
    model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": email_as_text(email)},
    ]
    tool_calls_made = 0
    format_retries = 0

    rate_limit_retries = 0
    steps = 0

    while True:
        if steps >= MAX_STEPS:
            yield {"type": "error", "message": f"Stopped after {MAX_STEPS} steps without a final answer. Escalate to a colleague."}
            return
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, tools=TOOL_SCHEMAS
            )
        except RateLimitError as error:
            # Too many requests this minute: wait as long as the service asks, then retry.
            if rate_limit_retries >= MAX_RATE_LIMIT_RETRIES:
                yield {"type": "error", "message": "The AI service is still busy after waiting. Try again in a minute."}
                return
            rate_limit_retries += 1
            found = re.search(r"retry in ([\d.]+)s", str(error))
            wait = min(float(found.group(1)) + 1 if found else 30, 65)
            yield {"type": "waiting", "seconds": round(wait)}
            time.sleep(wait)
            continue
        except Exception as error:
            yield {"type": "error", "message": f"The AI service returned an error: {error}"}
            return

        steps += 1
        message = response.choices[0].message
        # Keep the model's message exactly as returned (Gemini attaches hidden
        # fields to tool calls that must be sent back unchanged).
        messages.append(message.model_dump(exclude_none=True))

        if message.content and message.tool_calls:
            yield {"type": "thought", "text": message.content.strip()}

        # --- The model asked for one or more tools ---
        if message.tool_calls:
            for call in message.tool_calls:
                tool_calls_made += 1
                if tool_calls_made > MAX_TOOL_CALLS:
                    yield {"type": "error", "message": f"Stopped after {MAX_TOOL_CALLS} tool calls. Escalate to a colleague."}
                    return
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                yield {"type": "tool_call", "name": call.function.name, "arguments": arguments}

                result = run_tool(call.function.name, arguments)
                yield {"type": "tool_result", "name": call.function.name, "result": result}

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            continue

        # --- No tool requested: this should be the final answer ---
        try:
            answer = parse_final_answer(message.content or "")
        except (ValueError, json.JSONDecodeError):
            if format_retries == 0:
                format_retries += 1
                messages.append({"role": "user", "content": "Reply with ONLY the JSON object described in your instructions."})
                continue
            yield {"type": "error", "message": "The AI did not return a valid final answer. Escalate to a colleague."}
            return

        yield {"type": "final", "answer": check_final_answer(answer), "tool_calls": tool_calls_made, "steps": steps}
        return


# ---------------------------------------------------------------------------
# Terminal mode, for testing without the screen
# ---------------------------------------------------------------------------
def print_event(event):
    kind = event["type"]
    if kind == "thought":
        print(f"\n[thinking] {event['text']}")
    elif kind == "tool_call":
        print(f"\n[tool call] {event['name']}({json.dumps(event['arguments'], ensure_ascii=False)})")
    elif kind == "tool_result":
        summary = json.dumps(event["result"], ensure_ascii=False)
        print(f"[result]    {summary[:300]}{'...' if len(summary) > 300 else ''}")
    elif kind == "final":
        print("\n[final answer]")
        print(json.dumps(event["answer"], indent=2, ensure_ascii=False))
    elif kind == "waiting":
        print(f"\n[waiting] Free-tier request limit reached; waiting {event['seconds']}s, then continuing...")
    elif kind == "error":
        print(f"\n[error] {event['message']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py --check   or   python agent.py A")
        sys.exit(1)

    if sys.argv[1] == "--check":
        client = make_client()
        names = sorted(m.id for m in client.models.list())
        print("Key works. Flash models available:")
        for name in names:
            if "flash" in name:
                print("  ", name)
        sys.exit(0)

    chosen = sys.argv[1].upper()
    email = next((e for e in EMAILS if e["id"] == chosen), None)
    if email is None:
        print("Choose A, B or C.")
        sys.exit(1)

    print(email_as_text(email))
    print("-" * 60)
    for event in run_agent(email):
        print_event(event)
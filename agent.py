"""
agent.py - the agent loop: where the AI model comes in.

The loop:
  1. Send the model the email, the rules, and the descriptions of the tools.
  2. If the model asks for a tool, run it (tools.py) and send back the result.
  3. Repeat until the model gives its final answer, or the step limit is hit.
  4. Re-check the final plan with validate_plan ourselves, so every number
     shown to a colleague comes from code, not from the model.

If a model is busy or unavailable, the run restarts on the next model in MODELS.

run_agent() "yields" one event per step, so the screen (app.py) can show
each step live as it happens.

Run from the terminal:
  python agent.py --check     -> test the key and that each model responds
  python agent.py A           -> run email A (or B, or C) and print each step
  python agent.py A --save    -> the same, and save a successful run for replay mode
"""

import json
import os
import sys
import tomllib
from datetime import datetime

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from data import DEMO_NOW, EMAILS, LOCATIONS
from tools import TOOL_SCHEMAS, run_tool, validate_plan

# The model provider. Gemini is reached through its OpenAI-compatible address, so
# moving to another provider (for example Azure OpenAI, if that's what a client
# has approved) means changing these three settings, not the code.
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
KEY_NAME = "GEMINI_API_KEY"
MODELS = ["gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash"]   # tried in this order

MAX_STEPS = 8          # safety limit on rounds with the model, so it can't loop forever
MAX_TOOL_CALLS = 30    # backstop on the total number of tools run
REQUEST_TIMEOUT = 90   # seconds; the library's default is 10 minutes, far too long for a demo


SYSTEM_PROMPT = f"""You are Order Planner, an assistant to a colleague on the trade desk of a
Travis Perkins branch. You turn a contractor's email into a delivery plan
for the colleague to approve. You never contact the customer yourself.

The current time is {DEMO_NOW:%A %d %B %Y, %H:%M}.
Locations: {", ".join(f"{code} = {name}" for code, name in LOCATIONS.items())}.

How to work:
1. Read the email. Work out each item and quantity, any deadline, any budget, and
   what matters most to the customer.
2. Find every item with search_catalog, then check every chosen SKU with
   get_stock_and_delivery.
3. Before building a plan, check the facts: is there enough stock in total, and
   which delivery services arrive in time and at what charge? Each delivery
   service used is charged once, however many items it carries.
4. Build the cheapest plan that meets every requirement. You may split an order
   across locations and delivery services.
5. Call validate_plan with your plan. Give deadlines as 'YYYY-MM-DD HH:MM', and
   pass the customer's delivery limit as max_delivery_charge.
6. If validation fails, fix the specific problems it lists and validate again.

When the request cannot be met in full:
- If the facts in step 3 show that no plan can meet every requirement, or two
  attempts fail for the same reason, stop trying: the request cannot be met.
- Do not pretend otherwise. Offer two or three options, each relaxing ONE
  requirement (fewer items, a later time, or a higher delivery charge). Validate
  each option with the requirement it relaxes changed, so it passes, and say in
  its summary exactly what it gives up.
- Make each option the best way to relax that one requirement: consider every
  suitable product and location, and put items on as few delivery services as
  possible to keep charges down.

Rules:
- Every fact and number must come from a tool. Never invent products, stock,
  prices, times or terms (such as VAT, discounts or guarantees), and never do your
  own sums for totals: use validate_plan.
- If something is unclear, make a sensible assumption and state it, rather than
  stopping. Examples: how many of an item, which product best fits a vague request,
  or what time "morning" means. Assumed quantities must be practical for the job described.
- Flag any product-fit assumption a colleague should check, for example a different
  size or type from the one asked for.
- Before each tool call, say in one short sentence what you are checking and why.
- Work in as few steps as possible: when you need several look-ups, request them
  all together in one step.

When you are finished, reply with ONLY a JSON object, no other text:
{{
  "status": "plan_proposed" or "needs_decision",
  "understanding": "one or two sentences on what the customer needs and what matters most",
  "assumptions": ["each assumption you made"],
  "plan": {{"lines": [...], "max_delivery_charge": number or null}} or null,
  "options": [{{"label": "short name", "summary": "one sentence on what it gives up",
               "lines": [...], "max_delivery_charge": number or null}}],
  "reply_draft": "a short, friendly email to the customer, signed 'The trade desk team'"
}}
Use the same "lines" format as validate_plan. For "plan_proposed", fill "plan" and
leave "options" empty. For "needs_decision", set "plan" to null, fill "options", and
write the reply as a holding message saying a colleague will call to agree the option.
The reply may only state facts and figures that came from the tools.
"""


# ---------------------------------------------------------------------------
# Settings and connection
# ---------------------------------------------------------------------------
def get_setting(name):
    """Read a setting from an environment variable, or else from .streamlit/secrets.toml."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        with open(".streamlit/secrets.toml", "rb") as file:
            return tomllib.load(file).get(name)
    except FileNotFoundError:
        return None


def make_client():
    key = get_setting(KEY_NAME)
    if not key:
        raise RuntimeError(f"No {KEY_NAME} found. Add it to .streamlit/secrets.toml.")
    # max_retries=0: the library would otherwise retry quietly in the background.
    # Our own code decides what happens next: move on to the next model.
    return OpenAI(api_key=key, base_url=BASE_URL, timeout=REQUEST_TIMEOUT, max_retries=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
def run_agent(email, client=None, models=None):
    """Handle one email. Yields events: start, thought, tool_call, tool_result,
    switch_model, final, error.

    Tries each model in turn; moves to the next only if one is busy or unavailable.
    """
    try:
        client = client or make_client()
    except RuntimeError as error:          # e.g. no API key configured
        yield {"type": "error", "message": str(error)}
        return
    models = models or MODELS

    for index, model in enumerate(models):
        yield {"type": "start", "model": model}
        # "yield from" passes this model's events straight through, then gives
        # back how the run ended: "done", or "unavailable" with the reason.
        outcome, reason = yield from run_with_model(email, client, model)
        if outcome == "done":
            return
        if index + 1 < len(models):
            yield {"type": "switch_model", "from": model, "to": models[index + 1], "reason": reason}
        else:
            yield {"type": "error", "message": "Every model is busy right now. Use replay mode, "
                                               f"or try again in a minute. Last message: {reason}"}


def run_with_model(email, client, model):
    """One attempt with one model. Returns ("done", None) or ("unavailable", reason)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": email_as_text(email)},
    ]
    tool_calls_made = 0
    format_retries = 0
    steps = 0

    while True:
        if steps >= MAX_STEPS:
            yield {"type": "error", "message": f"Stopped after {MAX_STEPS} steps without a final answer. Escalate to a colleague."}
            return "done", None
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, tools=TOOL_SCHEMAS
            )
        except (RateLimitError, APIConnectionError) as error:   # over its limit, timed out, or unreachable
            return "unavailable", str(error)[:300]
        except APIStatusError as error:
            if error.status_code >= 500:                         # the provider's servers are overloaded
                return "unavailable", str(error)[:300]
            yield {"type": "error", "message": f"The AI service returned an error: {error}"}
            return "done", None

        steps += 1
        message = response.choices[0].message
        # Send back only the standard fields. Tool calls are kept whole, because
        # Gemini attaches hidden fields to them that must be returned unchanged.
        full = message.model_dump(exclude_none=True)
        messages.append({key: full[key] for key in ("role", "content", "tool_calls") if key in full})

        if message.content and message.tool_calls:
            yield {"type": "thought", "text": message.content.strip()}

        # --- The model asked for one or more tools ---
        if message.tool_calls:
            for call in message.tool_calls:
                tool_calls_made += 1
                if tool_calls_made > MAX_TOOL_CALLS:
                    yield {"type": "error", "message": f"Stopped after {MAX_TOOL_CALLS} tool calls. Escalate to a colleague."}
                    return "done", None
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
                    "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
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
            return "done", None

        yield {"type": "final", "answer": check_final_answer(answer),
               "model": model, "tool_calls": tool_calls_made, "steps": steps}
        return "done", None


# ---------------------------------------------------------------------------
# Recordings for replay mode
# ---------------------------------------------------------------------------
def save_recording(email_id, events):
    """Save a successful run, so the screen can replay it without calling the AI."""
    os.makedirs("recordings", exist_ok=True)
    final = next(e for e in events if e["type"] == "final")
    recording = {
        "email_id": email_id,
        "model": final["model"],
        "recorded_at": datetime.now().strftime("%a %d %b %Y, %H:%M"),
        "events": [e for e in events if e["type"] not in ("switch_model", "start")],
    }
    path = f"recordings/{email_id}.json"
    with open(path, "w", encoding="utf-8") as file:
        json.dump(recording, file, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# Terminal mode, for testing without the screen
# ---------------------------------------------------------------------------
def print_event(event):
    kind = event["type"]
    if kind == "start":
        print(f"\n[model] {event['model']}")
    elif kind == "thought":
        print(f"\n[thinking] {event['text']}")
    elif kind == "tool_call":
        print(f"\n[tool call] {event['name']}({json.dumps(event['arguments'], ensure_ascii=False)})")
    elif kind == "tool_result":
        summary = json.dumps(event["result"], ensure_ascii=False)
        print(f"[result]    {summary[:300]}{'...' if len(summary) > 300 else ''}")
    elif kind == "final":
        print(f"\n[final answer] ({event['steps']} steps, {event['tool_calls']} tool calls, {event['model']})")
        print(json.dumps(event["answer"], indent=2, ensure_ascii=False))
    elif kind == "switch_model":
        print(f"\n[switching] {event['from']} is busy; restarting on {event['to']}.")
        print(f"            Service said: {event['reason']}")
    elif kind == "error":
        print(f"\n[error] {event['message']}")


def check_models():
    """Send each model a tiny request, to check the key works and the model responds."""
    client = make_client()
    for model in MODELS:
        print(f"  {model:<20} ", end="", flush=True)   # result is printed on the same line
        try:
            client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Reply OK"}], max_tokens=5)
            print("OK")
        except RateLimitError as error:
            print(f"over its limit: {str(error)[:150]}")
        except APIConnectionError:
            print(f"no reply within {REQUEST_TIMEOUT}s")
        except APIStatusError as error:
            print(f"unavailable ({error.status_code}): {str(error)[:120]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py --check   or   python agent.py A [--save]")
        sys.exit(1)

    if sys.argv[1] == "--check":
        check_models()
        sys.exit(0)

    chosen = sys.argv[1].upper()
    email = next((e for e in EMAILS if e["id"] == chosen), None)
    if email is None:
        print("Choose A, B or C.")
        sys.exit(1)

    print(f"Models, in fallback order: {', '.join(MODELS)}")
    print(email_as_text(email))
    print("-" * 60)

    events = []
    for event in run_agent(email):
        print_event(event)
        events.append(event)

    if "--save" in sys.argv:
        if any(e["type"] == "final" for e in events):
            print(f"\nSaved for replay: {save_recording(chosen, events)}")
        else:
            print("\nNot saved: the run did not finish with a final answer.")
"""
agent.py - the agent loop: where the AI model comes in.

The loop:
  1. Send the model the email, the rules, and the descriptions of the tools.
  2. If the model asks for a tool, run it (tools.py) and send back the result.
  3. Repeat until the model gives its final answer, or the step limit is hit.
  4. Re-check the final plan with validate_plan ourselves, so every number
     shown to a colleague comes from code, not from the model.

If a model is busy or over its free-tier limit, the run restarts on the next
model in the "route": every model of every provider that has a key, in order.

run_agent() "yields" one event per step, so the screen (app.py) can show
each step live as it happens.

Run from the terminal:
  python agent.py --check     -> test the key, and whether each model has free capacity
  python agent.py A           -> run email A (or B, or C) and print each step
  python agent.py A --save    -> the same, and save a successful run for replay mode
"""

import json
import os
import re
import sys
import time
import tomllib
from datetime import datetime

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from data import DEMO_NOW, EMAILS, LOCATIONS
from tools import TOOL_SCHEMAS, run_tool, validate_plan

# Every provider here speaks the OpenAI format, so adding or switching one is
# configuration only. A provider is used only if its key is in .streamlit/secrets.toml.
# Order: LLM_PROVIDERS in secrets (e.g. "groq, gemini"), else DEFAULT_ORDER.
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_name": "GEMINI_API_KEY",
        "models": ["gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash"],
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_name": "GROQ_API_KEY",
        "models": ["openai/gpt-oss-120b"],
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_name": "OPENROUTER_API_KEY",
        "models": [],   # none by default: set OPENROUTER_MODELS in secrets to use it
    },
}
DEFAULT_ORDER = ["gemini", "groq", "openrouter"]

MAX_STEPS = 8          # safety limit on rounds with the model, so it can't loop forever
MAX_TOOL_CALLS = 30    # backstop on the total number of tools run
MAX_SHORT_WAIT = 15    # wait up to this many seconds for a busy model; longer -> next model
MAX_WAITS_PER_MODEL = 2
REQUEST_TIMEOUT = 90   # seconds; long enough for a reasoning step, far shorter than the library's 10-minute default


SYSTEM_PROMPT = f"""You are Order Planner, an assistant to a colleague on the trade desk of a
Travis Perkins branch in Glasgow. You turn a contractor's email into a delivery plan
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
  stopping. Examples: how many of an item, whether the site has power, or what
  time "morning" means. Assumed quantities must be practical for the job described.
- Flag any product-fit assumption a colleague should check, for example a product
  that needs mains power or a different size from the one asked for.
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
  "reply_draft": "a short, friendly email to the customer, signed 'Travis Perkins Glasgow'"
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


def provider_order():
    """Provider names in the order to try them."""
    setting = get_setting("LLM_PROVIDERS")
    names = [n.strip().lower() for n in setting.split(",")] if setting else DEFAULT_ORDER
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise RuntimeError(f"Unknown provider(s) {unknown}. Choose from: {', '.join(PROVIDERS)}.")
    return names


def make_client(provider_name):
    provider = PROVIDERS[provider_name]
    # max_retries=0: the library would otherwise retry quietly in the background.
    # Our own code decides when to wait, retry or move to the next model.
    return OpenAI(api_key=get_setting(provider["key_name"]), base_url=provider["base_url"],
                  timeout=REQUEST_TIMEOUT, max_retries=0)


def models_for(provider_name):
    """A provider's models: from e.g. OPENROUTER_MODELS in secrets if set, else the defaults."""
    setting = get_setting(f"{provider_name.upper()}_MODELS")
    if setting:
        return [m.strip() for m in setting.split(",") if m.strip()]
    return PROVIDERS[provider_name]["models"]


def build_route():
    """Every (provider, client, model) to try, in order, skipping providers with no key."""
    route = []
    for name in provider_order():
        if not get_setting(PROVIDERS[name]["key_name"]):
            continue
        client = make_client(name)
        for model in models_for(name):
            route.append((name, client, model))
    if not route:
        raise RuntimeError("No API keys found. Add at least one to .streamlit/secrets.toml.")
    return route


def describe_route(route):
    return ", ".join(f"{name}/{model}" for name, _, model in route)


def retry_wait_seconds(error):
    """How long the service asked us to wait. Gemini says 'retry in 54.9s', Groq 'try again in 7.5s'."""
    found = re.search(r"(?:retry|try again) in ((?:\d+m)?[\d.]+)(ms|s)", str(error))
    if not found:
        return 60   # no time given: usually a daily limit, so don't wait for it
    amount, unit = found.groups()
    minutes = 0
    if "m" in amount:
        minutes_text, amount = amount.split("m")
        minutes = int(minutes_text)
    seconds = minutes * 60 + float(amount) / (1000 if unit == "ms" else 1)
    return seconds + 1


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
def run_agent(email, route=None):
    """Handle one email. Yields events: start, thought, tool_call, tool_result,
    waiting, switch_model, final, error.

    route: a list of (provider name, client, model) to try in order. It moves to
    the next only if one is busy or over its limit.
    """
    route = route or build_route()

    for index, (provider_name, client, model) in enumerate(route):
        label = f"{provider_name}/{model}"
        yield {"type": "start", "model": label}
        # "yield from" passes this model's events straight through, then gives
        # back how the run ended: "done", or "unavailable" with the reason.
        outcome, reason = yield from run_with_model(email, client, model, label)
        if outcome == "done":
            return
        if index + 1 < len(route):
            next_name, _, next_model = route[index + 1]
            yield {"type": "switch_model", "from": label, "to": f"{next_name}/{next_model}", "reason": reason}
        else:
            yield {"type": "error", "message": "Every model is busy or over its free-tier limit right now. "
                                               f"Use replay mode, or try again later. Last message: {reason}"}


def run_with_model(email, client, model, label):
    """One attempt with one model. Returns ("done", None) or ("unavailable", reason)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": email_as_text(email)},
    ]
    tool_calls_made = 0
    format_retries = 0
    waits = 0
    steps = 0

    while True:
        if steps >= MAX_STEPS:
            yield {"type": "error", "message": f"Stopped after {MAX_STEPS} steps without a final answer. Escalate to a colleague."}
            return "done", None
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, tools=TOOL_SCHEMAS
            )
        except RateLimitError as error:
            wait = retry_wait_seconds(error)
            if wait > MAX_SHORT_WAIT or waits >= MAX_WAITS_PER_MODEL:
                return "unavailable", str(error)[:300]
            waits += 1
            yield {"type": "waiting", "seconds": round(wait), "reason": str(error)[:300]}
            time.sleep(wait)
            continue
        except APIConnectionError as error:   # includes timeouts
            return "unavailable", f"No reply within {REQUEST_TIMEOUT}s or connection failed: {error}"
        except APIStatusError as error:
            if error.status_code >= 500:      # the provider's servers are overloaded or failing
                return "unavailable", str(error)[:300]
            yield {"type": "error", "message": f"The AI service returned an error: {error}"}
            return "done", None
        except Exception as error:
            yield {"type": "error", "message": f"The AI service returned an error: {error}"}
            return "done", None

        steps += 1
        message = response.choices[0].message
        # Send back only the standard fields. Tool calls are kept whole, because
        # Gemini attaches hidden fields to them that must be returned unchanged.
        # Other extras (such as Groq's "reasoning") are left out.
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
                    # Compact JSON: fewer tokens, which matters on free-tier token limits.
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
               "model": label, "tool_calls": tool_calls_made, "steps": steps}
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
        "events": [e for e in events if e["type"] not in ("waiting", "switch_model", "start")],
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
    elif kind == "waiting":
        print(f"\n[waiting] Rate limit reached; waiting {event['seconds']}s, then continuing...")
        print(f"          Service said: {event['reason']}")
    elif kind == "switch_model":
        print(f"\n[switching] {event['from']} is busy or over its limit; restarting on {event['to']}.")
        print(f"            Service said: {event['reason']}")
    elif kind == "error":
        print(f"\n[error] {event['message']}")


def check_models():
    """For every provider with a key, send each model a tiny request to see if it can be used now."""
    for name in provider_order():
        if not get_setting(PROVIDERS[name]["key_name"]):
            print(f"{name}: no key in secrets.toml, skipped")
            continue
        client = make_client(name)
        try:
            listed = list(client.models.list())
        except Exception as error:
            print(f"{name}: key not accepted or service unreachable: {str(error)[:120]}")
            continue
        available = {m.id.removeprefix("models/") for m in listed}
        print(f"{name}: key works")
        for model in models_for(name):
            if model not in available:
                print(f"  {model:<28} not available on this account")
                continue
            check_one_model(client, model)
        if name == "openrouter":
            show_free_tool_models(listed)


def show_free_tool_models(listed):
    """OpenRouter lists many models; show the free ones that support tool calling."""
    free = []
    for m in listed:
        info = m.model_dump()
        if m.id.endswith(":free") and "tools" in (info.get("supported_parameters") or []):
            free.append((info.get("created") or 0, m.id))
    print(f"  Free OpenRouter models that support tools ({len(free)}), newest first:")
    for _, model_id in sorted(free, reverse=True)[:15]:
        print(f"    {model_id}")


def check_one_model(client, model):
    print(f"  {model:<28} ", end="", flush=True)   # result is printed on the same line
    try:
        client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Reply OK"}], max_tokens=5)
        print("OK")
    except RateLimitError as error:
        print(f"over its limit: {str(error)[:150]}")
    except APIConnectionError:
        print(f"no reply within {REQUEST_TIMEOUT}s")
    except APIStatusError as error:
        print(f"unavailable ({error.status_code}): {str(error)[:120]}")
    except Exception as error:
        print(f"error: {str(error)[:150]}")


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

    route = build_route()
    print(f"Models to try, in order: {describe_route(route)}")
    print(email_as_text(email))
    print("-" * 60)

    events = []
    for event in run_agent(email, route):
        print_event(event)
        events.append(event)

    if "--save" in sys.argv:
        if any(e["type"] == "final" for e in events):
            print(f"\nSaved for replay: {save_recording(chosen, events)}")
        else:
            print("\nNot saved: the run did not finish with a final answer.")
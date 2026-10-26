"""
app.py - the screen for Order Planner (run with:  streamlit run app.py).

What a trade-desk colleague sees:
  - an inbox of contractor emails
  - Order Planner's steps, shown live as it works (the "glass box")
  - the plan or options, with every number checked by code
  - a drafted reply they can edit, then approve or reject

Two modes:
  - Replay: plays back a recorded real run (recordings/*.json). No AI calls, no cost.
  - Live:   calls the AI. Limited per visitor, and optionally behind a passcode.

Remember: Streamlit re-runs this whole file from the top on every click.
st.session_state is the memory that survives those re-runs.
"""

import json
import os
import re
import time

import streamlit as st

from agent import KEY_NAME, MODELS, get_setting, run_agent
from data import DELIVERY_SERVICES, DEMO_NOW, EMAILS, LOCATIONS, PRODUCTS, STOCK

LIVE_RUN_LIMIT = 3       # live runs allowed per visitor session
REPLAY_STEP_DELAY = 0.6  # seconds between steps when replaying, so it reads like a live run

st.set_page_config(page_title="Order Planner", page_icon="📦", layout="wide")

# On Streamlit Cloud, keys live in st.secrets. Copy them into the environment so
# agent.py finds them the same way it does on a laptop.
try:
    for key, value in st.secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(key, value)
except Exception:
    pass  # no secrets configured: replay mode still works

# Memory that survives Streamlit's re-runs
state = st.session_state
state.setdefault("runs", {})        # email id -> {"events": [...], "source": "...", "note": "..."}
state.setdefault("decisions", {})   # email id -> what the colleague decided
state.setdefault("live_runs_used", 0)
state.setdefault("unlocked", False)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def money(value):
    return f"£{value:,.2f}"


def load_recording(email_id):
    try:
        with open(f"recordings/{email_id}.json", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return None


def replay_events(recording):
    """Yield a recording's events with a short pause, so it plays like a live run."""
    for event in recording["events"]:
        if event["type"] != "final":
            time.sleep(REPLAY_STEP_DELAY)
        yield event


TOOL_LABELS = {
    "search_catalog": "Searched the catalogue",
    "get_stock_and_delivery": "Checked stock and delivery",
    "validate_plan": "Validated a plan",
}


def describe_call(name, args):
    if name == "search_catalog":
        return f"for “{args.get('query', '')}”"
    if name == "get_stock_and_delivery":
        sku = args.get("sku", "")
        return f"for {PRODUCTS.get(sku, {}).get('name', sku)}"
    if name == "validate_plan":
        limit = args.get("max_delivery_charge")
        lines = len(args.get("lines", []))
        return f"with {lines} line{'s' if lines != 1 else ''}" + (f", delivery limit {money(limit)}" if limit is not None else "")
    return ""


def describe_result(name, result):
    if "error" in result:
        return f"⚠️ {result['error']}"
    if name == "search_catalog":
        if not result.get("matches"):
            return "No matching products."
        return "Found: " + ", ".join(f"{m['name']} ({money(m['unit_price'])})" for m in result["matches"])
    if name == "get_stock_and_delivery":
        return " · ".join(f"{loc['location_name']}: {loc['in_stock']} in stock" for loc in result["locations"])
    if name == "validate_plan":
        if result.get("valid"):
            limit = result.get("max_delivery_charge")
            return f"✅ Valid · delivery {money(result['delivery_total'])}" + (f" (limit {money(limit)})" if limit is not None else "")
        return "❌ " + " ".join(result.get("problems", []))
    return ""


def render_event(event):
    """Draw one step of the agent's work."""
    kind = event["type"]
    if kind == "start":
        st.caption(f"Model: {event['model']}")
    elif kind == "thought":
        st.markdown(f"💭 *{event['text']}*")
    elif kind == "tool_call":
        st.markdown(f"🔧 **{TOOL_LABELS.get(event['name'], event['name'])}** {describe_call(event['name'], event['arguments'])}")
    elif kind == "tool_result":
        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ {describe_result(event['name'], event['result'])}")
        st.json(event["result"], expanded=False)
    elif kind == "switch_model":
        st.warning(f"{event['from']} is busy; restarting on {event['to']}.")
    elif kind == "error":
        st.error(event["message"])
    elif kind == "final":
        st.markdown(f"✅ **Plan ready** · {event['steps']} steps, {event['tool_calls']} tool calls")


def figures_the_code_produced(answer):
    """Every £ figure the validator calculated, so we can check the AI's reply against them."""
    checks = [answer.get("plan_check")] + [o.get("check") for o in answer.get("options") or []]
    figures = set()
    for check in filter(None, checks):
        figures.update([check.get("goods_total"), check.get("delivery_total"), check.get("grand_total"),
                        check.get("max_delivery_charge")])
        for line in check.get("lines", []):
            figures.update([line["line_total"], line["unit_price"]])
        for shipment in check.get("shipments", []):
            figures.add(shipment["charge"])
    return {round(float(f), 2) for f in figures if f is not None}


def unverified_figures(reply, answer):
    """£ amounts in the reply that the validator never produced."""
    in_reply = re.findall(r"£\s?([\d,]+(?:\.\d{1,2})?)", reply)
    known = figures_the_code_produced(answer)
    return sorted({f"£{a}" for a in in_reply if round(float(a.replace(",", "")), 2) not in known})


# ---------------------------------------------------------------------------
# Drawing the plan
# ---------------------------------------------------------------------------
def render_check(check, is_option=False):
    """Show a plan exactly as validate_plan calculated it.

    An option relaxes one of the customer's requirements, so its check is against
    the relaxed version: say so, rather than implying it meets the original request.
    """
    if not check.get("valid"):
        st.error("Checked by code, and it fails: " + " ".join(check.get("problems", [])), icon="❌")
    elif is_option:
        st.success("Checked by code: stock, order cut-offs and arrival times confirmed. "
                   "What it gives up is stated above.", icon="✅")
    else:
        st.success("Checked by code: stock, order cut-offs, deadlines and delivery budget all pass.", icon="✅")

    st.table(
        [
            {
                "Item": line["name"],
                "Qty": line["quantity"],
                "Delivery": f"{DELIVERY_SERVICES.get(line['service'], {}).get('name', line['service'])}, "
                            f"from {LOCATIONS.get(line['location'], line['location'])}",
                "Arrives": line["arrives"],
                "Line total": money(line["line_total"]),
            }
            for line in check.get("lines", [])
        ],
        hide_index=True,
    )
    limit = None if is_option else check.get("max_delivery_charge")
    goods, delivery, total = st.columns(3)
    goods.metric("Goods", money(check.get("goods_total", 0)))
    delivery.metric("Delivery", money(check.get("delivery_total", 0)),
                    help="One charge per delivery service used.",
                    delta=f"customer's limit {money(limit)}" if limit is not None else None, delta_color="off")
    total.metric("Total", money(check.get("grand_total", 0)))


def render_answer(email_id, answer):
    st.markdown("#### What the customer needs")
    st.write(answer.get("understanding", ""))
    if answer.get("assumptions"):
        st.warning("**Assumptions to check before approving**\n\n" +
                   "\n".join(f"- {a}" for a in answer["assumptions"]), icon="🔎")

    chosen_label = None
    if answer.get("status") == "plan_proposed" and answer.get("plan_check"):
        st.markdown("#### Proposed plan")
        render_check(answer["plan_check"])
    else:
        st.markdown("#### This request can't be met in full")
        st.caption("Order Planner hasn't guessed. Here are the options; you choose which to agree with the customer.")
        options = answer.get("options") or []
        for number, option in enumerate(options, start=1):
            with st.container(border=True):
                st.markdown(f"**Option {number}: {option.get('label', '')}**")
                st.caption(option.get("summary", ""))
                render_check(option.get("check", {}), is_option=True)
        if options:
            chosen_label = st.radio("Option to agree with the customer",
                                    [f"Option {n}: {o.get('label', '')}" for n, o in enumerate(options, start=1)],
                                    key=f"option_{email_id}")

    render_reply_and_decision(email_id, answer, chosen_label)


def render_reply_and_decision(email_id, answer, chosen_label):
    st.markdown("#### Draft reply to the customer")
    reply = st.text_area("Edit before sending", value=answer.get("reply_draft", ""),
                         height=260, key=f"reply_{email_id}")

    unknown = unverified_figures(reply, answer)
    if unknown:
        st.warning(f"These amounts in the reply weren't produced by the validator: {', '.join(unknown)}. "
                   "Check them before sending.", icon="⚠️")
    else:
        st.caption("✓ Every £ amount in this reply matches a figure calculated by code.")

    decision = state.decisions.get(email_id)
    if decision:
        if decision["action"] == "approved":
            st.success(f"Approved{' (' + decision['option'] + ')' if decision['option'] else ''}. "
                       "In production this would send the reply and create the order. Nothing has been sent in this demo.")
        else:
            st.info("Rejected. In production this would go back to the queue for a colleague to handle by hand.")
        if st.button("Undo decision", key=f"undo_{email_id}"):
            del state.decisions[email_id]
            st.rerun()
        return

    approve, reject = st.columns(2)
    if approve.button("Approve and send", type="primary", width="stretch", key=f"approve_{email_id}"):
        state.decisions[email_id] = {"action": "approved", "option": chosen_label}
        st.rerun()
    if reject.button("Reject", width="stretch", key=f"reject_{email_id}"):
        state.decisions[email_id] = {"action": "rejected", "option": None}
        st.rerun()


# ---------------------------------------------------------------------------
# Sidebar: mode and safeguards
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Order Planner")
    st.caption("Prototype · mock data · not affiliated with Travis Perkins")
    st.caption(f"Demo clock: {DEMO_NOW:%a %d %b %Y, %H:%M}")

    mode = st.radio("Mode", ["Replay a recorded run", "Run live"],
                    help="Replay plays back a real recorded run, with no AI calls. Live calls the AI model.")
    live = mode == "Run live"

    if live:
        passcode = get_setting("LIVE_PASSCODE")
        if passcode and not state.unlocked:
            entered = st.text_input("Passcode for live mode", type="password")
            if entered == passcode:
                state.unlocked = True
                st.rerun()
            elif entered:
                st.error("Wrong passcode.")
        st.caption(f"Models, in fallback order: {', '.join(MODELS)}")
        if not get_setting(KEY_NAME):
            st.error(f"No {KEY_NAME} found, so live runs will fail. Replay still works.")
        st.caption(f"Live runs used this session: {state.live_runs_used} of {LIVE_RUN_LIMIT}")

live_allowed = live and (state.unlocked or not get_setting("LIVE_PASSCODE")) and state.live_runs_used < LIVE_RUN_LIMIT


# ---------------------------------------------------------------------------
# Main screen: inbox on the left, the selected email on the right
# ---------------------------------------------------------------------------
st.title("📦 Order Planner")
st.caption("Turns a contractor's email into a checked delivery plan. A colleague approves every plan before anything is sent.")

inbox_tab, data_tab = st.tabs(["Inbox", "System data (mock)"])

with data_tab:
    st.caption("What Order Planner's tools can see. Every number in a plan comes from here, "
               f"never from the AI. Demo clock: {DEMO_NOW:%a %d %b, %H:%M}.")
    st.markdown("#### Products and stock")
    st.table(
        [
            {
                "Product": product["name"],
                "Price": money(product["unit_price"]),
                **{f"In stock: {name}": STOCK[sku][code] for code, name in LOCATIONS.items()},
                "Notes": product["notes"],
            }
            for sku, product in PRODUCTS.items()
        ],
        hide_index=True,
    )
    st.markdown("#### Delivery options")
    st.table(
        [
            {
                "Delivery": service["name"],
                "From": LOCATIONS[service["location"]],
                "Order by": f"{service['order_by']:%a %H:%M}",
                "Arrives": f"{service['arrives']:%a %H:%M}",
                "Charge": money(service["charge"]),
            }
            for service in DELIVERY_SERVICES.values()
        ],
        hide_index=True,
    )
    st.caption("Each delivery is charged once, however many items it carries.")

with inbox_tab:
    inbox, work = st.columns([1, 2.4], gap="large")

    with inbox:
        st.markdown("#### Trade desk inbox")
        emails_by_id = {email["id"]: email for email in EMAILS}

        def inbox_label(email_id):
            email = emails_by_id[email_id]
            done = "✓ " if email_id in state.decisions else ""
            return f"{done}Email {email_id}: {email['label']}"

        email_id = st.radio("Inbox", list(emails_by_id), format_func=inbox_label, label_visibility="collapsed")

    selected = emails_by_id[email_id]

    with work:
        with st.container(border=True):
            st.markdown(f"**{selected['subject']}**")
            st.caption(f"From {selected['sender']} · received {selected['received']:%a %d %b, %H:%M}")
            st.write(selected["body"])

        run = state.runs.get(email_id)

        if run is None:
            if live and not live_allowed:
                if state.live_runs_used >= LIVE_RUN_LIMIT:
                    st.info(f"You've used the {LIVE_RUN_LIMIT} live runs for this session. Switch to replay to see recorded runs.")
                else:
                    st.info("Enter the passcode in the sidebar to run live, or switch to replay.")
            elif st.button("Plan this order", type="primary"):
                if live:
                    state.live_runs_used += 1
                    source, note, events_in = "live", "Live run", run_agent(selected)
                else:
                    recording = load_recording(email_id)
                    if recording is None:
                        st.error("No recording for this email yet. Switch to live mode, or record one with: python agent.py "
                                 f"{email_id} --save")
                        st.stop()
                    source = "replay"
                    note = f"Replay of a recorded live run · {recording['model']} · recorded {recording['recorded_at']}"
                    events_in = replay_events(recording)

                events = []
                with st.status("Order Planner is working…", expanded=True) as status:
                    for event in events_in:
                        render_event(event)
                        events.append(event)
                    status.update(label="Finished", state="complete", expanded=False)
                state.runs[email_id] = {"events": events, "source": source, "note": note}
                st.rerun()
        else:
            events = run["events"]
            final = next((e for e in events if e["type"] == "final"), None)

            st.caption(run["note"])
            label = (f"How Order Planner worked it out: {final['steps']} steps, {final['tool_calls']} tool calls"
                     if final else "What happened")
            with st.status(label, state="complete" if final else "error", expanded=final is None):
                for event in events:
                    render_event(event)

            if final:
                render_answer(email_id, final["answer"])
            else:
                st.error("Order Planner didn't finish. Escalate to a colleague.")
                if load_recording(email_id) and st.button("Show the recorded run instead"):
                    recording = load_recording(email_id)
                    state.runs[email_id] = {"events": recording["events"], "source": "replay",
                                            "note": f"Replay of a recorded live run · {recording['model']} · recorded {recording['recorded_at']}"}
                    st.rerun()

            if st.button("Clear and run again", key=f"clear_{email_id}"):
                del state.runs[email_id]
                state.decisions.pop(email_id, None)
                st.rerun()
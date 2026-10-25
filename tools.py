"""
tools.py - the three tools Order Planner (the AI) is allowed to call.

None of these use AI. They are ordinary Python functions that read data.py.
The AI decides WHICH tool to call and with WHAT inputs; the tools supply
every fact and every number. In a real deployment each tool would call one
of Travis Perkins' systems instead of data.py.

  search_catalog          -> product catalogue search
  get_stock_and_delivery  -> stock and transport systems
  validate_plan           -> business rules: stock, deadlines, delivery budget
"""

import json
from datetime import datetime

from data import DEMO_NOW, DELIVERY_SERVICES, LOCATIONS, PRODUCTS, STOCK


def _fmt(moment):
    """Turn a datetime into readable text, e.g. 'Wed 28 Oct 07:30'."""
    return moment.strftime("%a %d %b %H:%M")


# ---------------------------------------------------------------------------
# Tool 1: search the catalogue
# ---------------------------------------------------------------------------
def search_catalog(query):
    """Find products whose keywords appear in the query.

    This stands in for a real semantic search. Each product scores one point
    per keyword found in the query; the best matches come first.
    """
    text = query.lower()
    matches = []
    for sku, product in PRODUCTS.items():
        score = sum(1 for keyword in product["keywords"] if keyword in text)
        if score > 0:
            matches.append({
                "sku": sku,
                "name": product["name"],
                "unit_price": product["unit_price"],
                "notes": product["notes"],
                "match_score": score,
            })

    matches.sort(key=lambda m: m["match_score"], reverse=True)
    if not matches:
        return {"query": query, "matches": [], "message": "No products matched. Try other words."}
    return {"query": query, "matches": matches[:5]}


# ---------------------------------------------------------------------------
# Tool 2: stock and delivery options for one product
# ---------------------------------------------------------------------------
def get_stock_and_delivery(sku):
    """Stock at each location, and the delivery services that can still be booked."""
    if sku not in PRODUCTS:
        return {"error": f"Unknown SKU '{sku}'. Use search_catalog to find valid SKUs."}

    locations = []
    for code, location_name in LOCATIONS.items():
        services = []
        for service_id, service in DELIVERY_SERVICES.items():
            # Only services from this location whose order cut-off hasn't passed.
            if service["location"] == code and service["order_by"] > DEMO_NOW:
                services.append({
                    "service": service_id,
                    "name": service["name"],
                    "order_by": _fmt(service["order_by"]),
                    "arrives": _fmt(service["arrives"]),
                    "charge": service["charge"],
                })
        locations.append({
            "location": code,
            "location_name": location_name,
            "in_stock": STOCK[sku][code],
            "delivery_services": services,
        })

    return {
        "sku": sku,
        "name": PRODUCTS[sku]["name"],
        "unit_price": PRODUCTS[sku]["unit_price"],
        "delivery_note": "Each delivery service used is charged once, however many items it carries.",
        "locations": locations,
    }


# ---------------------------------------------------------------------------
# Tool 3: check a proposed plan and do all the maths
# ---------------------------------------------------------------------------
def validate_plan(lines, max_delivery_charge=None):
    """Check a proposed plan against stock, cut-offs, deadlines and the delivery budget.

    lines: a list of plan lines, each like
        {"sku": "PMP-PET-3IN", "quantity": 2, "location": "HUB",
         "service": "HUB-EXPRESS", "needed_by": "2026-10-28 08:00"}
        ("needed_by" is optional.)
    max_delivery_charge: the customer's limit on total delivery charges, if any.

    Returns every total and a list of problems. The plan is valid only if
    there are no problems.
    """
    problems = []
    checked_lines = []
    used_from_stock = {}   # (sku, location) -> quantity this plan takes
    shipments = {}         # service id -> list of SKUs it carries

    if not lines:
        return {"valid": False, "problems": ["The plan has no lines."]}

    for number, line in enumerate(lines, start=1):
        sku = line.get("sku")
        location = line.get("location")
        service_id = line.get("service")
        label = f"Line {number} ({sku})"

        # --- Basic facts must exist ---
        if sku not in PRODUCTS:
            problems.append(f"{label}: unknown SKU.")
            continue
        if location not in LOCATIONS:
            problems.append(f"{label}: unknown location '{location}'.")
            continue
        service = DELIVERY_SERVICES.get(service_id)
        if service is None:
            problems.append(f"{label}: unknown delivery service '{service_id}'.")
            continue
        if service["location"] != location:
            problems.append(f"{label}: service {service_id} does not run from {location}.")
            continue

        try:
            quantity = int(line.get("quantity"))
        except (TypeError, ValueError):
            problems.append(f"{label}: quantity must be a whole number.")
            continue
        if quantity <= 0:
            problems.append(f"{label}: quantity must be more than zero.")
            continue

        # --- Order cut-off ---
        if service["order_by"] <= DEMO_NOW:
            problems.append(f"{label}: {service_id} cut-off ({_fmt(service['order_by'])}) has passed.")

        # --- Deadline, if the line has one ---
        meets_deadline = None
        needed_by_text = line.get("needed_by")
        if needed_by_text:
            try:
                needed_by = datetime.strptime(needed_by_text, "%Y-%m-%d %H:%M")
            except ValueError:
                problems.append(f"{label}: needed_by must look like 2026-10-28 08:00.")
            else:
                meets_deadline = service["arrives"] <= needed_by
                if not meets_deadline:
                    problems.append(
                        f"{label}: arrives {_fmt(service['arrives'])}, "
                        f"but is needed by {_fmt(needed_by)}."
                    )

        used_from_stock[(sku, location)] = used_from_stock.get((sku, location), 0) + quantity
        shipments.setdefault(service_id, []).append(sku)

        unit_price = PRODUCTS[sku]["unit_price"]
        checked_lines.append({
            "sku": sku,
            "name": PRODUCTS[sku]["name"],
            "quantity": quantity,
            "location": location,
            "service": service_id,
            "arrives": _fmt(service["arrives"]),
            "needed_by": needed_by_text,
            "meets_deadline": meets_deadline,
            "unit_price": unit_price,
            "line_total": round(unit_price * quantity, 2),
        })

    # --- Stock: the plan can't take more than a location holds ---
    for (sku, location), quantity in used_from_stock.items():
        available = STOCK[sku][location]
        if quantity > available:
            problems.append(
                f"{sku}: plan takes {quantity} from {location}, but only {available} in stock."
            )

    # --- Delivery charges: one charge per service used ---
    shipment_list = [
        {
            "service": service_id,
            "location": DELIVERY_SERVICES[service_id]["location"],
            "arrives": _fmt(DELIVERY_SERVICES[service_id]["arrives"]),
            "charge": DELIVERY_SERVICES[service_id]["charge"],
            "carries": skus,
        }
        for service_id, skus in shipments.items()
    ]
    delivery_total = round(sum(s["charge"] for s in shipment_list), 2)
    goods_total = round(sum(l["line_total"] for l in checked_lines), 2)

    if max_delivery_charge is not None and delivery_total > float(max_delivery_charge):
        problems.append(
            f"Delivery charges total £{delivery_total:.2f}, over the £{float(max_delivery_charge):.2f} limit."
        )

    return {
        "valid": not problems,
        "problems": problems,
        "lines": checked_lines,
        "shipments": shipment_list,
        "goods_total": goods_total,
        "delivery_total": delivery_total,
        "max_delivery_charge": max_delivery_charge,
        "grand_total": round(goods_total + delivery_total, 2),
    }


# ---------------------------------------------------------------------------
# Descriptions of the tools, in the format the AI model expects.
# This is all the model knows about the tools: their names, what they do,
# and what inputs they take.
# ---------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": "Search the product catalogue with everyday words, e.g. 'cheap mud cover'. Returns matching products with SKU, price and notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What the customer asked for, in plain words."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_and_delivery",
            "description": "For one SKU, get stock at each location and the delivery services that can still be booked, with arrival times and charges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "description": "A SKU returned by search_catalog."},
                },
                "required": ["sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_plan",
            "description": "Check a proposed delivery plan against stock, order cut-offs, deadlines and the delivery budget. Calculates all totals. Always call this before proposing a plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "array",
                        "description": "One line per product, location and delivery service.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "quantity": {"type": "integer"},
                                "location": {"type": "string", "description": "Location code, e.g. GLA or HUB."},
                                "service": {"type": "string", "description": "Delivery service id, e.g. HUB-EXPRESS."},
                                "needed_by": {"type": "string", "description": "Optional deadline, format 'YYYY-MM-DD HH:MM'."},
                            },
                            "required": ["sku", "quantity", "location", "service"],
                        },
                    },
                    "max_delivery_charge": {"type": "number", "description": "The customer's limit on total delivery charges in pounds, if they gave one."},
                },
                "required": ["lines"],
            },
        },
    },
]


# Maps each tool name to the Python function that runs it.
TOOL_FUNCTIONS = {
    "search_catalog": search_catalog,
    "get_stock_and_delivery": get_stock_and_delivery,
    "validate_plan": validate_plan,
}


def run_tool(name, arguments):
    """Run the tool the AI asked for. Never crashes: errors are returned as data."""
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return {"error": f"There is no tool called '{name}'."}
    try:
        return function(**arguments)
    except Exception as error:   # a bad input from the AI shouldn't stop the demo
        return {"error": f"{name} failed: {error}"}


# ---------------------------------------------------------------------------
# Running this file directly checks the tools against the three scenarios.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    def show(title, result):
        print(f"\n=== {title} ===")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    show("Search: 'cheap mud cover'", search_catalog("cheap mud cover"))
    show("Stock and delivery: PMP-PET-3IN", get_stock_and_delivery("PMP-PET-3IN"))

    # Scenario A, a good plan: pumps by hub express; cement and tarps on the Wednesday van.
    plan_a = [
        {"sku": "PMP-PET-3IN", "quantity": 2, "location": "HUB", "service": "HUB-EXPRESS", "needed_by": "2026-10-28 08:00"},
        {"sku": "CEM-RAPID-25", "quantity": 60, "location": "GLA", "service": "GLA-VAN-WED"},
        {"sku": "TRP-ECO-4X3", "quantity": 10, "location": "GLA", "service": "GLA-VAN-WED"},
    ]
    result = validate_plan(plan_a, max_delivery_charge=200)
    print(f"\nScenario A, good plan:   valid={result['valid']}  delivery=£{result['delivery_total']:.2f}  problems={result['problems']}")

    # Scenario A, a bad plan: pumps by hub standard arrive too late.
    plan_a_bad = [dict(plan_a[0], service="HUB-STANDARD")] + plan_a[1:]
    result = validate_plan(plan_a_bad, max_delivery_charge=200)
    print(f"Scenario A, late pumps:  valid={result['valid']}  problems={result['problems']}")

    # Scenario B: everything on the Thursday van.
    plan_b = [
        {"sku": "TIM-CLS-24", "quantity": 30, "location": "GLA", "service": "GLA-VAN-THU"},
        {"sku": "PST-20", "quantity": 10, "location": "GLA", "service": "GLA-VAN-THU"},
    ]
    result = validate_plan(plan_b)
    print(f"Scenario B:              valid={result['valid']}  delivery=£{result['delivery_total']:.2f}  problems={result['problems']}")

    # Scenario C: 8 pumps by 08:00 for under £100 can't be done.
    plan_c = [{"sku": "PMP-PET-3IN", "quantity": 8, "location": "HUB", "service": "HUB-EXPRESS", "needed_by": "2026-10-28 08:00"}]
    result = validate_plan(plan_c, max_delivery_charge=100)
    print(f"Scenario C:              valid={result['valid']}  problems={result['problems']}")
"""
data.py - the mock "database" for the Order Planner demo.

Everything in this file is invented. In a real deployment, each section
would be replaced by a connection to one of Travis Perkins' systems:
  PRODUCTS          -> the product catalogue
  STOCK             -> the stock system for each branch and warehouse
  DELIVERY_SERVICES -> the transport / delivery planning system
  EMAILS            -> the branch's shared inbox
"""

from datetime import datetime

# The demo pretends it is always this moment, so results are the same every run.
# Tuesday 27 October 2026, 14:40.
DEMO_NOW = datetime(2026, 10, 27, 14, 40)


# ---------------------------------------------------------------------------
# Where stock can come from
# ---------------------------------------------------------------------------
LOCATIONS = {
    "BRANCH": "Local branch",
    "WAREHOUSE": "Regional warehouse",
}


# ---------------------------------------------------------------------------
# Product catalogue
# "keywords" is what the catalogue search matches against.
# ---------------------------------------------------------------------------
PRODUCTS = {
    "PUMP-PETROL": {
        "name": "Petrol water pump",
        "unit_price": 420.00,
        "keywords": ["pump", "water", "flood", "heavy duty", "heavy-duty", "petrol", "dewatering"],
        "notes": "Heavy duty. Runs on petrol, so no power supply is needed on site.",
    },
    "CEMENT-RAPID": {
        "name": "Rapid-set cement, 25 kg bag",
        "unit_price": 9.50,
        "keywords": ["cement", "rapid", "fast-setting", "fast setting", "quick set", "quick-set"],
        "notes": "",
    },
    "TARP-ECONOMY": {
        "name": "Tarpaulin, economy, 4 x 3 m",
        "unit_price": 6.00,
        "keywords": ["tarp", "tarpaulin", "cover", "sheet", "mud", "cheap", "economy"],
        "notes": "Lightweight. The cheapest cover we stock.",
    },
    "TARP-HEAVY": {
        "name": "Tarpaulin, heavy-duty, 6 x 4 m",
        "unit_price": 14.00,
        "keywords": ["tarp", "tarpaulin", "cover", "sheet", "mud", "heavy duty", "heavy-duty"],
        "notes": "",
    },
    "TIMBER-4X2": {
        "name": "Timber, 4x2, 2.4 m length",
        "unit_price": 4.20,
        "keywords": ["timber", "wood", "4x2", "4 x 2", "cls", "stud", "lengths"],
        "notes": "Also called CLS timber.",
    },
    "CONCRETE-POST": {
        "name": "Fence-post concrete, 20 kg bag",
        "unit_price": 7.00,
        "keywords": ["concrete", "fence-post", "fence post", "postcrete", "post mix", "postmix"],
        "notes": "Often sold as Postcrete.",
    },
}


# ---------------------------------------------------------------------------
# Stock: how many of each product each location has right now
# ---------------------------------------------------------------------------
STOCK = {
    "PUMP-PETROL":   {"BRANCH": 0,   "WAREHOUSE": 6},
    "CEMENT-RAPID":  {"BRANCH": 120, "WAREHOUSE": 0},
    "TARP-ECONOMY":  {"BRANCH": 25,  "WAREHOUSE": 0},
    "TARP-HEAVY":    {"BRANCH": 40,  "WAREHOUSE": 0},
    "TIMBER-4X2":    {"BRANCH": 200, "WAREHOUSE": 0},
    "CONCRETE-POST": {"BRANCH": 60,  "WAREHOUSE": 0},
}


# ---------------------------------------------------------------------------
# Delivery options to the customer's site.
# The charge is per delivery: one charge however many items it carries.
# An option can only be booked before its "order_by" time.
# ---------------------------------------------------------------------------
DELIVERY_SERVICES = {
    "VAN-WED": {
        "location": "BRANCH",
        "name": "Branch van, Wednesday morning",
        "order_by": datetime(2026, 10, 27, 17, 0),   # Tue 17:00
        "arrives":  datetime(2026, 10, 28, 7, 30),   # Wed 07:30
        "charge": 25.00,
    },
    "VAN-THU": {
        "location": "BRANCH",
        "name": "Branch van, Thursday morning",
        "order_by": datetime(2026, 10, 28, 17, 0),   # Wed 17:00
        "arrives":  datetime(2026, 10, 29, 7, 30),   # Thu 07:30
        "charge": 25.00,
    },
    "COURIER-OVERNIGHT": {
        "location": "WAREHOUSE",
        "name": "Overnight courier",
        "order_by": datetime(2026, 10, 27, 18, 0),   # Tue 18:00
        "arrives":  datetime(2026, 10, 28, 7, 30),   # Wed 07:30
        "charge": 150.00,
    },
    "COURIER-STANDARD": {
        "location": "WAREHOUSE",
        "name": "Standard courier",
        "order_by": datetime(2026, 10, 27, 18, 0),   # Tue 18:00
        "arrives":  datetime(2026, 10, 28, 13, 0),   # Wed 13:00
        "charge": 50.00,
    },
}


# ---------------------------------------------------------------------------
# The inbox: three contractor emails, all received at DEMO_NOW
# "label" is how the email is listed in the demo's inbox.
# ---------------------------------------------------------------------------
EMAILS = [
    {
        "id": "A",
        "label": "Flooded site (urgent)",
        "sender": "Site manager, groundworks firm",
        "subject": "URGENT: flooded site",
        "received": DEMO_NOW,
        "body": (
            "Our site is flooded. Need 2 heavy-duty water pumps, 60 bags of fast-setting "
            "cement and whatever you've got cheap to cover the mud. Keep delivery under "
            "£200 total, but the pumps must be on site by 8am tomorrow or the job's ruined."
        ),
    },
    {
        "id": "B",
        "label": "Routine order",
        "sender": "Office manager, building firm",
        "subject": "Order for Thursday",
        "received": DEMO_NOW,
        "body": (
            "Hi, could we get 30 lengths of 4x2 timber and 10 bags of fence-post concrete, "
            "delivered Thursday morning? Thanks."
        ),
    },
    {
        "id": "C",
        "label": "Pumps first thing",
        "sender": "Site manager, construction firm",
        "subject": "Pumps needed first thing",
        "received": DEMO_NOW,
        "body": (
            "Need 8 heavy-duty water pumps on site by 8am tomorrow. "
            "Delivery can't cost more than £100."
        ),
    },
]


# Running this file directly prints a quick summary, as a check that it loads.
if __name__ == "__main__":
    print(f"Demo time: {DEMO_NOW:%a %d %b %H:%M}")
    print(f"{len(PRODUCTS)} products, {len(DELIVERY_SERVICES)} delivery options, {len(EMAILS)} emails")
    for sku, product in PRODUCTS.items():
        stock = STOCK[sku]
        print(f"  {product['name']:<34} £{product['unit_price']:>6.2f}   "
              f"Branch: {stock['BRANCH']:>3}   Warehouse: {stock['WAREHOUSE']:>3}")
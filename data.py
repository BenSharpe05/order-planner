"""
data.py - the mock "database" for the Order Planner demo.

Everything in this file is invented. In a real deployment, each section
would be replaced by a connection to one of Travis Perkins' systems:
  PRODUCTS          -> the product catalogue
  STOCK             -> the stock system for each branch and hub
  DELIVERY_SERVICES -> the transport / delivery planning system
  EMAILS            -> the branch's shared inbox
"""

from datetime import datetime

# The demo pretends it is always this moment, so results are the same every run.
# Tuesday 27 October 2026, 14:40.
DEMO_NOW = datetime(2026, 10, 27, 14, 40)


# ---------------------------------------------------------------------------
# Locations that can supply a Glasgow site
# ---------------------------------------------------------------------------
LOCATIONS = {
    "GLA": "Glasgow branch",
    "HUB": "Central Scotland hub",
}


# ---------------------------------------------------------------------------
# Product catalogue
# "keywords" is what the catalogue search matches against.
# ---------------------------------------------------------------------------
PRODUCTS = {
    "PMP-PET-3IN": {
        "name": "3-inch petrol trash pump",
        "unit_price": 420.00,
        "keywords": ["pump", "water", "flood", "heavy duty", "heavy-duty", "trash", "petrol", "dewatering"],
        "notes": "Runs on petrol, so no mains power is needed on site.",
    },
    "PMP-SUB-2IN": {
        "name": "2-inch electric submersible pump",
        "unit_price": 180.00,
        "keywords": ["pump", "water", "flood", "heavy duty", "heavy-duty", "submersible", "electric", "dewatering"],
        "notes": "Needs a 110V power supply on site.",
    },
    "CEM-RAPID-25": {
        "name": "Rapid-set cement, 25 kg bag",
        "unit_price": 9.50,
        "keywords": ["cement", "concrete", "rapid", "fast-setting", "fast setting", "quick set", "quick-set"],
        "notes": "",
    },
    "TRP-ECO-4X3": {
        "name": "Economy tarpaulin, 4 x 3 m",
        "unit_price": 6.00,
        "keywords": ["tarp", "tarpaulin", "cover", "sheet", "mud", "cheap", "economy"],
        "notes": "Lightweight. The cheapest cover we stock.",
    },
    "TRP-HD-6X4": {
        "name": "Heavy-duty tarpaulin, 6 x 4 m",
        "unit_price": 14.00,
        "keywords": ["tarp", "tarpaulin", "cover", "sheet", "mud", "heavy duty", "heavy-duty"],
        "notes": "",
    },
    "TIM-CLS-24": {
        "name": "CLS timber, 38 x 89 mm, 2.4 m length",
        "unit_price": 4.20,
        "keywords": ["timber", "wood", "cls", "4x2", "stud", "lengths"],
        "notes": "Often called '4x2' on site.",
    },
    "PST-20": {
        "name": "Postcrete, 20 kg bag",
        "unit_price": 7.00,
        "keywords": ["postcrete", "post mix", "postmix", "post concrete", "fence post"],
        "notes": "",
    },
}


# ---------------------------------------------------------------------------
# Stock: how many of each product each location has right now
# ---------------------------------------------------------------------------
STOCK = {
    "PMP-PET-3IN":  {"GLA": 0,   "HUB": 6},
    "PMP-SUB-2IN":  {"GLA": 1,   "HUB": 4},
    "CEM-RAPID-25": {"GLA": 120, "HUB": 0},
    "TRP-ECO-4X3":  {"GLA": 25,  "HUB": 0},
    "TRP-HD-6X4":   {"GLA": 40,  "HUB": 0},
    "TIM-CLS-24":   {"GLA": 200, "HUB": 0},
    "PST-20":       {"GLA": 60,  "HUB": 0},
}


# ---------------------------------------------------------------------------
# Delivery services to Glasgow sites.
# The charge is per shipment: one charge however many items it carries.
# A service can only be booked before its "order_by" time.
# ---------------------------------------------------------------------------
DELIVERY_SERVICES = {
    "GLA-SAMEDAY": {
        "location": "GLA",
        "name": "Same-day van",
        "order_by": datetime(2026, 10, 27, 15, 0),   # Tue 15:00
        "arrives":  datetime(2026, 10, 27, 17, 0),   # Tue 17:00
        "charge": 40.00,
    },
    "GLA-VAN-WED": {
        "location": "GLA",
        "name": "Scheduled van, Wednesday morning",
        "order_by": datetime(2026, 10, 27, 17, 0),   # Tue 17:00
        "arrives":  datetime(2026, 10, 28, 7, 30),   # Wed 07:30
        "charge": 25.00,
    },
    "GLA-VAN-THU": {
        "location": "GLA",
        "name": "Scheduled van, Thursday morning",
        "order_by": datetime(2026, 10, 28, 17, 0),   # Wed 17:00
        "arrives":  datetime(2026, 10, 29, 7, 30),   # Thu 07:30
        "charge": 25.00,
    },
    "HUB-EXPRESS": {
        "location": "HUB",
        "name": "Express overnight",
        "order_by": datetime(2026, 10, 27, 18, 0),   # Tue 18:00
        "arrives":  datetime(2026, 10, 28, 7, 30),   # Wed 07:30
        "charge": 150.00,
    },
    "HUB-STANDARD": {
        "location": "HUB",
        "name": "Standard next-day",
        "order_by": datetime(2026, 10, 27, 18, 0),   # Tue 18:00
        "arrives":  datetime(2026, 10, 28, 13, 0),   # Wed 13:00
        "charge": 50.00,
    },
}


# ---------------------------------------------------------------------------
# The inbox: three contractor emails, all received at DEMO_NOW
# ---------------------------------------------------------------------------
EMAILS = [
    {
        "id": "A",
        "sender": "Darren, McKay Groundworks",
        "subject": "URGENT - flooded trench Dalmarnock",
        "received": DEMO_NOW,
        "body": (
            "Disaster on the Dalmarnock site, trench is flooded. Need 2 heavy duty "
            "water pumps, 60 bags of fast-setting cement and whatever you've got cheap "
            "to cover the mud. We're bleeding money so keep delivery under £200 total, "
            "but if the pumps aren't on site by 8am tomorrow the job's ruined. "
            "Account MCK-2291."
        ),
    },
    {
        "id": "B",
        "sender": "Fiona, Brennan Builders",
        "subject": "Order for Shawlands job",
        "received": DEMO_NOW,
        "body": (
            "Hi, usual for the Shawlands job please: 30 lengths of 4x2 CLS and "
            "10 bags of Postcrete. Thursday morning is fine. Account BRN-1180. "
            "Cheers, Fiona"
        ),
    },
    {
        "id": "C",
        "sender": "Raj, RSL Civils",
        "subject": "Pumps needed first thing",
        "received": DEMO_NOW,
        "body": (
            "Need 8 heavy duty pumps on the Govan site by 8am tomorrow. Delivery "
            "can't be more than £100, margins are shot on this one. Account RSL-0457."
        ),
    },
]


# Running this file directly prints a quick summary, as a check that it loads.
if __name__ == "__main__":
    print(f"Demo time: {DEMO_NOW:%a %d %b %H:%M}")
    print(f"{len(PRODUCTS)} products, {len(DELIVERY_SERVICES)} delivery services, {len(EMAILS)} emails")
    for sku, product in PRODUCTS.items():
        stock = STOCK[sku]
        print(f"  {sku:<13} {product['name']:<40} £{product['unit_price']:>6.2f}   "
              f"Glasgow: {stock['GLA']:>3}   Hub: {stock['HUB']:>3}")
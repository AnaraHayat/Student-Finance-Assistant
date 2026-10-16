DECLARATIONS = [
    {"name": "get_rate",
     "description": "Get the current mid-market CNY to PKR reference rate "
                    "and its timestamp."},
    {"name": "get_balances",
     "description": "Get the sandbox PKR and CNY balances and the "
                    "permission tier."},
    {"name": "compare_routes",
     "description": "Compare all transfer routes for delivering an amount "
                    "of CNY. Returns total PKR cost, effective rate and "
                    "hidden cost per route, cheapest first. Optionally "
                    "filter by route type.",
     "parameters": {"type": "OBJECT", "properties": {
         "cny_to_deliver": {"type": "NUMBER",
                            "description": "CNY the student must receive"},
         "route_type": {"type": "STRING",
                        "description": "optional filter, e.g. bank_transfer"}},
         "required": ["cny_to_deliver"]}},
    {"name": "request_transfer",
     "description": "Request a sandbox transfer on a named route. The "
                    "mandate decides whether it is blocked, needs the "
                    "user's confirmation, or executes.",
     "parameters": {"type": "OBJECT", "properties": {
         "route_name": {"type": "STRING",
                        "description": "exact route name from compare_routes"},
         "cny_to_deliver": {"type": "NUMBER"}},
         "required": ["route_name", "cny_to_deliver"]}},
    {"name": "add_expense",
     "description": "Record one expense in CNY.",
     "parameters": {"type": "OBJECT", "properties": {
         "name": {"type": "STRING"},
         "on": {"type": "STRING", "description": "date as YYYY-MM-DD"},
         "amount_cny": {"type": "NUMBER"},
         "category": {"type": "STRING",
                      "description": "e.g. housing, food, transport"}},
         "required": ["name", "on", "amount_cny", "category"]}},
    {"name": "spending_summary",
     "description": "Summarize one month of spending in CNY and PKR, by "
                    "category, including hidden cost.",
     "parameters": {"type": "OBJECT", "properties": {
         "year": {"type": "INTEGER"}, "month": {"type": "INTEGER"}},
         "required": ["year", "month"]}},
    {"name": "add_event",
     "description": "Record a future obligation (money out) or inflow "
                    "(money in) in CNY for cash-flow forecasting.",
     "parameters": {"type": "OBJECT", "properties": {
         "name": {"type": "STRING"},
         "on": {"type": "STRING", "description": "date as YYYY-MM-DD"},
         "amount_cny": {"type": "NUMBER"},
         "kind": {"type": "STRING", "enum": ["obligation", "inflow"]}},
         "required": ["name", "on", "amount_cny", "kind"]}},
    {"name": "forecast_cashflow",
     "description": "Forecast CNY balance between two dates using the "
                    "recorded events. Finds the first shortfall, the "
                    "top-up needed and the latest date to send it.",
     "parameters": {"type": "OBJECT", "properties": {
         "start": {"type": "STRING", "description": "YYYY-MM-DD"},
         "end": {"type": "STRING", "description": "YYYY-MM-DD"},
         "start_balance_cny": {"type": "NUMBER",
                               "description": "optional; defaults to the "
                                              "sandbox CNY balance"},
         "buffer_cny": {"type": "NUMBER"},
         "lead_days": {"type": "INTEGER",
                       "description": "days a transfer takes"}},
         "required": ["start", "end"]}},
]
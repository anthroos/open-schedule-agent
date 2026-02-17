"""Anthropic tool definitions for owner and guest flows."""

GUEST_TOOLS = [
    {
        "name": "collect_guest_info",
        "description": (
            "Save the guest's contact info and meeting topic. "
            "Call this as soon as you know the guest's name and email. "
            "You MUST call this before confirm_booking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Guest's name.",
                },
                "email": {
                    "type": "string",
                    "description": "Guest's email for the calendar invite.",
                },
                "topic": {
                    "type": "string",
                    "description": "What the meeting is about (short summary).",
                },
            },
            "required": ["name", "email"],
        },
    },
    {
        "name": "confirm_booking",
        "description": (
            "Confirm and book a meeting slot. Call this ONLY after collect_guest_info has been called. "
            "Use the 1-based slot number from the AVAILABLE SLOTS list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slot_number": {
                    "type": "integer",
                    "description": "The 1-based slot number from the AVAILABLE SLOTS list.",
                },
                "attendee_emails": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional attendee emails (max 2). Optional.",
                },
            },
            "required": ["slot_number"],
        },
    },
]

OWNER_TOOLS = [
    {
        "name": "add_rule",
        "description": "Add a recurring or specific-date availability rule. Use 'day' for recurring weekly rules (e.g. 'monday') or 'date' for a specific date (e.g. '2026-02-20'). Each slot needs its own add_rule call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": "Day of week, lowercase English: monday, tuesday, wednesday, thursday, friday, saturday, sunday. Mutually exclusive with 'date'.",
                },
                "date": {
                    "type": "string",
                    "description": "Specific date in YYYY-MM-DD format. Mutually exclusive with 'day'.",
                },
                "start": {
                    "type": "string",
                    "description": "Start time in HH:MM format (24h).",
                },
                "end": {
                    "type": "string",
                    "description": "End time in HH:MM format (24h).",
                },
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "block_time",
        "description": "Block a recurring or specific-date time range (mark as unavailable). Guests cannot book during blocked times.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": "Day of week, lowercase English. Mutually exclusive with 'date'.",
                },
                "date": {
                    "type": "string",
                    "description": "Specific date YYYY-MM-DD. Mutually exclusive with 'day'.",
                },
                "start": {
                    "type": "string",
                    "description": "Start time HH:MM (24h).",
                },
                "end": {
                    "type": "string",
                    "description": "End time HH:MM (24h).",
                },
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "clear_rules",
        "description": "Clear all availability rules for a specific day of week or specific date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": "Day of week to clear, lowercase English.",
                },
                "date": {
                    "type": "string",
                    "description": "Specific date to clear (YYYY-MM-DD).",
                },
            },
        },
    },
    {
        "name": "clear_all",
        "description": "Clear ALL availability rules. Use when the owner wants to start completely fresh.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "show_rules",
        "description": "Show the current availability rules summary to the owner.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

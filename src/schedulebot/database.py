"""SQLite database for conversations, bookings, and availability rules."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .models import AvailabilityRule, Booking, Conversation, ConversationState, TimeSlot

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    sender_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'greeting',
    mode TEXT NOT NULL DEFAULT 'guest',
    guest_name TEXT DEFAULT '',
    guest_email TEXT DEFAULT '',
    guest_topic TEXT DEFAULT '',
    attendee_emails TEXT DEFAULT '[]',
    selected_slot_start TEXT,
    selected_slot_end TEXT,
    messages TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    guest_name TEXT NOT NULL,
    guest_channel TEXT NOT NULL,
    guest_sender_id TEXT NOT NULL,
    guest_email TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    attendee_emails TEXT DEFAULT '[]',
    slot_start TEXT NOT NULL,
    slot_end TEXT NOT NULL,
    calendar_event_id TEXT,
    meet_link TEXT,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS availability_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day_of_week TEXT DEFAULT '',
    specific_date TEXT DEFAULT '',
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    is_blocked INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

MIGRATIONS = [
    # Migration 1: Add guest_email, guest_topic, attendee_emails to conversations and bookings
    [
        "ALTER TABLE conversations ADD COLUMN guest_email TEXT DEFAULT ''",
        "ALTER TABLE conversations ADD COLUMN guest_topic TEXT DEFAULT ''",
        "ALTER TABLE conversations ADD COLUMN attendee_emails TEXT DEFAULT '[]'",
        "ALTER TABLE bookings ADD COLUMN guest_email TEXT DEFAULT ''",
        "ALTER TABLE bookings ADD COLUMN topic TEXT DEFAULT ''",
        "ALTER TABLE bookings ADD COLUMN attendee_emails TEXT DEFAULT '[]'",
    ],
    # Migration 2: Add guest_timezone to conversations
    [
        "ALTER TABLE conversations ADD COLUMN guest_timezone TEXT DEFAULT ''",
    ],
    # Migration 3: Add cancel_token and reminder_sent to bookings
    [
        "ALTER TABLE bookings ADD COLUMN cancel_token TEXT DEFAULT ''",
        "ALTER TABLE bookings ADD COLUMN reminder_sent INTEGER DEFAULT 0",
    ],
    # Migration 4: Indexes for cancel_token lookup and reminder query
    [
        "CREATE INDEX IF NOT EXISTS idx_bookings_cancel_token ON bookings(cancel_token) WHERE cancel_token != ''",
        "CREATE INDEX IF NOT EXISTS idx_bookings_reminder ON bookings(slot_start, reminder_sent) WHERE reminder_sent = 0",
    ],
]


class Database:
    def __init__(self, db_path: str | Path = "schedulebot.db"):
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DB_SCHEMA)
        self._run_migrations()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _run_migrations(self) -> None:
        """Run ALTER TABLE migrations for existing databases."""
        for migration_stmts in MIGRATIONS:
            for stmt in migration_stmts:
                try:
                    self._conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # Column already exists
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if not self._conn:
            self.connect()
        return self._conn

    # --- Conversations ---

    def get_conversation(self, sender_id: str) -> Conversation | None:
        row = self.conn.execute(
            "SELECT * FROM conversations WHERE sender_id = ?", (sender_id,)
        ).fetchone()
        if not row:
            return None
        selected_slot = None
        if row["selected_slot_start"] and row["selected_slot_end"]:
            selected_slot = TimeSlot(
                start=datetime.fromisoformat(row["selected_slot_start"]),
                end=datetime.fromisoformat(row["selected_slot_end"]),
            )
        return Conversation(
            sender_id=row["sender_id"],
            channel=row["channel"],
            state=ConversationState(row["state"]),
            guest_name=row["guest_name"],
            guest_email=row["guest_email"] or "",
            guest_topic=row["guest_topic"] or "",
            guest_timezone=row["guest_timezone"] if "guest_timezone" in row.keys() else "",
            attendee_emails=json.loads(row["attendee_emails"] or "[]"),
            selected_slot=selected_slot,
            messages=json.loads(row["messages"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def save_conversation(self, conv: Conversation) -> None:
        slot_start = conv.selected_slot.start.isoformat() if conv.selected_slot else None
        slot_end = conv.selected_slot.end.isoformat() if conv.selected_slot else None
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO conversations
                (sender_id, channel, state, mode, guest_name, guest_email, guest_topic,
                 guest_timezone, attendee_emails, selected_slot_start, selected_slot_end,
                 messages, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    conv.sender_id,
                    conv.channel,
                    conv.state.value,
                    getattr(conv, '_mode', 'guest'),
                    conv.guest_name,
                    conv.guest_email,
                    conv.guest_topic,
                    conv.guest_timezone,
                    json.dumps(conv.attendee_emails),
                    slot_start,
                    slot_end,
                    json.dumps(conv.messages),
                    conv.created_at.isoformat(),
                    conv.updated_at.isoformat(),
                ),
            )
            self.conn.commit()

    def delete_conversation(self, sender_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM conversations WHERE sender_id = ?", (sender_id,))
            self.conn.commit()

    def cleanup_stale_conversations(self, max_age_hours: int = 24) -> int:
        """Delete conversations older than max_age_hours. Returns count deleted."""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        with self._lock:
            cursor = self.conn.execute(
                "DELETE FROM conversations WHERE updated_at < ?",
                (cutoff.isoformat(),),
            )
            self.conn.commit()
            return cursor.rowcount

    # --- Bookings ---

    def save_booking(self, booking: Booking) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO bookings
                (id, guest_name, guest_channel, guest_sender_id, guest_email, topic,
                 attendee_emails, slot_start, slot_end,
                 calendar_event_id, meet_link, notes, cancel_token, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    booking.id,
                    booking.guest_name,
                    booking.guest_channel,
                    booking.guest_sender_id,
                    booking.guest_email,
                    booking.topic,
                    json.dumps(booking.attendee_emails),
                    booking.slot.start.isoformat(),
                    booking.slot.end.isoformat(),
                    booking.calendar_event_id,
                    booking.meet_link,
                    booking.notes,
                    booking.cancel_token,
                    booking.created_at.isoformat(),
                ),
            )
            self.conn.commit()

    def reserve_slot(self, start: datetime, end: datetime, booking_id: str) -> bool:
        """Atomically check + reserve a slot. Returns True if reserved, False if already taken."""
        with self._lock:
            try:
                self.conn.execute("BEGIN EXCLUSIVE")
                row = self.conn.execute(
                    "SELECT COUNT(*) as cnt FROM bookings WHERE slot_start < ? AND slot_end > ?",
                    (end.isoformat(), start.isoformat()),
                ).fetchone()
                if row["cnt"] > 0:
                    self.conn.execute("ROLLBACK")
                    return False
                # Insert placeholder booking to hold the slot
                self.conn.execute(
                    "INSERT INTO bookings (id, guest_name, guest_channel, guest_sender_id, "
                    "slot_start, slot_end, created_at) VALUES (?, '', '', '', ?, ?, ?)",
                    (booking_id, start.isoformat(), end.isoformat(), datetime.now().isoformat()),
                )
                self.conn.execute("COMMIT")
                return True
            except Exception:
                try:
                    self.conn.execute("ROLLBACK")
                except Exception:
                    pass
                return False

    def finalize_booking(self, booking: Booking) -> None:
        """Update a reserved (placeholder) booking with full details."""
        with self._lock:
            self.conn.execute(
                """UPDATE bookings SET guest_name=?, guest_channel=?, guest_sender_id=?,
                guest_email=?, topic=?, attendee_emails=?, calendar_event_id=?,
                meet_link=?, notes=?, cancel_token=? WHERE id=?""",
                (
                    booking.guest_name,
                    booking.guest_channel,
                    booking.guest_sender_id,
                    booking.guest_email,
                    booking.topic,
                    json.dumps(booking.attendee_emails),
                    booking.calendar_event_id,
                    booking.meet_link,
                    booking.notes,
                    booking.cancel_token,
                    booking.id,
                ),
            )
            self.conn.commit()

    def release_slot(self, booking_id: str) -> None:
        """Remove a reserved slot (e.g., if calendar creation failed)."""
        with self._lock:
            self.conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
            self.conn.commit()

    def _row_to_booking(self, row: sqlite3.Row) -> Booking:
        """Deserialize a database row into a Booking object."""
        keys = row.keys()
        return Booking(
            id=row["id"],
            guest_name=row["guest_name"],
            guest_channel=row["guest_channel"],
            guest_sender_id=row["guest_sender_id"],
            guest_email=row["guest_email"] or "",
            topic=row["topic"] or "",
            attendee_emails=json.loads(row["attendee_emails"] or "[]"),
            slot=TimeSlot(
                start=datetime.fromisoformat(row["slot_start"]),
                end=datetime.fromisoformat(row["slot_end"]),
            ),
            calendar_event_id=row["calendar_event_id"],
            meet_link=row["meet_link"],
            notes=row["notes"],
            cancel_token=row["cancel_token"] if "cancel_token" in keys else "",
            reminder_sent=bool(row["reminder_sent"]) if "reminder_sent" in keys else False,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_bookings(self, limit: int = 50) -> list[Booking]:
        rows = self.conn.execute(
            "SELECT * FROM bookings ORDER BY slot_start DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_booking(row) for row in rows]

    def get_booking_by_id(self, booking_id: str) -> Booking | None:
        """Get a single booking by ID."""
        row = self.conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_booking(row)

    def get_booking_by_cancel_token(self, cancel_token: str) -> Booking | None:
        """Get a booking by its cancel token."""
        if not cancel_token:
            return None
        row = self.conn.execute(
            "SELECT * FROM bookings WHERE cancel_token = ?", (cancel_token,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_booking(row)

    def get_upcoming_bookings_needing_reminder(
        self, after: datetime, before: datetime
    ) -> list[Booking]:
        """Get bookings starting between after and before that haven't been reminded."""
        rows = self.conn.execute(
            """SELECT * FROM bookings
            WHERE slot_start > ? AND slot_start <= ?
            AND reminder_sent = 0
            AND guest_name != ''""",
            (after.isoformat(), before.isoformat()),
        ).fetchall()
        return [self._row_to_booking(row) for row in rows]

    def mark_reminder_sent(self, booking_id: str) -> None:
        """Mark a booking as having its reminder sent."""
        with self._lock:
            self.conn.execute(
                "UPDATE bookings SET reminder_sent = 1 WHERE id = ?", (booking_id,)
            )
            self.conn.commit()

    def delete_booking(self, booking_id: str) -> bool:
        with self._lock:
            cursor = self.conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
            self.conn.commit()
            return cursor.rowcount > 0

    def is_slot_booked(self, start: datetime, end: datetime) -> bool:
        """Check if a time slot overlaps with any existing booking."""
        row = self.conn.execute(
            """SELECT COUNT(*) as cnt FROM bookings
            WHERE slot_start < ? AND slot_end > ?""",
            (end.isoformat(), start.isoformat()),
        ).fetchone()
        return row["cnt"] > 0

    # --- Availability Rules ---

    def get_availability_rules(self) -> list[AvailabilityRule]:
        rows = self.conn.execute(
            "SELECT * FROM availability_rules ORDER BY day_of_week, specific_date, start_time"
        ).fetchall()
        return [
            AvailabilityRule(
                id=row["id"],
                day_of_week=row["day_of_week"],
                specific_date=row["specific_date"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                is_blocked=bool(row["is_blocked"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def add_availability_rule(self, rule: AvailabilityRule) -> int:
        with self._lock:
            cursor = self.conn.execute(
                """INSERT INTO availability_rules
                (day_of_week, specific_date, start_time, end_time, is_blocked, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    rule.day_of_week,
                    rule.specific_date,
                    rule.start_time,
                    rule.end_time,
                    int(rule.is_blocked),
                    rule.created_at.isoformat(),
                ),
            )
            self.conn.commit()
            return cursor.lastrowid

    def delete_availability_rule(self, rule_id: int) -> bool:
        with self._lock:
            cursor = self.conn.execute(
                "DELETE FROM availability_rules WHERE id = ?", (rule_id,)
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def clear_availability_rules(self, day_of_week: str = "", specific_date: str = "") -> int:
        """Clear rules matching criteria. Empty string = don't filter by that field."""
        conditions = []
        params = []
        if day_of_week:
            conditions.append("day_of_week = ?")
            params.append(day_of_week)
        if specific_date:
            conditions.append("specific_date = ?")
            params.append(specific_date)
        with self._lock:
            if not conditions:
                cursor = self.conn.execute("DELETE FROM availability_rules")
            else:
                cursor = self.conn.execute(
                    f"DELETE FROM availability_rules WHERE {' AND '.join(conditions)}", params
                )
            self.conn.commit()
            return cursor.rowcount

    def format_availability_summary(self) -> str:
        """Human-readable summary of current availability rules."""
        rules = self.get_availability_rules()
        if not rules:
            return "No availability rules set. Tell me when you're available!"

        recurring = {}
        specific = {}
        for r in rules:
            if r.day_of_week:
                recurring.setdefault(r.day_of_week, []).append(r)
            elif r.specific_date:
                specific.setdefault(r.specific_date, []).append(r)

        lines = []
        if recurring:
            lines.append("Recurring schedule:")
            day_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            for day in day_order:
                if day in recurring:
                    slots = []
                    for r in recurring[day]:
                        prefix = "BLOCKED " if r.is_blocked else ""
                        slots.append(f"{prefix}{r.start_time}-{r.end_time}")
                    lines.append(f"  {day.capitalize()}: {', '.join(slots)}")

        if specific:
            lines.append("Specific dates:")
            for date in sorted(specific.keys()):
                slots = []
                for r in specific[date]:
                    prefix = "BLOCKED " if r.is_blocked else ""
                    slots.append(f"{prefix}{r.start_time}-{r.end_time}")
                lines.append(f"  {date}: {', '.join(slots)}")

        return "\n".join(lines)

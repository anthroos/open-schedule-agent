"""SQLite database for conversations and bookings."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Booking, Conversation, ConversationState, TimeSlot

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    sender_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'greeting',
    guest_name TEXT DEFAULT '',
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
    slot_start TEXT NOT NULL,
    slot_end TEXT NOT NULL,
    calendar_event_id TEXT,
    meet_link TEXT,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: str | Path = "schedulebot.db"):
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DB_SCHEMA)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if not self._conn:
            self.connect()
        return self._conn

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
            selected_slot=selected_slot,
            messages=json.loads(row["messages"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def save_conversation(self, conv: Conversation) -> None:
        slot_start = conv.selected_slot.start.isoformat() if conv.selected_slot else None
        slot_end = conv.selected_slot.end.isoformat() if conv.selected_slot else None
        self.conn.execute(
            """INSERT OR REPLACE INTO conversations
            (sender_id, channel, state, guest_name, selected_slot_start, selected_slot_end,
             messages, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conv.sender_id,
                conv.channel,
                conv.state.value,
                conv.guest_name,
                slot_start,
                slot_end,
                json.dumps(conv.messages),
                conv.created_at.isoformat(),
                conv.updated_at.isoformat(),
            ),
        )
        self.conn.commit()

    def delete_conversation(self, sender_id: str) -> None:
        self.conn.execute("DELETE FROM conversations WHERE sender_id = ?", (sender_id,))
        self.conn.commit()

    def save_booking(self, booking: Booking) -> None:
        self.conn.execute(
            """INSERT INTO bookings
            (id, guest_name, guest_channel, guest_sender_id, slot_start, slot_end,
             calendar_event_id, meet_link, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                booking.id,
                booking.guest_name,
                booking.guest_channel,
                booking.guest_sender_id,
                booking.slot.start.isoformat(),
                booking.slot.end.isoformat(),
                booking.calendar_event_id,
                booking.meet_link,
                booking.notes,
                booking.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    def get_bookings(self, limit: int = 50) -> list[Booking]:
        rows = self.conn.execute(
            "SELECT * FROM bookings ORDER BY slot_start DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Booking(
                id=row["id"],
                guest_name=row["guest_name"],
                guest_channel=row["guest_channel"],
                guest_sender_id=row["guest_sender_id"],
                slot=TimeSlot(
                    start=datetime.fromisoformat(row["slot_start"]),
                    end=datetime.fromisoformat(row["slot_end"]),
                ),
                calendar_event_id=row["calendar_event_id"],
                meet_link=row["meet_link"],
                notes=row["notes"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

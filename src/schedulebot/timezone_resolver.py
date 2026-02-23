"""Resolve city names and common timezone aliases to IANA timezone identifiers."""

from __future__ import annotations

from zoneinfo import ZoneInfo, available_timezones

# Common city/country → IANA timezone mapping
CITY_TO_TZ: dict[str, str] = {
    # Ukraine
    "kyiv": "Europe/Kyiv",
    "kiev": "Europe/Kyiv",
    "kharkiv": "Europe/Kyiv",
    "odesa": "Europe/Kyiv",
    "odessa": "Europe/Kyiv",
    "lviv": "Europe/Kyiv",
    "dnipro": "Europe/Kyiv",
    "zaporizhzhia": "Europe/Kyiv",
    "ukraine": "Europe/Kyiv",
    "ua": "Europe/Kyiv",
    # Russia
    "moscow": "Europe/Moscow",
    "st petersburg": "Europe/Moscow",
    "saint petersburg": "Europe/Moscow",
    # Europe
    "london": "Europe/London",
    "uk": "Europe/London",
    "paris": "Europe/Paris",
    "france": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "amsterdam": "Europe/Amsterdam",
    "netherlands": "Europe/Amsterdam",
    "warsaw": "Europe/Warsaw",
    "krakow": "Europe/Warsaw",
    "poland": "Europe/Warsaw",
    "prague": "Europe/Prague",
    "vienna": "Europe/Vienna",
    "rome": "Europe/Rome",
    "milan": "Europe/Rome",
    "italy": "Europe/Rome",
    "madrid": "Europe/Madrid",
    "spain": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "lisbon": "Europe/Lisbon",
    "portugal": "Europe/Lisbon",
    "zurich": "Europe/Zurich",
    "geneva": "Europe/Zurich",
    "switzerland": "Europe/Zurich",
    "istanbul": "Europe/Istanbul",
    "turkey": "Europe/Istanbul",
    "bucharest": "Europe/Bucharest",
    "romania": "Europe/Bucharest",
    "helsinki": "Europe/Helsinki",
    "finland": "Europe/Helsinki",
    "stockholm": "Europe/Stockholm",
    "sweden": "Europe/Stockholm",
    "oslo": "Europe/Oslo",
    "norway": "Europe/Oslo",
    "copenhagen": "Europe/Copenhagen",
    "denmark": "Europe/Copenhagen",
    "dublin": "Europe/Dublin",
    "ireland": "Europe/Dublin",
    "athens": "Europe/Athens",
    "greece": "Europe/Athens",
    "sofia": "Europe/Sofia",
    "bulgaria": "Europe/Sofia",
    # Americas
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "boston": "America/New_York",
    "miami": "America/New_York",
    "washington": "America/New_York",
    "chicago": "America/Chicago",
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "denver": "America/Denver",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "toronto": "America/Toronto",
    "canada": "America/Toronto",
    "vancouver": "America/Vancouver",
    "sao paulo": "America/Sao_Paulo",
    "brazil": "America/Sao_Paulo",
    "mexico city": "America/Mexico_City",
    "mexico": "America/Mexico_City",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires",
    # Asia
    "tokyo": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "seoul": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "shanghai": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "singapore": "Asia/Singapore",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "dubai": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "bangkok": "Asia/Bangkok",
    "thailand": "Asia/Bangkok",
    "jakarta": "Asia/Jakarta",
    "bali": "Asia/Makassar",
    "makassar": "Asia/Makassar",
    "tel aviv": "Asia/Jerusalem",
    "israel": "Asia/Jerusalem",
    "taipei": "Asia/Taipei",
    "taiwan": "Asia/Taipei",
    "hanoi": "Asia/Ho_Chi_Minh",
    "ho chi minh": "Asia/Ho_Chi_Minh",
    "vietnam": "Asia/Ho_Chi_Minh",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "malaysia": "Asia/Kuala_Lumpur",
    # Oceania
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "australia": "Australia/Sydney",
    "auckland": "Pacific/Auckland",
    "new zealand": "Pacific/Auckland",
    # Africa
    "cairo": "Africa/Cairo",
    "egypt": "Africa/Cairo",
    "johannesburg": "Africa/Johannesburg",
    "south africa": "Africa/Johannesburg",
    "nairobi": "Africa/Nairobi",
    "kenya": "Africa/Nairobi",
    "lagos": "Africa/Lagos",
    "nigeria": "Africa/Lagos",
    # Common abbreviations
    "est": "America/New_York",
    "cst": "America/Chicago",
    "mst": "America/Denver",
    "pst": "America/Los_Angeles",
    "gmt": "Europe/London",
    "cet": "Europe/Berlin",
    "eet": "Europe/Kyiv",
    "ist": "Asia/Kolkata",
    "jst": "Asia/Tokyo",
    "kst": "Asia/Seoul",
    "aest": "Australia/Sydney",
    "wita": "Asia/Makassar",
    "wib": "Asia/Jakarta",
}


def resolve_timezone(city_or_tz: str) -> str | None:
    """Resolve a city name, country, or timezone string to an IANA timezone.

    Returns the IANA timezone string or None if unrecognized.
    """
    if not city_or_tz:
        return None

    normalized = city_or_tz.strip().lower()

    # Exact lookup in city map first (catches abbreviations like EST, PST)
    if normalized in CITY_TO_TZ:
        return CITY_TO_TZ[normalized]

    # Direct IANA timezone (e.g. "Europe/Kyiv")
    if "/" in normalized:
        for tz in available_timezones():
            if tz.lower() == normalized:
                return tz

    # Partial match (e.g. "Kyiv, Ukraine" -> "kyiv")
    # Only match keys with 3+ chars to avoid false positives like "la" in "planet mars"
    for key, tz in CITY_TO_TZ.items():
        if len(key) >= 3 and key in normalized:
            return tz

    return None

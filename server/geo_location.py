"""Parse free-text profile locations into US state codes (US-XX).

Used by the Stage dashboard choropleth. No geocoding API — regex + name/abbr
lookup plus a small city→state table for common entries.
"""

from __future__ import annotations

import re
from typing import Optional

# Canonical two-letter codes (includes DC).
US_STATES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

_NAME_TO_CODE: dict[str, str] = {
    name.lower(): code for code, name in US_STATES.items()
}
# Alternate spellings / short forms.
_NAME_TO_CODE.update(
    {
        "district of columbia": "DC",
        "washington dc": "DC",
        "washington d.c.": "DC",
        "washington, dc": "DC",
        "wash dc": "DC",
    }
)

# Common city / metro strings → state (lowercase keys).
_CITY_TO_STATE: dict[str, str] = {
    "new york": "NY",
    "new york city": "NY",
    "nyc": "NY",
    "brooklyn": "NY",
    "manhattan": "NY",
    "queens": "NY",
    "los angeles": "CA",
    "san francisco": "CA",
    "san jose": "CA",
    "san diego": "CA",
    "oakland": "CA",
    "sacramento": "CA",
    "seattle": "WA",
    "bellevue": "WA",
    "portland": "OR",
    "chicago": "IL",
    "boston": "MA",
    "cambridge": "MA",
    "austin": "TX",
    "dallas": "TX",
    "houston": "TX",
    "san antonio": "TX",
    "denver": "CO",
    "boulder": "CO",
    "atlanta": "GA",
    "miami": "FL",
    "orlando": "FL",
    "tampa": "FL",
    "phoenix": "AZ",
    "scottsdale": "AZ",
    "philadelphia": "PA",
    "pittsburgh": "PA",
    "detroit": "MI",
    "minneapolis": "MN",
    "salt lake city": "UT",
    "las vegas": "NV",
    "nashville": "TN",
    "charlotte": "NC",
    "raleigh": "NC",
    "durham": "NC",
    "washington": "DC",
    "arlington": "VA",
    "alexandria": "VA",
    "mclean": "VA",
    "reston": "VA",
    "baltimore": "MD",
    "jersey city": "NJ",
    "hoboken": "NJ",
    "columbus": "OH",
    "cincinnati": "OH",
    "cleveland": "OH",
    "indianapolis": "IN",
    "kansas city": "MO",
    "st louis": "MO",
    "st. louis": "MO",
    "madison": "WI",
    "milwaukee": "WI",
}

_ABBR_RE = re.compile(
    r"(?:^|[\s,;/|(])([A-Za-z]{2})(?:\s*\d{5}(?:-\d{4})?)?(?:$|[\s,;/)|])"
)
_ZIP_ONLY_RE = re.compile(r"^\s*\d{5}(?:-\d{4})?\s*$")


def state_code_to_region(code: str) -> str:
    """jsVectorMap US region id, e.g. CA → US-CA."""
    return f"US-{code.upper()}"


def parse_us_state(location: str | None) -> Optional[str]:
    """Return two-letter US state code, or None if not mapped.

    Tries (in order): bare abbreviation, full state name, city table,
    trailing ``City, ST`` / ``City, State`` patterns.
    """
    if not location:
        return None
    raw = str(location).strip()
    if not raw or _ZIP_ONLY_RE.match(raw):
        return None

    # Explicit non-US / remote markers — skip early when whole string.
    lowered = raw.lower().strip()
    if lowered in {
        "remote",
        "n/a",
        "na",
        "none",
        "unknown",
        "worldwide",
        "global",
        "international",
    }:
        return None

    # Exact state name.
    if lowered in _NAME_TO_CODE:
        return _NAME_TO_CODE[lowered]

    # Exact city.
    if lowered in _CITY_TO_STATE:
        return _CITY_TO_STATE[lowered]

    # Bare two-letter code.
    if len(raw) == 2 and raw.upper() in US_STATES:
        return raw.upper()

    # "City, ST" or "City, State" or "… ST 94105"
    parts = re.split(r"[,;/|]+", raw)
    for part in reversed(parts):
        token = part.strip()
        if not token:
            continue
        # Drop trailing zip from this segment.
        token_noz = re.sub(r"\s+\d{5}(?:-\d{4})?\s*$", "", token).strip()
        tl = token_noz.lower()
        if tl in _NAME_TO_CODE:
            return _NAME_TO_CODE[tl]
        if tl in _CITY_TO_STATE:
            return _CITY_TO_STATE[tl]
        # Last word may be a state abbr: "San Francisco CA"
        words = token_noz.split()
        if words:
            last = words[-1].upper().rstrip(".")
            if last in US_STATES:
                return last
            # Multi-word state at end: "New York"
            for n in range(min(3, len(words)), 0, -1):
                phrase = " ".join(words[-n:]).lower()
                if phrase in _NAME_TO_CODE:
                    return _NAME_TO_CODE[phrase]
                if phrase in _CITY_TO_STATE:
                    return _CITY_TO_STATE[phrase]

    # Scan whole string for a state abbreviation bounded by separators.
    for m in _ABBR_RE.finditer(f" {raw} "):
        code = m.group(1).upper()
        if code in US_STATES:
            return code

    # Substring city match (e.g. "Greater Seattle Area").
    for city, code in _CITY_TO_STATE.items():
        if city in lowered:
            return code

    for name, code in _NAME_TO_CODE.items():
        if len(name) >= 5 and name in lowered:
            return code

    return None


def resolve_profile_state(
    location: str | None, university_location: str | None = None
) -> Optional[str]:
    """Prefer profile ``location``, fall back to ``university_location``."""
    return parse_us_state(location) or parse_us_state(university_location)

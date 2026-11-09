"""Unit tests for free-text US state parsing (dashboard geo)."""

from __future__ import annotations

from server.geo_location import (
    parse_us_state,
    resolve_profile_state,
    state_code_to_region,
)


def test_parse_abbreviation_and_name():
    assert parse_us_state("CA") == "CA"
    assert parse_us_state("California") == "CA"
    assert parse_us_state("new york") == "NY"
    assert parse_us_state("Washington, DC") == "DC"


def test_parse_city_state_patterns():
    assert parse_us_state("San Francisco, CA") == "CA"
    assert parse_us_state("Austin, Texas 78701") == "TX"
    assert parse_us_state("Seattle WA") == "WA"
    assert parse_us_state("Greater Seattle Area") == "WA"
    assert parse_us_state("NYC") == "NY"


def test_parse_remote_and_empty():
    assert parse_us_state(None) is None
    assert parse_us_state("") is None
    assert parse_us_state("Remote") is None
    assert parse_us_state("94105") is None


def test_resolve_falls_back_to_university():
    assert resolve_profile_state("", "Boston, MA") == "MA"
    assert resolve_profile_state("Remote", "Cambridge, Massachusetts") == "MA"
    assert resolve_profile_state("Chicago, IL", "Boston, MA") == "IL"


def test_region_id():
    assert state_code_to_region("ca") == "US-CA"

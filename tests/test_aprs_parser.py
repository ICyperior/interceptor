"""APRS packet parser regression tests."""

from __future__ import annotations

import pytest

from routes.aprs import parse_aprs_packet


_BASE_PACKET = "N0CALL-9>APRS,TCPIP*:@092345z4903.50N/07201.75W_090/000g005t077"


@pytest.mark.parametrize(
    "line",
    [
        _BASE_PACKET,
        f"[0.4] {_BASE_PACKET}",
        f"[0L] {_BASE_PACKET}",
        f"AFSK1200: {_BASE_PACKET}",
        f"AFSK1200: [0L] {_BASE_PACKET}",
    ],
)
def test_parse_aprs_packet_accepts_decoder_prefix_variants(line: str) -> None:
    packet = parse_aprs_packet(line)
    assert packet is not None
    assert packet["callsign"] == "N0CALL-9"
    assert packet["type"] == "aprs"

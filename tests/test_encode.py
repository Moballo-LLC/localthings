"""json_safe / from_json_safe: representing what CBOR decodes to and JSON
cannot hold.

The case that matters is `bytes` -- the usage history at
/file/transfer/vs/0 (issue #301) is a few KB of binary, and every path
that carries device data out of this integration ends at a JSON encoder.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from custom_components.localthings.registry.encode import (
    TYPE_KEY,
    from_json_safe,
    json_safe,
)


def _json_round_trip(value):
    """What HA's serializer would do with it. Any survivor of json_safe has
    to make it through this without raising."""
    return json.loads(json.dumps(value, allow_nan=False))


def test_plain_reps_pass_through_untouched():
    rep = {
        "x.com.samsung.da.cumulativePower": "5118585",
        "rt": ["x.com.samsung.file.list"],
        "n": 12,
        "ok": True,
        "missing": None,
        "ratio": 2.5,
    }
    assert json_safe(rep) == rep


def test_bytes_become_a_marker_that_json_accepts():
    blob = bytes(range(256)) * 4
    safe = json_safe({"x.com.samsung.blob": blob})
    marker = safe["x.com.samsung.blob"]

    assert marker[TYPE_KEY] == "bytes"
    assert marker["len"] == len(blob)
    assert marker["sha256"] == hashlib.sha256(blob).hexdigest()
    assert base64.b64decode(marker["base64"]) == blob
    assert "truncated" not in marker
    _json_round_trip(safe)


def test_bytes_round_trip_back_to_bytes():
    original = {"x.com.samsung.items": [{"x.com.samsung.blob": b"\x00\x01\x02usage"}]}
    # Through an actual JSON encode/decode, which is the trip a fixture makes.
    restored = from_json_safe(_json_round_trip(json_safe(original)))
    assert restored == original


def test_a_truncated_blob_keeps_honest_metadata_and_does_not_round_trip():
    blob = b"x" * 500
    marker = json_safe(blob, max_inline_bytes=10)

    assert marker["len"] == 500
    assert marker["sha256"] == hashlib.sha256(blob).hexdigest()
    assert marker["truncated"] is True
    assert base64.b64decode(marker["base64"]) == b"x" * 10
    # Refusing to rebuild a partial value is the point: a parser must never
    # be handed 10 bytes that claim to be 500.
    assert from_json_safe(marker) == marker


@pytest.mark.parametrize(
    "value",
    [
        b"raw",
        bytearray(b"raw"),
        memoryview(b"raw"),
    ],
)
def test_every_bytes_like_type_is_handled(value):
    assert from_json_safe(json_safe(value)) == b"raw"


def test_non_string_map_keys_are_coerced_and_marked():
    safe = json_safe({1: "a", "b": 2})
    assert safe["b"] == 2
    coerced = [k for k in safe if k != "b"]
    assert len(coerced) == 1
    assert coerced[0].startswith(TYPE_KEY)
    _json_round_trip(safe)


def test_sets_and_tuples_become_lists():
    assert json_safe(("a", "b")) == ["a", "b"]
    assert sorted(json_safe({"a", "b"})) == ["a", "b"]


def test_non_finite_floats_survive_as_markers():
    safe = json_safe({"a": float("nan"), "b": float("inf")})
    assert safe["a"][TYPE_KEY] == "float"
    assert safe["b"][TYPE_KEY] == "float"
    # allow_nan=False is what orjson does; a bare NaN would fail here.
    _json_round_trip(safe)


def test_a_cycle_terminates():
    rep: dict = {"href": "/x/vs/0"}
    rep["self"] = rep
    safe = json_safe(rep)
    assert safe["href"] == "/x/vs/0"
    assert safe["self"][TYPE_KEY] == "cycle"
    _json_round_trip(safe)


def test_an_unknown_type_is_described_rather_than_raising():
    class Opaque:
        def __repr__(self):
            return "<opaque>"

    safe = json_safe({"weird": Opaque()})
    assert safe["weird"] == {TYPE_KEY: "unrepresentable", "repr": "<opaque>"}
    _json_round_trip(safe)


def test_cbor_tags_keep_their_tag_number_and_payload():
    cbor2 = pytest.importorskip("cbor2")
    safe = json_safe(cbor2.CBORTag(42, b"inner"))
    assert safe[TYPE_KEY] == "tag"
    assert safe["tag"] == 42
    assert from_json_safe(safe["value"]) == b"inner"
    _json_round_trip(safe)


def test_from_json_safe_leaves_ordinary_data_alone():
    rep = {"a": [1, "two", {"three": None}]}
    assert from_json_safe(rep) == rep

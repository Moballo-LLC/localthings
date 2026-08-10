import cbor2

from custom_components.localthings.registry.identity import read_identity


class FakeSession:
    def __init__(self, table):
        self.table = table  # tuple(path) -> rep dict

    def get(self, path, timeout=10.0):
        rep = self.table.get(tuple(path))
        if rep is None:
            return 0x84, b""  # 4.04 not found
        return 0x45, cbor2.dumps(rep)


def test_read_identity_from_oic_p_and_d():
    sess = FakeSession(
        {
            ("oic", "p"): {"mnmn": "Samsung Electronics", "mnmo": "RF9000B"},
            ("oic", "d"): {"n": "Family Hub"},
        }
    )
    ident = read_identity(sess, serial="ABC123")
    assert ident.manufacturer == "Samsung Electronics"
    assert ident.model == "RF9000B"
    assert ident.name == "Family Hub"
    assert ident.serial == "ABC123"


def test_read_identity_tolerates_missing_resources():
    ident = read_identity(FakeSession({}), serial=None)
    assert ident.manufacturer == "Samsung"
    assert ident.model == ""
    assert ident.serial is None
    assert ident.device_types == ()
    assert ident.raw == {"/oic/p": {}, "/oic/d": {}, "/oic/res": []}


def test_read_identity_captures_oic_d_device_types():
    """/oic/d's `rt` is OCF's own device-type declaration -- captured so
    diagnostics can show whether real hardware populates it usefully."""
    sess = FakeSession(
        {
            ("oic", "d"): {
                "n": "Living Room AC",
                "rt": ["oic.wk.d", "oic.d.airconditioner"],
            },
        }
    )
    ident = read_identity(sess, serial=None)
    assert ident.device_types == ("oic.wk.d", "oic.d.airconditioner")


def test_read_identity_normalizes_scalar_and_malformed_rt():
    """Firmware that reports a bare string, or a non-list, must not explode."""
    assert read_identity(
        FakeSession({("oic", "d"): {"rt": "oic.d.refrigerator"}}), None
    ).device_types == ("oic.d.refrigerator",)
    assert read_identity(FakeSession({("oic", "d"): {"rt": 42}}), None).device_types == ()
    assert read_identity(
        FakeSession({("oic", "d"): {"rt": ["oic.wk.d", 7, None]}}), None
    ).device_types == ("oic.wk.d",)


def test_read_identity_keeps_raw_payloads_for_diagnostics():
    sess = FakeSession(
        {
            ("oic", "p"): {"mnmn": "Samsung Electronics", "mnmo": "RF9000B"},
            ("oic", "d"): {"n": "Family Hub", "di": "abc-123"},
        }
    )
    ident = read_identity(sess, serial=None)
    oic_p = ident.raw["/oic/p"]
    assert isinstance(oic_p, dict)
    assert oic_p["mnmo"] == "RF9000B"
    oic_d = ident.raw["/oic/d"]
    assert isinstance(oic_d, dict)
    assert oic_d["di"] == "abc-123"


def test_read_identity_captures_oic_res_links():
    """/oic/res is OCF's discovery endpoint. Real hardware (issue #177
    follow-up, a TP1X_REF_21K fridge dump) groups the response by `di`: one
    entry per logical Device, each carrying its own `links` array -- not a
    flat array of individually-`di`-tagged links. Every entry that dump
    returned had its discoverable policy bit set (`bm`'s bit 0); the whole
    x.com.samsung.da.* tree (including /device/0 itself) did not appear at
    all, meaning it's registered non-discoverable and simply invisible to
    this endpoint -- see _SPECULATIVE_DEVICE_INDICES' docstring for why that
    motivated probing /device/1 and /device/2 directly instead."""
    sess = FakeSession(
        {
            ("oic", "res"): [
                {
                    "di": "aaaa",
                    "links": [
                        {
                            "href": "/oic/d",
                            "rt": ["oic.wk.d", "oic.d.refrigerator"],
                            "p": {"bm": 1},
                        },
                        {"href": "/oic/sec/doxm", "rt": ["oic.r.doxm"], "p": {"bm": 1}},
                    ],
                },
            ],
        }
    )
    ident = read_identity(sess, serial=None)
    assert ident.raw["/oic/res"] == [
        {
            "di": "aaaa",
            "links": [
                {"href": "/oic/d", "rt": ["oic.wk.d", "oic.d.refrigerator"], "p": {"bm": 1}},
                {"href": "/oic/sec/doxm", "rt": ["oic.r.doxm"], "p": {"bm": 1}},
            ],
        },
    ]


def test_read_identity_tolerates_malformed_oic_res():
    """A single Property map instead of an array (or anything else
    non-list-shaped) must not explode -- same defensive posture as
    _device_types' handling of a malformed /oic/d rt."""
    ident = read_identity(FakeSession({("oic", "res"): {"not": "a list"}}), serial=None)
    assert ident.raw["/oic/res"] == []

"""Tests for Samsung water-purifier support (issue #90, TP2X_WATERPURIFIER_20K).

HA-free like the rest of the suite: exercises the registry, discovery/
flatten, and the write contracts.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc
from tests.conftest import _load_device


def _water_purifier():
    resources = _load_device("water_purifier")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _bound():
    reg, resources = _water_purifier()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_model_resolves_to_water_purifier_registry():
    reg, _ = _water_purifier()
    assert reg is not None and reg.name == "water_purifier"


def test_no_unbound_hrefs():
    """Every resource in the issue #90 dump binds or is covered -- clears
    the coverage-gap repair."""
    reg, resources = _water_purifier()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_state_keys_present():
    state = _state()
    for key in (
        "dispense_type",
        "hot_water_temperature",
        "dispense_capacity",
        "pouring",
        "waterpurifier_status",
        "filter_usage",
        "filter_status",
        "hotwater_lock",
        "coldwater_lock",
        "buzz_lock",
    ):
        assert key in state, key


def test_dispense_type_options_come_from_live_supported_types():
    desc = _desc("dispense_type")
    assert desc.options_field == "x.com.samsung.da.supportedTypes"


def test_dispense_type_write_contract():
    desc = _desc("dispense_type")
    path, body = desc.write_fn("hotwater", {})
    assert path == ["setting", "waterpurifier", "vs", "0"]
    assert body == {"x.com.samsung.da.desiredType": "hotwater"}


def test_hot_water_temperature_is_a_select_not_a_number():
    """Only a handful of discrete temperatures are selectable (not a
    continuous range) -- confirmed by supportedHotTemperatures being a short
    enumerated list, not a [min, max] range field."""
    desc = _desc("hot_water_temperature")
    assert desc.options_field == "x.com.samsung.da.supportedHotTemperatures"


def test_dispense_capacity_bounds_come_live_not_hardcoded():
    """Bounds and step come from the device's own desiredCapacityRange/
    capacityResolution fields, not a hardcoded constant -- see the
    adding-device-support skill's 'never hard-code the one dump's values'
    section."""
    desc = _desc("dispense_capacity")
    assert isinstance(desc, NumberDesc)
    assert desc.native_min is None
    assert desc.native_max is None
    assert desc.range_field == "x.com.samsung.da.desiredCapacityRange"
    rep = {
        "x.com.samsung.da.desiredCapacityRange": ["50", "2000"],
        "x.com.samsung.da.capacityResolution": "10",
    }
    assert desc.step_fn(rep) == 10


def test_dispense_capacity_write_contract():
    desc = _desc("dispense_capacity")
    path, body = desc.write_fn("550", {})
    assert path == ["setting", "waterpurifier", "vs", "0"]
    assert body == {"x.com.samsung.da.desiredCapacity": "550"}


def test_lock_switches_read_unlocked_as_off():
    state = _state()
    assert state["hotwater_lock"] is False
    assert state["coldwater_lock"] is False
    assert state["buzz_lock"] is False


def test_lock_switch_write_contracts():
    hot = _desc("hotwater_lock")
    cold = _desc("coldwater_lock")
    buzz = _desc("buzz_lock")
    assert hot.write_fn("On", {}) == (
        ["status", "lock", "vs", "0"],
        {"x.com.samsung.da.hotwaterLock": "Locked"},
    )
    assert cold.write_fn("Off", {}) == (
        ["status", "lock", "vs", "0"],
        {"x.com.samsung.da.coldwaterLock": "Unlocked"},
    )
    assert buzz.write_fn("On", {}) == (
        ["status", "lock", "vs", "0"],
        {"x.com.samsung.da.buzzLock": "Locked"},
    )


def test_favorite_capacity_options_come_from_live_capacity_list():
    desc = _desc("favorite_capacity")
    assert desc.options_field == "x.com.samsung.da.capacityList"


def test_sterilize_timestamps_parsed_as_utc():
    state = _state()
    assert state["sterilize_last_time"].tzinfo is not None
    assert state["sterilize_plan_time"].tzinfo is not None


def test_mode_hrefs_are_ignored_not_guessed():
    """/mode/vs/0's supportedModes carries a single opaque wizard token and
    modes reports an unrelated value not even in supportedModes -- internal
    plumbing, left unmodeled per the 'don't guess' rule rather than exposed
    as a nonsensical select."""
    from custom_components.localthings.registry.capabilities import water_purifier

    ignored_hrefs = {cap.href for cap in water_purifier.COVERAGE}
    assert "/mode/vs/0" in ignored_hrefs
    assert "/automation/waterpurifier/vs/0" in ignored_hrefs


# ---------------------------------------------------------------------------
# Coffee-capable variant (issue #107) -- /favorite/coffee/vs/0 and
# /favorite/hotwater/vs/0, not present in issue #90's original dump.
# ---------------------------------------------------------------------------


def _water_purifier_coffee():
    resources = _load_device("water_purifier_coffee")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _bound_coffee():
    reg, resources = _water_purifier_coffee()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state_coffee():
    bound, resources = _bound_coffee()
    return flatten(bound, resources)


def _desc_coffee(key):
    bound, _ = _bound_coffee()
    return next(b.desc for b in bound if b.desc.key == key)


def _desc_coffee_by_href(key, href):
    """Like _desc_coffee, but disambiguates descriptors that share a key
    across hrefs (hotwater_lock: LOCK's hotwaterLock field and
    FAVORITE_HOTWATER's switchHotwater fallback, issue #144)."""
    bound, _ = _bound_coffee()
    return next(b.desc for b in bound if b.desc.key == key and b.href == href)


def test_coffee_variant_no_unbound_hrefs():
    """Every resource in the issue #107 dump binds or is covered, including
    the four coffee-recipe hrefs not present in issue #90's dump."""
    reg, resources = _water_purifier_coffee()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_coffee_variant_expected_state_keys_present():
    state = _state_coffee()
    for key in (
        "favorite_coffee_enabled",
        "coffee_brew_status",
        "hotwater_lock",
        "favorite_hotwater_temperature",
    ):
        assert key in state, key


def test_favorite_coffee_write_contract():
    desc = _desc_coffee("favorite_coffee_enabled")
    assert desc.write_fn("On", {}) == (
        ["favorite", "coffee", "vs", "0"],
        {"favorite.activate": "On"},
    )
    assert desc.write_fn("Off", {}) == (
        ["favorite", "coffee", "vs", "0"],
        {"favorite.activate": "Off"},
    )


def test_favorite_hotwater_switch_is_a_lock_not_an_enable_flag():
    """issue #144: switchHotwater's value domain is Locked/Unlocked, not an
    enable flag, and it's misspelled as "Favorite hot water" -- it's the same
    hot-water lock as LOCK.hotwater_lock, just surfaced through this href on
    boards (like this fixture's) that don't populate /status/lock/vs/0's
    hotwaterLock field."""
    lock = _desc_coffee_by_href("hotwater_lock", "/favorite/hotwater/vs/0")
    assert lock.write_fn("On", {}) == (
        ["favorite", "hotwater", "vs", "0"],
        {"x.com.samsung.da.switchHotwater": "Locked"},
    )
    assert lock.write_fn("Off", {}) == (
        ["favorite", "hotwater", "vs", "0"],
        {"x.com.samsung.da.switchHotwater": "Unlocked"},
    )
    assert lock.value_fn("Unlocked") is False
    assert lock.value_fn("Locked") is True


def test_favorite_hotwater_lock_wins_in_flattened_state():
    """adapter.flatten() -- the actual source of coordinator.data every
    switch's is_on reads -- only honours exists_fn, never entity.py's
    implicit own-field-presence default. So it's not enough for the
    *registered* entity to resolve correctly (test_expected_state_keys_present
    territory); the shared 'hotwater_lock' key in the flattened dict itself
    must reflect the live switchHotwater value, not a stale phantom from
    LOCK's ungated hotwaterLock read (issue #144). This fixture's
    switchHotwater reads 'Unlocked'; without exists_fn on *both* sides of the
    pair, LOCK's descriptor computes None != 'Unlocked' == True regardless,
    and flatten() would pick whichever of the two entities happens to be
    processed last."""
    state = _state_coffee()
    assert state["hotwater_lock"] is False


def test_exactly_one_hotwater_lock_descriptor_exists_per_resource_state():
    """Both LOCK.hotwater_lock and FAVORITE_HOTWATER's switchHotwater
    fallback are always bound on this fixture (their hrefs are both always
    present) -- discrimination happens entirely in exists_fn. Exactly one of
    the two must ever pass, regardless of iteration order, or two switch
    entities would be registered with the same unique_id."""
    bound, resources = _bound_coffee()
    candidates = [b for b in bound if b.desc.key == "hotwater_lock"]
    assert len(candidates) == 2
    included = [b for b in candidates if b.desc.exists_fn(resources.get(b.href) or {}, resources)]
    assert len(included) == 1
    assert included[0].href == "/favorite/hotwater/vs/0"


def test_hotwater_lock_fallback_gating_across_status_lock_states():
    """The fallback (FAVORITE_HOTWATER's switchHotwater descriptor) must
    activate only once /status/lock/vs/0 is confirmed to lack hotwaterLock --
    never while that resource is an unfetched stub ({}), and never when it
    does carry the field. LOCK's own descriptor is the mirror image."""
    fallback = _desc_coffee_by_href("hotwater_lock", "/favorite/hotwater/vs/0")
    primary = _desc_coffee_by_href("hotwater_lock", "/status/lock/vs/0")
    own_rep = {"x.com.samsung.da.switchHotwater": "Unlocked"}

    # /status/lock/vs/0 absent entirely -- device genuinely lacks it.
    assert fallback.exists_fn(own_rep, {}) is True

    # /status/lock/vs/0 present but not yet fetched (a stub): outcome
    # pending, so the fallback must defer to LOCK rather than assume absence.
    stub_resources = {"/status/lock/vs/0": {}}
    assert fallback.exists_fn(own_rep, stub_resources) is False
    assert primary.exists_fn({}, stub_resources) is True

    # /status/lock/vs/0 fetched and confirmed to lack hotwaterLock (this
    # fixture's actual shape) -- the fallback wins.
    absent_resources = {"/status/lock/vs/0": {"x.com.samsung.da.coldwaterLock": "Unlocked"}}
    assert fallback.exists_fn(own_rep, absent_resources) is True
    assert primary.exists_fn(absent_resources["/status/lock/vs/0"], absent_resources) is False

    # /status/lock/vs/0 fetched and does carry hotwaterLock -- primary wins.
    present_resources = {"/status/lock/vs/0": {"x.com.samsung.da.hotwaterLock": "Unlocked"}}
    assert fallback.exists_fn(own_rep, present_resources) is False
    assert primary.exists_fn(present_resources["/status/lock/vs/0"], present_resources) is True

    # The fallback also re-asserts its own field, since it no longer gets
    # that check for free once it shares LOCK's key (issue #144 review).
    assert fallback.exists_fn({}, {}) is False


def test_favorite_hotwater_temperature_options_come_from_live_show_list():
    """`supportedList` is only the four fixed presets -- `showList` is a
    superset that also carries the one custom value (if any) the
    SmartThings app's "temperatures to display" editor let the user add,
    and always includes whatever the current default actually is. Reading
    `supportedList` meant a unit whose default *was* that custom value
    rendered as 'unknown' in HA (issue #196) -- see the ailite fixture's
    version of this test below for the case that actually exercises it
    (this fixture's showList == supportedList, no custom value set)."""
    desc = _desc_coffee("favorite_hotwater_temperature")
    assert desc.options_field == "x.com.samsung.da.favorite.showList"


def test_coffee_recipe_hrefs_are_ignored_not_guessed():
    """Static capability-advertisement blobs or empty resources -- no live
    'current recipe'/'current custom slot' field to expose, per the 'don't
    guess' rule."""
    from custom_components.localthings.registry.capabilities import water_purifier

    ignored_hrefs = {cap.href for cap in water_purifier.COVERAGE}
    for href in (
        "/brand/recipe/info/vs/0",
        "/coffee/custom/recipe/vs/0",
        "/recipe/coffee/vs/0",
        "/recipe/coffee/deletion/vs/0",
    ):
        assert href in ignored_hrefs, href


# ---------------------------------------------------------------------------
# AILITE_DA-REF-WATERPURIFIER board (issue #196, RWP70F15ANW) -- a
# coffee-capable variant on a different board family than issue #90/#107's
# TP2X_WATERPURIFIER_20K, whose modelNum's 'REF' token would otherwise
# misroute it to the refrigerator registry (see test_by_type.py's
# TestBoardTokenAmbiguity carve-out). Also the first dump to expose
# /cup/state/vs/0, /statistic/pour/vs/0, and the settings/sound/* trio on
# this device type.
# ---------------------------------------------------------------------------


def _water_purifier_ailite():
    resources = _load_device("water_purifier_ailite_25k")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _bound_ailite():
    reg, resources = _water_purifier_ailite()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state_ailite():
    bound, resources = _bound_ailite()
    return flatten(bound, resources)


def _desc_ailite(key):
    bound, _ = _bound_ailite()
    return next(b.desc for b in bound if b.desc.key == key)


def test_ailite_model_resolves_to_water_purifier_not_refrigerator():
    reg, _ = _water_purifier_ailite()
    assert reg is not None and reg.name == "water_purifier"


def test_ailite_no_unbound_hrefs():
    reg, resources = _water_purifier_ailite()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_ailite_expected_state_keys_present():
    state = _state_ailite()
    for key in (
        "cup_state",
        "sound_mode",
        "sound_output",
        "sound_volume",
        "alarm_in_mute",
        "last_pour_type",
        "last_pour_capacity",
    ):
        assert key in state, key


def test_ailite_hot_water_temperature_gated_off_without_supported_list():
    """This board reports tempDesiredHotWater but no
    supportedHotTemperatures (only a hotwaterRange/hotwaterLevel pair whose
    write contract isn't confirmed) -- the exact shape that used to make
    HA's select show 'unknown' (issue #196), since current_option isn't in
    an empty options list. The descriptor must gate off entirely rather than
    register with empty options."""
    state = _state_ailite()
    assert "hot_water_temperature" not in state


def test_ailite_favorite_hotwater_temperature_options_include_the_custom_value():
    """Issue #196's concrete failure case: the user added a custom 50C value
    via the SmartThings app's "temperatures to display" editor, so the
    board's defaultTemperature is now '50'. showList contains '50'
    ([40, 50, 75, 85, 90]) but supportedList does NOT (still the fixed
    [40, 75, 85, 90]). Reading options_field='x.com.samsung.da.favorite.supportedList'
    -- the old behavior -- would register a select whose options list
    doesn't contain the current default, so HA would render the entity as
    'unknown'. The descriptor must read from showList so '50' is in
    options."""
    desc = _desc_ailite("favorite_hotwater_temperature")
    assert desc.options_field == "x.com.samsung.da.favorite.showList"
    _, resources = _water_purifier_ailite()
    rep = resources["/favorite/hotwater/vs/0"]
    # defaultTemperature='50' must be a member of the field the descriptor
    # actually reads -- this is the precise assertion that would fail under
    # the old supportedList behavior.
    assert rep["x.com.samsung.da.favorite.defaultTemperature"] == "50"
    assert "50" in rep[desc.options_field]
    assert "50" not in rep["x.com.samsung.da.favorite.supportedList"]


def test_ailite_sound_mode_options_come_from_live_supported_modes():
    """This board's supportedModes (voice/fixedTone/mute) differs from both
    laundry.SOUND_MODE's hardcoded voice/tone/mute and air_purifier.SOUND_MODE's
    mute/buzzer -- reusing either would reject a value this device actually
    supports, per the oven._OVEN_MODES lesson from issue #138."""
    desc = _desc_ailite("sound_mode")
    assert desc.options_field == "supportedModes"
    assert desc.translation_key == "water_purifier_sound_mode"


def test_ailite_sound_volume_bounds_come_live_not_hardcoded():
    desc = _desc_ailite("sound_volume")
    assert isinstance(desc, NumberDesc)
    assert desc.native_min is None and desc.native_max is None
    rep = {"minLevel": "0", "maxLevel": "15", "resolution": "5"}
    assert desc.native_min_fn(rep) == 0
    assert desc.native_max_fn(rep) == 15
    assert desc.step_fn(rep) == 5


def test_ailite_alarm_in_mute_is_read_only():
    """No sibling field advertises alarmInMute as user-settable -- surfaced
    as a read-only diagnostic per the 'don't guess' rule rather than an
    invented switch."""
    from custom_components.localthings.registry.entities import BinarySensorDesc

    desc = _desc_ailite("alarm_in_mute")
    assert isinstance(desc, BinarySensorDesc)
    assert desc.value_fn("true") is True
    assert desc.value_fn("false") is False

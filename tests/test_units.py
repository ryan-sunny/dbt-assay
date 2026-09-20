"""*** THE MODEL NAMES THE UNIT. CODE DECIDES WHETHER THE NUMBERS CAN BE IT. ***

Verified against real warehouse values. Asked whether magnitudes were plausible, Jev passed
218,235 as "acres" at 0.82 and 4,073,925 as "acre-feet" at 0.54. Asked only what the NAME claims,
it scored 8/8 at confidence 1.00. The arithmetic moved to `range_conflicts`, where it is exact.
"""
from dbt_assay.feeds import UNIT_MAX, range_conflicts


def test_a_unit_confusion_of_orders_of_magnitude_is_caught_by_code():
    assert range_conflicts("acres", {"max_0": 218235.6, "min_0": 0.72}, 0)        # square feet
    assert range_conflicts("acre_feet", {"max_0": 4073925.0, "min_0": 1.0}, 0)    # gallons


def test_real_values_are_not_flagged():
    """Bounds exist to catch a 1000x mix-up, never a merely large parcel."""
    assert range_conflicts("acres", {"max_0": 5.51, "min_0": 0.72}, 0) is None
    assert range_conflicts("acre_feet", {"max_0": 88.0, "min_0": 1.75}, 0) is None
    assert range_conflicts("feet", {"max_0": 450.0, "min_0": 10.0}, 0) is None


def test_silence_means_not_checked_and_never_means_checked_and_fine():
    """The caller reports only when a REASON comes back, so an absent profile must return None
    rather than a pass. A check that cannot see must not read as one that did."""
    assert range_conflicts("acres", {}, 0) is None
    assert range_conflicts("acres", {"max_0": 1e9}, None) is None
    assert range_conflicts("no_unit_implied", {"max_0": 1e9}, 0) is None


def test_every_unit_option_the_model_can_pick_has_bounds_or_is_a_decline():
    """An option with no bounds silently never fires, which is the shape of every bug this
    codebase has had."""
    from dbt_assay.contracts import QUESTIONS
    opts = set(QUESTIONS["units_are_what_the_column_claims"]["criteria"])
    unbounded = opts - set(UNIT_MAX) - {"no_unit_implied", "cannot_tell"}
    assert not unbounded, unbounded

import pytest

from cubby.domain.duration import format_duration, parse_duration


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1m", 60.0),
        ("30s", 30.0),
        ("2h", 7200.0),
        ("1d", 86400.0),
        ("90", 90.0),
        (90, 90.0),
        (1.5, 1.5),
        ("  5 m ", 300.0),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("bad", ["", "abc", "1x", "m", "-5s"])
def test_parse_duration_rejects_garbage(bad):
    with pytest.raises((ValueError, TypeError)):
        parse_duration(bad)


def test_parse_duration_rejects_bool():
    with pytest.raises(TypeError):
        parse_duration(True)


@pytest.mark.parametrize(
    "seconds,expected",
    [(60, "1m"), (30, "30s"), (7200, "2h"), (86400, "1d"), (90, "90s")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected

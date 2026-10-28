import pytest
from sparq_analytics.weather.metar import Report, lowest_f, parse

LIVE = "METAR KNYC 270151Z AUTO VRB04KT 10SM CLR 18/07 A3018 RMK AO2 SLP211 T01780067 $"


@pytest.mark.parametrize(
    ("c10", "expected"),
    [
        (178, 64),  # 17.8 C is 64.04 F
        (172, 62),  # 17.2 C is 62.96 F: rounded down, never up
        (0, 32),
        (-56, 21),  # -5.6 C is 21.92 F
        (-178, -1),  # -17.8 C is -0.04 F
        (500, 122),
    ],
)
def test_lowest_f_rounds_down_to_a_whole_degree(c10: int, expected: int) -> None:
    assert lowest_f(c10) == expected


def test_the_tenths_remark_gives_the_exact_temperature() -> None:
    assert parse(LIVE) == Report(temp_c10=178, exact=True, max6_c10=None, precip_e2=None)


def test_a_negative_tenths_remark_and_one_without_the_dew_point() -> None:
    assert parse("KMSP 150151Z 10SM CLR M05/M12 A3020 RMK AO2 T10561122").temp_c10 == -56
    assert parse("KMSP 150151Z 10SM CLR M05/ A3020 RMK AO2 T1056").temp_c10 == -56


def test_without_the_remark_the_body_gives_the_lower_edge_of_its_whole_degree() -> None:
    assert parse("KXYZ 150151Z 00000KT 10SM CLR 18/07 A3018") == Report(175, False, None, None)
    assert parse("KXYZ 150151Z 00000KT 10SM CLR M05/M10 A3018 RMK AO2").temp_c10 == -55
    assert parse("KXYZ 150151Z 00000KT 10SM CLR M00/M02 A3018").temp_c10 == -5


def test_the_six_hour_maximum_and_the_hourly_rain() -> None:
    report = parse("KNYC 262351Z AUTO 25003KT 10SM CLR 18/07 RMK AO2 T01780067 10206 20178 P0003")
    assert (report.max6_c10, report.precip_e2) == (206, 3)
    assert parse("KXYZ 150551Z 10SM 01/M01 RMK AO2 11012").max6_c10 == -12


def test_a_trace_is_no_rain_and_a_missing_group_is_unknown() -> None:
    assert parse("KXYZ 150151Z 10SM 18/07 RMK AO2 P0000").precip_e2 == 0
    assert parse("KXYZ 150151Z 10SM 18/07 RMK AO2 P////").precip_e2 is None


def test_groups_outside_the_remarks_or_unreadable_are_never_guessed() -> None:
    # "10156" before RMK is not a six-hour maximum, "1////" is unreadable, and a report with
    # no temperature at all has none
    report = parse("KXYZ 150151Z 10156 10SM A3018 RMK AO2 1//// T////")
    assert report == Report(None, False, None, None)

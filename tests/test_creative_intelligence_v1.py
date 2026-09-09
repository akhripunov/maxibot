from app.ads.creative_intelligence import _f


def test_creative_intelligence_numeric_helper_is_bounded():
    assert _f(None) == 0.0
    assert _f("2.5") == 2.5


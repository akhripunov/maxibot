from app.bot.menu import new_landing_public_id


def test_new_landing_public_id_is_a_14_digit_public_identifier():
    public_id = new_landing_public_id()

    assert public_id.isdigit()
    assert len(public_id) == 14
    assert 10_000_000_000_000 <= int(public_id) <= 99_999_999_999_999

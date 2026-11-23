"""gab.game.base input helpers, offline."""
from gab.game import base as gbase


def test_button_accepts_name_or_number():
    assert gbase._button(1) == 1
    assert gbase._button("2") == 2
    assert gbase._button("left") == 1
    assert gbase._button("Right") == 3
    assert gbase._button("middle") == 2

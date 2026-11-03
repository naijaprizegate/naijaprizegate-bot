import pytest

from services.airtime_providers.vtu_ng import VTUNgProvider


def test_supplied_request_id_is_preserved():
    request_id = "NPG-12345"
    assert VTUNgProvider._make_request_id(request_id) == request_id


def test_missing_request_id_generates_one():
    request_id = VTUNgProvider._make_request_id()
    assert request_id.startswith("npg_")
    assert len(request_id) <= 50


@pytest.mark.parametrize("request_id", ["", "   "])
def test_blank_supplied_request_id_is_rejected(request_id):
    with pytest.raises(ValueError):
        VTUNgProvider._make_request_id(request_id)


def test_overlong_supplied_request_id_is_rejected():
    request_id = "N" * 51
    with pytest.raises(ValueError):
        VTUNgProvider._make_request_id(request_id)

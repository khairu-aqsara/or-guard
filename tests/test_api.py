import pytest

from or_guard import api


def test_timeout_becomes_api_error(monkeypatch):
    def slow(*args, **kwargs):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(api.urllib.request, "urlopen", slow)

    with pytest.raises(api.ApiError) as err:
        api.fetch_models()
    assert err.value.status == 0

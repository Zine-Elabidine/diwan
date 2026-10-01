import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own DIWAN_HOME: sessions never land in the real ~/.diwan."""
    monkeypatch.setenv("DIWAN_HOME", str(tmp_path / "home"))

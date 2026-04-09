"""Shared test configuration."""

import pytest


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Reset the in-memory rate-limit buckets before each test so
    rapid-fire auth calls from the test suite don't trigger 429s."""
    from teb.main import reset_rate_limits
    reset_rate_limits()

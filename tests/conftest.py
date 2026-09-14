"""Fixtures for MowgliNext tests."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for every test in this suite.

    `enable_custom_integrations` is provided by
    pytest-homeassistant-custom-component's own conftest.
    """
    yield

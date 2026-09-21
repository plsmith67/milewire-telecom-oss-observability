"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        # Ensure clean slate between tests
        test_client.delete("/failures")
        yield test_client
        test_client.delete("/failures")

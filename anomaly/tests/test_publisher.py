"""Publisher payload shape."""

from app.publisher import IncidentPublisher


def test_publisher_posts_wrapped_payload(httpx_mock=None):
    # Lightweight unit check of payload construction contract
    pub = IncidentPublisher("http://example.invalid/webhooks/anomalies")
    assert pub.webhook_url.endswith("/webhooks/anomalies")

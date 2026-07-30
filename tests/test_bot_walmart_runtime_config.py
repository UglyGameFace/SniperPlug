from sniperplug.bot import walmart_affiliate_config
from sniperplug.config import Settings


def test_walmart_runtime_config_uses_loaded_settings() -> None:
    settings = Settings(
        discord_token="test-token",
        walmart_consumer_id="consumer-123",
        walmart_key_version="7",
        walmart_private_key_b64="private-key-data",
        walmart_publisher_id="publisher-456",
        walmart_provider_enabled=True,
    )

    config = walmart_affiliate_config(settings)

    assert config.enabled is True
    assert config.configured is True
    assert config.consumer_id == "consumer-123"
    assert config.key_version == "7"
    assert config.private_key_b64 == "private-key-data"
    assert config.publisher_id == "publisher-456"


def test_walmart_runtime_config_stays_disabled_without_enable_flag() -> None:
    settings = Settings(
        discord_token="test-token",
        walmart_consumer_id="consumer-123",
        walmart_private_key_b64="private-key-data",
        walmart_provider_enabled=False,
    )

    config = walmart_affiliate_config(settings)

    assert config.enabled is False
    assert config.configured is False

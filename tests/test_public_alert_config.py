from sniperplug.services.public_alert_config import decode_channel_id, encode_channel_id


def test_decode_channel_id_accepts_canonical_and_raw_values():
    assert decode_channel_id("ch:123") == 123
    assert decode_channel_id("123") == 123


def test_encode_channel_id_canonicalizes_raw_value():
    assert encode_channel_id("456") == "ch:456"


def test_decode_channel_id_rejects_bad_values():
    assert decode_channel_id(None) is None
    assert decode_channel_id("") is None
    assert decode_channel_id("not-a-channel") is None

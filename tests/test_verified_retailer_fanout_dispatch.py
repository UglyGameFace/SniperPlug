from sniperplug.services.verified_retailer_event_fanout import retailer_fanout_handler


def test_verified_retailer_fanout_dispatches_hp_and_target() -> None:
    assert retailer_fanout_handler("hp").label == "HP"
    assert retailer_fanout_handler("target").label == "Target"
    assert retailer_fanout_handler("unknown") is None

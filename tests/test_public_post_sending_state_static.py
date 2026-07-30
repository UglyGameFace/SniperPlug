from pathlib import Path


def test_public_post_persists_sending_state_before_discord_delivery():
    source = Path("sniperplug/services/public_deal_posts.py").read_text()
    send_state = source.index("await mark_public_deal_sending(")
    discord_send = source.index("message = await channel.send", send_state)
    assert send_state < discord_send
    assert "status = 'sending'" in source
    assert "status IN ('reserved', 'sending')" in source
    assert "timedelta(days=30)" in source
    assert "public post reservation could not be confirmed as sending" in source

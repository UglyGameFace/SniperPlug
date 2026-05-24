from sniperplug.config import parse_guild_ids


def test_parse_guild_ids_accepts_multiple_separators():
    assert parse_guild_ids("123, 456;789") == (123, 456, 789)


def test_parse_guild_ids_ignores_invalid_values_and_duplicates():
    assert parse_guild_ids("123, abc, 123, 456") == (123, 456)

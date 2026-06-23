import asyncio

from sniperplug.services.public_deal_posts import reserve_public_deal_post


class NoRowcountCursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class FakeNoRowcountConnection:
    def __init__(self):
        self.rows = []
        self.commits = 0

    async def execute(self, sql, params=None):
        params = tuple(params or ())
        if "INSERT OR IGNORE INTO guild_public_deal_posts" in sql:
            guild_id, deal_key, retailer, source_label, first_seen_at = params
            exists = any(row["guild_id"] == guild_id and row["deal_key"] == deal_key for row in self.rows)
            if not exists:
                self.rows.append(
                    {
                        "guild_id": guild_id,
                        "deal_key": deal_key,
                        "retailer": retailer,
                        "source_label": source_label,
                        "status": "reserved",
                        "first_seen_at": first_seen_at,
                    }
                )
            return NoRowcountCursor()
        if "SELECT 1 FROM guild_public_deal_posts" in sql:
            guild_id, deal_key, first_seen_at = params
            found = any(
                row["guild_id"] == guild_id
                and row["deal_key"] == deal_key
                and row["status"] == "reserved"
                and row["first_seen_at"] == first_seen_at
                for row in self.rows
            )
            return NoRowcountCursor((1,) if found else None)
        return NoRowcountCursor()

    async def commit(self):
        self.commits += 1


class FakeDb:
    def __init__(self):
        self.conn = FakeNoRowcountConnection()

    def require_conn(self):
        return self.conn


def test_reserve_public_deal_post_works_without_cursor_rowcount():
    db = FakeDb()

    reserved = asyncio.run(
        reserve_public_deal_post(
            db,
            guild_id=123,
            retailer="walmart",
            deal_key="walmart:sku123:price:9.99",
            source_label="autoscan:test",
        )
    )

    assert reserved is True
    assert len(db.conn.rows) == 1

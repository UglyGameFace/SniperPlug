from __future__ import annotations

from typing import Any, Iterable

from sniperplug.hp_watcher.storage import PRODUCT_TABLE, ensure_hp_watcher_tables


async def load_product_promotion_texts(
    db: Any,
    product_keys: Iterable[str],
) -> dict[str, str]:
    """Load current promotion text for a claimed HP offer batch in one query."""

    await ensure_hp_watcher_tables(db)
    keys = tuple(dict.fromkeys(str(value or "").strip() for value in product_keys if str(value or "").strip()))
    if not keys:
        return {}
    conn = db.require_conn()
    placeholders = ",".join("?" for _ in keys)
    cursor = await conn.execute(
        f"SELECT product_key, promotion_text FROM {PRODUCT_TABLE} WHERE product_key IN ({placeholders})",
        keys,
    )
    result: dict[str, str] = {}
    for row in await cursor.fetchall():
        product_key = str(_row_get(row, "product_key", 0) or "")
        if product_key:
            result[product_key] = str(_row_get(row, "promotion_text", 1) or "")
    return result


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)

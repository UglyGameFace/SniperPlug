"""SniperPlug-Reader: the standalone picture-reading app.

A separate Discloud application (512 MB) that runs the OCR engine
the main bot's 256 MB container provably cannot hold, reading DM'd
cart/wishlist screenshots for the DM list intake. It has no Discord
connection; its only link to the bot is the shared production Turso
database and the job queue in ``sniperplug.services.dm_ocr_jobs``.
"""

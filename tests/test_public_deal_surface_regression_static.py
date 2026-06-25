from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_open_box_command_and_routes_exist():
    source = read("sniperplug/cogs/open_box_deals.py")
    assert '@app_commands.command(name="open_box_deals"' in source
    for query in (
        "open box vacuum",
        "open box electronics",
        "restored vacuum",
        "refurbished vacuum",
        "like new vacuum",
        "open box appliance",
        "restored electronics",
        "open box home",
        "open box gaming",
        "open box monitor",
        "open box laptop",
    ):
        assert query in source


def test_open_box_cog_and_autoscan_routes_are_loaded():
    bot = read("sniperplug/bot.py")
    routes = read("sniperplug/services/open_box_autoscan_routes.py")
    assert "from sniperplug.cogs.open_box_deals import OpenBoxDealsCog" in bot
    assert "await self.add_cog(OpenBoxDealsCog(self))" in bot
    assert "install_open_box_autoscan_routes()" in bot
    assert "OPEN_BOX_AUTOSCAN_QUERIES" in routes
    assert "AUTO_SCAN_CATEGORY_ROTATION" in routes


def test_existing_deal_and_autoscan_commands_still_exist():
    scanner = read("sniperplug/cogs/deal_scanner.py")
    autoscan = read("sniperplug/cogs/auto_scan_runner.py")
    assert '@app_commands.command(name="deals"' in scanner
    assert '@app_commands.command(name="hunt"' in scanner
    assert '@app_commands.command(name="walmart_cash"' in scanner
    assert '@app_commands.command(name="walmart_scan"' in scanner
    assert '@app_commands.command(name="autoscan_now"' in autoscan


def test_home_depot_penny_and_verizon_surfaces_still_exist():
    home_depot_local = read("sniperplug/cogs/home_depot_local.py")
    home_depot_search = read("sniperplug/cogs/home_depot_search.py")
    verizon = read("sniperplug/cogs/verizon_shine.py")
    assert "penny" in home_depot_local.lower() or "penny" in home_depot_search.lower()
    assert "home_depot" in home_depot_local.lower() or "home depot" in home_depot_local.lower()
    assert "verizon" in verizon.lower()
    assert "shine" in verizon.lower()

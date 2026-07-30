from __future__ import annotations

from pathlib import Path


def main() -> None:
    for name in (
        "sniperplug/services/walmart_cash_api_truth.py",
        "sniperplug/services/walmart_pdp_cash_proof.py",
    ):
        path = Path(name)
        lines = path.read_text().splitlines(keepends=True)
        kept = [line for line in lines if 'r"walmart\\s+cash[^' not in line]
        if len(kept) == len(lines):
            raise SystemExit(f"unsafe reverse Cash amount pattern missing in {name}")
        path.write_text("".join(kept))

    pdp = Path("sniperplug/services/walmart_pdp_cash_proof.py")
    text = pdp.read_text()
    nearest_money = '''    money_matches = list(re.finditer(r"\\$\\s*(\\d+(?:\\.\\d{1,2})?)", raw))
    nearby_money = [match for match in money_matches if abs(match.start() - cash_index) <= 90]
    if nearby_money:
        best = min(nearby_money, key=lambda match: abs(match.start() - cash_index))
        return _float_or_none(best.group(1))

'''
    if nearest_money not in text:
        raise SystemExit("PDP nearest-dollar shortcut missing")
    pdp.write_text(text.replace(nearest_money, "", 1))


if __name__ == "__main__":
    main()

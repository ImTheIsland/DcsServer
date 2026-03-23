"""
Inventory data importer.

Reads product records from a CSV file. Provides a clean separation between
the data source and population logic — if the source ever changes (e.g. to a
REST API), only this module needs updating.

Expected CSV columns: VEND_C, DCS_CODE, DESCRIPTION1
"""
import csv
from pathlib import Path
from typing import Iterator

REQUIRED_COLUMNS = {"vendor_code", "dcs_code", "item_name"}


def iter_products(csv_path: str) -> Iterator[dict]:
    """
    Yield product records from a CSV file.

    Each yielded dict has keys:
        vendor_code : str  — vendor identifier
        dcs_raw     : str  — DCS code as exported (trailing spaces may be stripped)
        item_name   : str  — product description / name
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Product file not found: {csv_path}")

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}")

        for row in reader:
            vendor_code = row["vendor_code"].strip()
            dcs_raw = row["dcs_code"].strip()
            item_name = row["item_name"].strip()

            if not vendor_code or not dcs_raw or not item_name:
                continue

            yield {
                "vendor_code": vendor_code,
                "dcs_raw": dcs_raw,
                "item_name": item_name,
            }

"""
Database initialization / update script.

Run directly to populate or update the database:

    python dcs_lookup/init/populate_db.py
    python -m dcs_lookup.init.populate_db

Edit the paths in the __main__ block at the bottom and call whichever
function(s) you need.
"""
import logging
import sys
from pathlib import Path

# Allow running directly (python populate_db.py) or as a module (-m dcs_lookup.init.populate_db)
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from dcs_lookup.db.database import SessionLocal, engine
from dcs_lookup.db.models import Base, DcsCode, Keyword, Vendor, VendorDcs
from dcs_lookup.init.importer import iter_products
from dcs_lookup.init.keyword_processor import extract_keywords

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_PERSON_TYPES = {"MEN", "WMN", "UNI", "BOY", "GRL", "YTH", "KID"}


def derive_person(c: str | None, s: str | None) -> str | None:
    """Return the first letter of the person-type component (S checked first, then C)."""
    for val in [s, c]:
        if val and val.strip() in _PERSON_TYPES:
            return val.strip()[0]
    return None


def init_db():
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(bind=engine)


def parse_dcs_components(raw_dcs: str) -> tuple[str, str, str | None, str | None]:
    """
    Parse a raw DCS code into (full_dcs, d, c, s) by splitting at fixed 3-char positions.

    No modification is made to the received value — what comes in is what gets stored.

    Examples:
        "FW CLMMEN"  (9 chars) → d="FW ", c="CLM", s="MEN"
        "FWMENCLI"   (8 chars) → d="FWM", c="ENC", s="LI"
        "BIKACS"     (6 chars) → d="BIK", c="ACS", s=None
        "BIK"        (3 chars) → d="BIK", c=None,  s=None
    """
    raw = raw_dcs.strip()
    if not raw:
        raise ValueError("Empty DCS code.")

    d = raw[0:3]
    c = raw[3:6] if len(raw) > 3 else None
    s = raw[6:9] if len(raw) > 6 else None
    full_dcs = d + (c or "") + (s or "")

    return full_dcs, d, c, s


def _load_canonical_df(canonical_path: str) -> pd.DataFrame:
    """
    Read the canonical DCS data from the "Normalized" sheet of the DCS List Excel file.

    Sheet layout written by DcsGsParser.write_normalized_sheet():
        Row 1: timestamp (skipped)
        Row 2: headers — DCS, D, C, S, Type, Description
        Row 3+: data
    """
    suffix = Path(canonical_path).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(
            canonical_path,
            sheet_name="Normalized",
            header=1,       # row index 1 (0-based) = the header row
            dtype=str,
        ).fillna("")
    elif suffix == ".csv":
        df = pd.read_csv(canonical_path, dtype=str).fillna("")
    else:
        raise ValueError(f"Unsupported canonical file type: {suffix}")

    if df is None or df.empty:
        logger.error("Canonical file returned no data — aborting.")
        sys.exit(1)

    return df


def load_canonical(canonical_path: str) -> None:
    """
    Insert/update dcs_codes from a canonical DCS file (.xlsx or .csv) and
    extract canonical keywords (source="canonical").

    Safe to re-run — all operations are upserts.
    """
    logger.info(f"Loading canonical DCS codes from: {canonical_path}")
    df = _load_canonical_df(canonical_path)
    logger.info(f"  {len(df)} rows in canonical file.")

    db = SessionLocal()
    try:
        inserted = 0
        for _, row in df.iterrows():
            raw_dcs = str(row.get("DCS", "")).strip()
            if not raw_dcs:
                continue

            d = str(row.get("D", "")).ljust(3)[:3]
            c = str(row.get("C", "")).ljust(3)[:3] if pd.notna(row.get("C")) else None
            s = str(row.get("S", "")).ljust(3)[:3] if pd.notna(row.get("S")) else None
            type_val = str(row.get("Type", "")).strip() or None
            desc_val = str(row.get("Description", "")).strip() or None

            full_dcs = d + (c or "") + (s or "")
            person = derive_person(c, s)

            db.execute(
                sqlite_insert(DcsCode)
                .values(dcs=full_dcs, d=d, c=c, s=s, type=type_val, description=desc_val, person=person, is_current=True)
                .on_conflict_do_update(
                    index_elements=["dcs"],
                    set_={"d": d, "c": c, "s": s, "type": type_val, "description": desc_val, "person": person, "is_current": True},
                )
            )
            inserted += 1

            canonical_text = " ".join(filter(None, [type_val, desc_val]))
            for kw in extract_keywords(canonical_text):
                db.execute(
                    sqlite_insert(Keyword)
                    .values(keyword=kw, dcs=full_dcs, weight=1, source="canonical")
                    .on_conflict_do_update(
                        index_elements=["keyword", "dcs"],
                        set_={"weight": Keyword.__table__.c.weight + 1},
                    )
                )

        db.commit()
        logger.info(f"  Canonical load complete — {inserted} codes upserted.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def process_products(products_path: str) -> None:
    """
    Process product records from a CSV, upserting vendors, vendor_dcs, and keywords.

    Safe to run multiple times — all operations are upserts with incrementing counts.
    Canonical keyword source values are never overwritten by product imports.
    """
    logger.info(f"Processing products from: {products_path}")

    db = SessionLocal()
    try:
        count = 0
        skipped = 0

        for product in iter_products(products_path):
            try:
                full_dcs, d, c, s = parse_dcs_components(product["dcs_raw"])
            except ValueError as e:
                logger.warning(f"Skipping row — {e}: {product}")
                skipped += 1
                continue

            vendor_code = product["vendor_code"]
            item_name = product["item_name"]

            # Ensure DCS code exists; insert as non-current if not in canonical list
            db.execute(
                sqlite_insert(DcsCode)
                .values(dcs=full_dcs, d=d, c=c, s=s, type=None, description=None, person=derive_person(c, s), is_current=False)
                .on_conflict_do_nothing()
            )

            db.execute(
                sqlite_insert(Vendor)
                .values(vendor_code=vendor_code)
                .on_conflict_do_nothing()
            )

            db.execute(
                sqlite_insert(VendorDcs)
                .values(vendor_code=vendor_code, dcs=full_dcs, product_count=1)
                .on_conflict_do_update(
                    index_elements=["vendor_code", "dcs"],
                    set_={"product_count": VendorDcs.__table__.c.product_count + 1},
                )
            )

            for kw in extract_keywords(item_name):
                db.execute(
                    sqlite_insert(Keyword)
                    .values(keyword=kw, dcs=full_dcs, weight=1, source="product")
                    .on_conflict_do_update(
                        index_elements=["keyword", "dcs"],
                        set_={"weight": Keyword.__table__.c.weight + 1},
                    )
                )

            count += 1
            if count % 1000 == 0:
                db.commit()
                logger.info(f"  {count} products processed...")

        db.commit()
        logger.info(f"  Products complete — {count} processed, {skipped} skipped.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def full_refresh(canonical_path: str, products_path: str | None = None) -> None:
    """
    Wipe and rebuild all tables except user_keywords, then reload from source files.

    Drop order respects foreign keys: keywords → vendor_dcs → vendors → dcs_codes
    """
    logger.info("Starting full refresh...")

    for table in [Keyword.__table__, VendorDcs.__table__, Vendor.__table__, DcsCode.__table__]:
        table.drop(bind=engine, checkfirst=True)
    Base.metadata.create_all(bind=engine)
    logger.info("Tables reset (user_keywords preserved).")

    load_canonical(canonical_path)

    if products_path:
        process_products(products_path)
    else:
        logger.info("No products file provided — skipping product import.")

    logger.info("Full refresh complete.")


def incremental_update(products_path: str) -> None:
    """
    Additive update from a product CSV.
    Does not remove or recalculate any existing data.
    """
    logger.info(f"Starting incremental update from: {products_path}")
    init_db()
    process_products(products_path)
    logger.info("Incremental update complete.")


if __name__ == "__main__":
    canonical_path = r'C:\Shared Drive\GearHeads\Departments\DCS List.xlsx'
    products_path  = r'C:\Shared Drive\GearHeads\Departments\export_vc-dcs-name.csv'

    # --- Full rebuild (wipes and reloads everything except user_keywords) ---
    full_refresh(canonical_path, products_path)

    # --- Individual steps (additive/upsert only — does NOT purge old data) ---
    # init_db()
    # load_canonical(canonical_path)
    # process_products(products_path)

    # --- Incremental update from a new product file ---
    # incremental_update("data/new_products.csv")

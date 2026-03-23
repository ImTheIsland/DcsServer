"""
Syncs dcs_codes table from the DCS List Excel file.

Run directly to populate or update the dcs_codes table:

    python dcs_lookup/init/populate_db_from_excel.py
    python -m dcs_lookup.init.populate_db_from_excel

Edit the path in the __main__ block at the bottom before running.
"""
import logging
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from dcs_lookup.db.database import SessionLocal, engine
from dcs_lookup.db.models import Base, DcsCode
from dcs_lookup.init.dcs_parser import DcsGsParser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_PERSON_TYPES = {"MEN", "WMN", "UNI", "BOY", "GRL", "YTH", "KID"}


def derive_person(c, s) -> str | None:
    """Return the first letter of the person-type component (S checked first, then C)."""
    for val in [s, c]:
        if val and str(val).strip() in _PERSON_TYPES:
            return str(val).strip()[0]
    return None


def init_db():
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(bind=engine)


def sync_dcs_codes(excel_path: str) -> dict:
    """
    Parse the Excel file and sync the dcs_codes table.

    - Codes present in the file → upserted with is_current=True
    - Codes previously in DB but absent from the file → marked is_current=False

    Returns a summary dict with counts.
    """
    logger.info(f"Parsing DCS codes from: {excel_path}")
    parser = DcsGsParser(excel_path)
    df = parser.result_df

    if df is None or df.empty:
        logger.error("Parser returned no data — aborting.")
        sys.exit(1)

    logger.info(f"Parsed {len(df)} DCS codes from Excel file.")

    db = SessionLocal()
    try:
        incoming_dcs_set = set(df["DCS"].str.strip())

        upserted = 0
        for _, row in df.iterrows():
            dcs_val = row["DCS"].strip()
            c_val = row["C"]
            s_val = row["S"]
            person = derive_person(c_val, s_val)
            stmt = (
                sqlite_insert(DcsCode)
                .values(
                    dcs=dcs_val,
                    d=row["D"],
                    c=c_val,
                    s=s_val,
                    type=row["Type"].strip() or None,
                    description=row["Description"].strip() or None,
                    person=person,
                    is_current=True,
                )
                .on_conflict_do_update(
                    index_elements=["dcs"],
                    set_={
                        "d": row["D"],
                        "c": c_val,
                        "s": s_val,
                        "type": row["Type"].strip() or None,
                        "description": row["Description"].strip() or None,
                        "person": person,
                        "is_current": True,
                    },
                )
            )
            db.execute(stmt)
            upserted += 1

        retired = (
            db.query(DcsCode)
            .filter(
                DcsCode.is_current == True,
                DcsCode.dcs.notin_(incoming_dcs_set),
            )
            .all()
        )
        for code in retired:
            code.is_current = False

        db.commit()

        summary = {
            "parsed": len(df),
            "upserted": upserted,
            "retired": len(retired),
        }
        logger.info(
            f"Sync complete — upserted: {upserted}, retired (marked non-current): {len(retired)}"
        )
        return summary

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    file_path = r'C:\Shared Drive\GearHeads\Departments\DCS List.xlsx'
    sync_dcs_codes(file_path)

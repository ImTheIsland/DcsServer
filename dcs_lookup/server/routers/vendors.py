from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db.database import get_db
from ...db.models import DcsCode, VendorDcs
from ..schemas import DCSResult

router = APIRouter(prefix="/vendors", tags=["vendors"])


@router.get("/{vendor_code}/dcs", response_model=list[DCSResult])
def get_dcs_for_vendor(vendor_code: str, db: Session = Depends(get_db)):
    """Return all current DCS codes associated with a vendor."""
    codes = (
        db.query(DcsCode)
        .join(VendorDcs, VendorDcs.dcs == DcsCode.dcs)
        .filter(VendorDcs.vendor_code == vendor_code, DcsCode.is_current == True)
        .all()
    )
    return [DCSResult.model_validate(c) for c in codes]

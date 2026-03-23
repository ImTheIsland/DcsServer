from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...db.database import get_db
from ...db.models import DcsCode, Keyword, UserKeyword, VendorDcs
from ...init.keyword_processor import extract_keywords, stem_word
from ..schemas import DCSResult, KeywordUpdateBody

router = APIRouter(prefix="/dcs", tags=["dcs"])


@router.get("/possible", response_model=list[DCSResult])
def get_possible_dcs_codes(
    request: Request,
    item_name: str,
    vendor_code: str | None = None,
    person: str | None = None,
    db: Session = Depends(get_db),
):
    """Return ranked DCS suggestions for a product description."""
    person_filter = person.upper()[0] if person else None
    cfg = request.app.state.config
    w_product = cfg["weight_product"]
    w_canonical = cfg["weight_canonical"]
    w_user = cfg["weight_user"]
    w_vendor = cfg["weight_vendor_affinity"]

    tokens = extract_keywords(item_name)
    if not tokens:
        return []

    # --- 1. Score from keywords table (product + canonical sources) ---
    kw_rows = (
        db.query(Keyword.dcs, Keyword.source, func.sum(Keyword.weight).label("total"))
        .filter(Keyword.keyword.in_(tokens))
        .group_by(Keyword.dcs, Keyword.source)
        .all()
    )
    raw_kw_scores: dict[str, float] = {}
    for dcs_val, source, total in kw_rows:
        mult = w_canonical if source == "canonical" else w_product
        raw_kw_scores[dcs_val] = raw_kw_scores.get(dcs_val, 0.0) + total * mult

    # --- 2. Score from user_keywords (cascade: D / D+C / D+C+S) ---
    all_user_kws = db.query(UserKeyword).all()
    # Pre-stem stored keywords for comparison
    stemmed_user = [
        (stem_word(row.keyword), row.d, row.c, row.s)
        for row in all_user_kws
    ]

    matched_d: set[str] = set()
    matched_dc: set[tuple[str, str]] = set()
    matched_dcs_exact: set[str] = set()

    for token in tokens:
        for stemmed_kw, d, c, s in stemmed_user:
            if token != stemmed_kw:
                continue
            if c and s:
                matched_dcs_exact.add(d + c + s)
            elif c:
                matched_dc.add((d, c))
            else:
                matched_d.add(d)

    if matched_d or matched_dc or matched_dcs_exact:
        current_codes = db.query(DcsCode).filter(DcsCode.is_current == True).all()
        for code in current_codes:
            user_score = 0.0
            if code.dcs in matched_dcs_exact:
                user_score += w_user * 3
            if code.c and (code.d, code.c) in matched_dc:
                user_score += w_user * 2
            if code.d in matched_d:
                user_score += w_user * 1
            if user_score > 0:
                raw_kw_scores[code.dcs] = raw_kw_scores.get(code.dcs, 0.0) + user_score

    # --- 3. Vendor affinity ---
    vendor_dcs_counts: dict[str, int] = {}
    total_vendor_products = 0
    if vendor_code:
        vd_rows = db.query(VendorDcs).filter(VendorDcs.vendor_code == vendor_code).all()
        for row in vd_rows:
            vendor_dcs_counts[row.dcs] = row.product_count
            total_vendor_products += row.product_count

    # Candidate DCS = keyword matches + vendor's DCS codes (current only)
    candidate_dcs = set(raw_kw_scores.keys()) | set(vendor_dcs_counts.keys())
    if not candidate_dcs:
        return []

    person_q = db.query(DcsCode).filter(
        DcsCode.dcs.in_(candidate_dcs), DcsCode.is_current == True
    )
    if person_filter:
        person_q = person_q.filter(DcsCode.person == person_filter)
    dcs_records = {code.dcs: code for code in person_q.all()}
    if not dcs_records:
        return []

    # --- 4. Normalize keyword_score to 0-100 ---
    max_kw = max(raw_kw_scores.values()) if raw_kw_scores else 1.0
    norm_kw: dict[str, float] = {
        d: (s / max_kw * 100) for d, s in raw_kw_scores.items()
    }

    # --- 5. Compute raw vendor affinity per candidate ---
    raw_va: dict[str, float] = {}
    if vendor_code and total_vendor_products > 0:
        for dcs_val, count in vendor_dcs_counts.items():
            raw_va[dcs_val] = (count / total_vendor_products) * 100 * w_vendor

    # Normalize vendor affinity to 0-100
    norm_va: dict[str, float] = {}
    if raw_va:
        max_va = max(raw_va.values())
        if max_va > 0:
            norm_va = {d: (v / max_va * 100) for d, v in raw_va.items()}
        else:
            norm_va = {d: 0.0 for d in raw_va}

    # --- 6. Build results ---
    results: list[DCSResult] = []
    for dcs_val, code in dcs_records.items():
        kw_score = round(norm_kw.get(dcs_val, 0.0), 2)

        if vendor_code:
            va_score = round(norm_va.get(dcs_val, 0.0), 2)
            v_match = dcs_val in vendor_dcs_counts
            combined = round((kw_score + va_score) / 2, 2)
        else:
            va_score = None
            v_match = None
            combined = kw_score

        results.append(
            DCSResult(
                dcs=code.dcs,
                d=code.d,
                c=code.c,
                s=code.s,
                type=code.type,
                description=code.description,
                person=code.person,
                is_current=code.is_current,
                keyword_score=kw_score,
                vendor_affinity_score=va_score,
                combined_score=combined,
                vendor_match=v_match,
            )
        )

    # Sort: vendor matches first by combined_score desc, then non-vendor by combined_score desc
    results.sort(key=lambda r: (0 if r.vendor_match else 1, -(r.combined_score or 0)))
    return results


@router.get("", response_model=list[DCSResult])
def get_dcs_codes(wildcard: str, db: Session = Depends(get_db)):
    """Wildcard/prefix match on the dcs field. Only returns current codes."""
    codes = (
        db.query(DcsCode)
        .filter(DcsCode.dcs.like(f"{wildcard}%"), DcsCode.is_current == True)
        .all()
    )
    return [DCSResult.model_validate(c) for c in codes]


@router.get("/keywords/{dcs}", response_model=list[str])
def get_keywords_for_dcs(dcs: str, db: Session = Depends(get_db)):
    """Return all keywords associated with a DCS code (keywords table only)."""
    rows = db.query(Keyword.keyword).filter(Keyword.dcs == dcs).all()
    return [r.keyword for r in rows]


@router.put("/keywords/{dcs}", status_code=204)
def update_keywords_for_dcs(
    dcs: str,
    body: KeywordUpdateBody,
    db: Session = Depends(get_db),
):
    """
    Update keywords for a DCS code.
    replace: delete all existing, insert new list (weight=1 each).
    merge: upsert — add new keywords, increment weight on existing.
    """
    if not db.query(DcsCode).filter(DcsCode.dcs == dcs).first():
        raise HTTPException(status_code=404, detail=f"DCS code '{dcs}' not found.")

    if body.mode == "replace":
        db.query(Keyword).filter(Keyword.dcs == dcs).delete()
        for kw in body.keywords:
            db.add(Keyword(keyword=kw, dcs=dcs, weight=1))
    else:  # merge
        existing = {
            row.keyword: row
            for row in db.query(Keyword).filter(Keyword.dcs == dcs).all()
        }
        for kw in body.keywords:
            if kw in existing:
                existing[kw].weight += 1
            else:
                db.add(Keyword(keyword=kw, dcs=dcs, weight=1))

    db.commit()


@router.get("/{dcs}/vendors", response_model=list[str])
def get_vendors_with_dcs(dcs: str, db: Session = Depends(get_db)):
    """Return all vendor codes that have products under this DCS."""
    rows = db.query(VendorDcs.vendor_code).filter(VendorDcs.dcs == dcs).all()
    return [r.vendor_code for r in rows]

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db.database import get_db
from ...db.models import UserKeyword as UserKeywordModel
from ..schemas import UserKeyword, UserKeywordCreate

router = APIRouter(prefix="/user-keywords", tags=["user-keywords"])


@router.get("", response_model=list[UserKeyword])
def get_user_keywords(
    d: str,
    c: str | None = None,
    s: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(UserKeywordModel).filter(UserKeywordModel.d == d)
    if c is not None:
        q = q.filter(UserKeywordModel.c == c)
    if s is not None:
        q = q.filter(UserKeywordModel.s == s)
    return q.all()


@router.post("", response_model=UserKeyword, status_code=201)
def create_user_keyword(body: UserKeywordCreate, db: Session = Depends(get_db)):
    if body.s and not body.c:
        raise HTTPException(status_code=400, detail="s requires c to be set.")
    row = UserKeywordModel(
        keyword=body.keyword,
        d=body.d,
        c=body.c,
        s=body.s,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{id}", status_code=204)
def delete_user_keyword(id: int, db: Session = Depends(get_db)):
    row = db.query(UserKeywordModel).filter(UserKeywordModel.id == id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User keyword not found.")
    db.delete(row)
    db.commit()

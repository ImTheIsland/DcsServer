from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DCSResult(BaseModel):
    dcs: str
    d: str
    c: str | None
    s: str | None
    type: str | None
    description: str | None
    person: str | None
    is_current: bool
    keyword_score: float | None = None
    vendor_affinity_score: float | None = None
    combined_score: float | None = None
    vendor_match: bool | None = None

    model_config = {"from_attributes": True}


class UserKeyword(BaseModel):
    id: int
    keyword: str
    d: str
    c: str | None
    s: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserKeywordCreate(BaseModel):
    keyword: str
    d: str
    c: str | None = None
    s: str | None = None


class KeywordUpdateBody(BaseModel):
    keywords: list[str]
    mode: Literal["replace", "merge"]

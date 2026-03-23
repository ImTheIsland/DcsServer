from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint

from .database import Base


class DcsCode(Base):
    __tablename__ = "dcs_codes"

    dcs = Column(Text, primary_key=True)      # full concatenated code e.g. "FW CLMMEN"
    d = Column(Text, nullable=False)           # department, exactly 3 chars, space-padded
    c = Column(Text, nullable=True)            # class, 3 chars, or NULL
    s = Column(Text, nullable=True)            # subclass, 3 chars, or NULL
    type = Column(Text, nullable=True)         # from canonical list e.g. "Footwear"
    description = Column(Text, nullable=True)  # from canonical list e.g. "Climbing Shoes"
    person = Column(Text, nullable=True)       # first letter of person-type component: M W U B G Y K
    is_current = Column(Boolean, nullable=False, default=True)


class Vendor(Base):
    __tablename__ = "vendors"

    vendor_code = Column(Text, primary_key=True)  # 6-char UID from inventory system


class VendorDcs(Base):
    __tablename__ = "vendor_dcs"

    vendor_code = Column(Text, ForeignKey("vendors.vendor_code"), primary_key=True)
    dcs = Column(Text, ForeignKey("dcs_codes.dcs"), primary_key=True)
    product_count = Column(Integer, nullable=False, default=1)


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(Text, nullable=False)
    dcs = Column(Text, ForeignKey("dcs_codes.dcs"), nullable=False)
    weight = Column(Integer, nullable=False, default=1)
    source = Column(Text, nullable=False, default="product")  # "product" or "canonical"

    __table_args__ = (
        UniqueConstraint("keyword", "dcs", name="uq_keyword_dcs"),
    )


class UserKeyword(Base):
    __tablename__ = "user_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(Text, nullable=False)
    d = Column(Text, nullable=False)
    c = Column(Text, nullable=True)
    s = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("keyword", "d", "c", "s", name="uq_user_keyword"),
    )

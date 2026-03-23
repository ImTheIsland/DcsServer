# DCS Lookup — Project Specification

## Overview
A SQLite-backed lookup engine that lets retail employees type a product description and get ranked DCS (Department/Class/Subclass) code suggestions. Consists of a standalone initialization script, a FastAPI server, and a CustomTkinter client UI.

---

## Project Structure
```
dcs_lookup/
├── db/
│   ├── database.py          # SQLAlchemy engine, session, base
│   └── models.py            # All table definitions
├── server/
│   ├── main.py              # FastAPI app
│   ├── routers/
│   │   ├── dcs.py           # DCS-related endpoints
│   │   ├── keywords.py      # Keyword endpoints
│   │   └── vendors.py       # Vendor endpoints
│   └── schemas.py           # Pydantic request/response models
├── init/
│   ├── populate_db.py       # Standalone init script (run manually)
│   ├── importer.py          # Inventory REST API client
│   └── keyword_processor.py # Stop words, stemming, unit stripping
├── client/
│   └── app.py               # CustomTkinter UI
├── data/                    # Drop new_codes.csv here
└── dcs_lookup.db
```

---

## Database Schema (SQLAlchemy/SQLite)

### `dcs_codes`
The master list of DCS codes. Populated from a user-supplied CSV/Excel file.
- `dcs` TEXT PRIMARY KEY — full 9-char code, e.g. `001002003`
- `d` TEXT — department (3 chars)
- `c` TEXT — class (3 chars)
- `s` TEXT — subclass (3 chars)
- `type` TEXT — general type label, typically describes the D code (e.g. "Climbing", "Footwear")
- `description` TEXT — full DCS description (e.g. "Climbing Guide Book")
- `is_current` BOOLEAN — False means this code is old/flagged; old codes are never returned in lookup results

### `vendors`
- `vendor_code` TEXT PRIMARY KEY — 6-char UID from inventory system

### `vendor_dcs`
Junction table — normalized, one row per vendor+DCS pair.
- `vendor_code` TEXT FK → vendors
- `dcs` TEXT FK → dcs_codes
- Composite PK on (vendor_code, dcs)

### `keywords`
- `id` INTEGER PRIMARY KEY autoincrement
- `keyword` TEXT — stemmed, normalized word
- `dcs` TEXT FK → dcs_codes
- `weight` INTEGER — number of products that contributed this keyword to this DCS
- UNIQUE constraint on (keyword, dcs)

---

## Initialization Script (`init/populate_db.py`)
Run manually from the command line. Not part of the FastAPI server.

**Steps:**
1. Read new DCS codes from a CSV or Excel file (path passed as CLI arg `--dcs-file`)
2. Populate `dcs_codes` table from that file
3. Call the inventory REST API (multiple targeted calls — details below) to pull ~70k products
4. For each product: extract vendor → upsert into `vendors` and `vendor_dcs`
5. For each product: extract keywords from product name → upsert into `keywords` with weight increment
6. Any DCS code found in the inventory data that is NOT in the `dcs_codes` table gets inserted with `is_current=False`

**Inventory REST API:**
- Source: remote REST API / HTTP endpoint (base URL and auth details TBD — use a config file or env vars)
- Access pattern: multiple targeted calls (by DCS, vendor, or category — exact endpoint structure TBD)
- Each product record contains: `dcs_code` (9 chars), `vendor_code` (6 chars), `product_name` (string)
- The importer logic lives in `init/importer.py` and should be cleanly separable from the DB logic

**Keyword processing (`init/keyword_processor.py`):**
- Lowercase all words
- Remove common English stop words (use NLTK stopwords corpus)
- Remove tokens that are purely numeric or unit-like (e.g. `2pk`, `32oz`, `size10`, standalone numbers)
- Stem remaining words using NLTK `PorterStemmer`
- Return list of cleaned, stemmed keywords

---

## FastAPI Server (`server/`)

All endpoints only return `is_current=True` DCS codes unless otherwise noted.

### `GET /dcs/possible`
```
get_possible_dcs_codes(item_name: str, vendor_code: str | None = None) -> List[DCSResult]
```
- Stem/filter the `item_name` input words using the same keyword processor
- Look up matching `(keyword → dcs)` rows in the `keywords` table, sum weights per DCS → base score
- If `vendor_code` provided, tag each result `vendor_match: true/false`
- Sort: vendor matches first (by descending score), then non-vendor matches (by descending score)
- Only return `is_current=True` codes
- Response includes full DCS details (type, description, d/c/s) alongside score and vendor_match flag

### `GET /dcs/keywords/{dcs}`
```
get_keywords_for_dcs(dcs: str) -> List[str]
```
Returns all keywords associated with a DCS code.

### `PUT /dcs/keywords/{dcs}`
```
update_keywords_for_dcs(dcs: str, keywords: List[str], mode: Literal["replace", "merge"]) -> None
```
- `replace`: delete all existing keywords for this DCS, insert new list
- `merge`: upsert — add new keywords, increment weight on existing ones

### `GET /vendors/{vendor_code}/dcs`
```
get_dcs_for_vendor(vendor_code: str) -> List[DCSResult]
```
Returns all current DCS codes associated with a vendor, with full details.

### `GET /dcs`
```
get_dcs_codes(wildcard: str) -> List[DCSResult]
```
Wildcard/partial match on the `dcs` field (e.g. `001` returns all codes starting with `001`). Only current codes returned.

### `GET /dcs/{dcs}/vendors`
```
get_vendors_with_dcs(dcs: str) -> List[str]
```
Returns all vendor codes that have products under this DCS.

---

## Pydantic Response Model: `DCSResult`
```python
class DCSResult(BaseModel):
    dcs: str
    d: str
    c: str
    s: str
    type: str
    description: str
    is_current: bool
    score: int | None          # present on scored endpoints
    vendor_match: bool | None  # present when vendor_code was supplied
```

---

## CustomTkinter Client (`client/app.py`)
- Primary workflow: user types a product description → gets ranked DCS suggestions
- Results display includes: DCS code, type, description, score, and a visual indicator for vendor match vs. outside vendor history
- Optional vendor code input field to enable vendor-match ranking
- UI detail is secondary concern for now — functionality first

---

## Key Dependencies
```
fastapi
uvicorn
sqlalchemy
pydantic
nltk
customtkinter
requests        # for inventory API calls
openpyxl        # for Excel new-codes file support
```
NLTK data required: `stopwords`, `punkt`

---

## Notes for Implementation
- SQLite connection should use `check_same_thread=False` (multi-threaded FastAPI access)
- The init script and the server share the same `db/` models — no duplication
- Inventory API base URL and credentials should be read from environment variables or a `.env` file (use `python-dotenv`)
- The init script should be safe to re-run (upsert everywhere, not insert)
- Keyword weights reflect how many distinct products contributed that keyword to a DCS — increment on upsert, don't reset

---

## Suggested Claude Code Prompt
Paste this document into a new Claude Code session and start with:

> "Here is the full spec for a project I want to build. Please implement it module by module starting with `db/models.py` and `db/database.py`, then ask me before moving to the next module."

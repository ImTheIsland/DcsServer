# DCS Lookup — Project Specification (v2)

## Overview
A SQLite-backed lookup engine that lets retail employees type a product description and
get ranked DCS (Department/Class/Subclass) code suggestions. Consists of a standalone
initialization script, a FastAPI server, and a CustomTkinter client UI.

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
├── data/                    # Drop CSVs here
├── config.json              # Tunable weight multipliers
└── dcs_lookup.db
```

---

## DCS Code Format

DCS codes follow a strict 3+3+3 character block structure:

```
DCS = D(3 chars) + C(3 chars, optional) + S(3 chars, optional)
```

- Each component is **exactly 3 characters**, right-padded with spaces if needed
- Components are **concatenated directly** — no separator
- The apparent "space" in codes like `FW CLMMEN` is the trailing space of `D = "FW "`
- S can only exist if C exists. C can exist without S.
- Valid examples:
  - `BIK` → D=`BIK`, C=None, S=None
  - `BIKACS` → D=`BIK`, C=`ACS`, S=None
  - `FW CLMMEN` → D=`FW `, C=`CLM`, S=`MEN`

**CSV import note:** Exported CSV files strip trailing spaces from D, C, S values.
The import script must right-pad each component to 3 chars before storing or
reconstructing the full DCS string.

---

## Database Schema (SQLAlchemy / SQLite)

### `dcs_codes`
Master list of all DCS codes encountered in the product dataset.
Populated during initialization; determines which codes exist in the engine.

| Column | Type | Notes |
|---|---|---|
| `dcs` | TEXT PK | Full concatenated code e.g. `FW CLMMEN` |
| `d` | TEXT | Department, exactly 3 chars, space-padded |
| `c` | TEXT\|NULL | Class, exactly 3 chars, space-padded, or NULL |
| `s` | TEXT\|NULL | Subclass, exactly 3 chars, space-padded, or NULL |
| `type` | TEXT\|NULL | From canonical list e.g. `Footware` |
| `description` | TEXT\|NULL | From canonical list e.g. `Climbing Shoes` |
| `is_current` | BOOLEAN | True = present in canonical list; False = deprecated |

### `vendors`
| Column | Type | Notes |
|---|---|---|
| `vendor_code` | TEXT PK | 6-char UID from inventory system |

### `vendor_dcs`
Normalized junction table — one row per vendor+DCS pair.

| Column | Type | Notes |
|---|---|---|
| `vendor_code` | TEXT FK | → vendors |
| `dcs` | TEXT FK | → dcs_codes |
| `product_count` | INTEGER | Number of products for this vendor under this DCS |
| PK | | Composite (vendor_code, dcs) |

`product_count` is used to calculate vendor affinity score. A vendor with 500 products
under one DCS code has much stronger affinity than one with 2.

### `keywords`
Keywords derived from product names and canonical Type/Description fields.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `keyword` | TEXT | Stemmed, normalized word |
| `dcs` | TEXT FK | → dcs_codes |
| `weight` | INTEGER | Count of products/entries that contributed this keyword |
| `source` | TEXT | `product` or `canonical` — tracks origin |
| UNIQUE | | (keyword, dcs) |

### `user_keywords`
User-defined keywords associated at the D, D+C, or D+C+S level.
**Never wiped during full refresh or canonical list updates.**

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `keyword` | TEXT | Raw user-entered word (still stemmed on lookup) |
| `d` | TEXT | Department (always required) |
| `c` | TEXT\|NULL | Class (optional) |
| `s` | TEXT\|NULL | Subclass (optional, requires C) |
| `created_at` | DATETIME | Timestamp |
| UNIQUE | | (keyword, d, c, s) |

**Cascade behavior:** A user keyword defined at D-level matches all DCS codes sharing
that D. A keyword at D+C matches all codes sharing that D and C. A keyword at D+C+S
matches only the exact code. More specific matches score higher.

---

## Keyword Sources & Weight Multipliers

Three keyword sources feed the scoring engine. All multipliers are stored in
`config.json` and can be adjusted without code changes.

| Source | Description | Config key |
|---|---|---|
| Product names | `item_name` from product dataset, stemmed/filtered | `weight_product` |
| Canonical descriptions | `Type` + `Description` from canonical CSV | `weight_canonical` |
| User-defined keywords | Manual entries in `user_keywords` table | `weight_user` |
| Vendor affinity | Based on `product_count` in `vendor_dcs` | `weight_vendor_affinity` |

**Default `config.json`:**
```json
{
  "weight_product": 1.0,
  "weight_canonical": 2.0,
  "weight_user": 3.0,
  "weight_vendor_affinity": 2.0
}
```

These are multipliers applied at scoring time, not stored in the DB — changing them
takes effect immediately without any data rebuild.

---

## Keyword Processing Pipeline (`init/keyword_processor.py`)

Applied consistently to all text inputs — product names, canonical descriptions,
and query strings at lookup time:

1. Lowercase all text
2. Tokenize on whitespace and punctuation
3. Remove common English stop words (NLTK stopwords corpus)
4. Remove tokens that are purely numeric or unit-like (e.g. `2pk`, `32oz`, `10ct`,
   standalone integers)
5. Stem remaining tokens using NLTK `PorterStemmer`
6. Return deduplicated list of cleaned tokens

---

## Initialization Script (`init/populate_db.py`)
Run manually from the command line. Not part of the FastAPI server.

### Full Refresh (`--mode full`)
Wipes and rebuilds all tables **except `user_keywords`**.

1. Drop and recreate `dcs_codes`, `vendors`, `vendor_dcs`, `keywords`
2. Load canonical CSV → populate `dcs_codes` with `is_current=True`, extract
   keywords from `Type` and `Description` columns
3. Call inventory REST API (targeted calls via `importer.py`) to pull all products
4. For each product:
   - If `dcs_code` not in `dcs_codes`: insert with `is_current=False`, NULL type/description
   - Upsert vendor into `vendors`
   - Upsert into `vendor_dcs`, incrementing `product_count`
   - Extract keywords from `item_name`, upsert into `keywords` with weight increment
5. Any `dcs_codes` row not touched by step 4 (i.e. in canonical list but no products)
   remains — it's a valid current code with no products yet

**Note:** `user_keywords` is never touched during a full refresh.

### Incremental Update (`--mode incremental --file products.csv`)
Additive only. Accepts a small CSV in the same format as the product dataset.

1. For each product in the file:
   - If `dcs_code` not in `dcs_codes`: insert with `is_current=False`
   - Upsert vendor, vendor_dcs (increment product_count), keywords (increment weight)
2. Does not remove or recalculate any existing data

### CLI Usage
```bash
# Full rebuild
python init/populate_db.py --mode full --canonical data/canonical_dcs.csv

# Incremental update from a small product file
python init/populate_db.py --mode incremental --file data/new_products.csv
```

---

## Inventory REST API (`init/importer.py`)

- Access pattern: multiple targeted calls (by DCS, vendor, or category)
- Base URL and credentials read from `.env` file via `python-dotenv`
- Each product record contains: `dcs_code`, `vendor_code`, `item_name`
- Importer logic is cleanly separated from DB logic — `populate_db.py` calls it,
  does not embed HTTP logic itself

---

## Scoring Algorithm (`get_possible_dcs_codes`)

Given `item_name: str` and optional `vendor_code: str`:

1. Run `item_name` through the keyword processing pipeline → list of stemmed tokens
2. For each token, look up matching rows in `keywords` table → accumulate
   `weight * weight_product` or `weight * weight_canonical` per DCS code (by source)
3. For each token, look up matching `user_keywords` rows:
   - Exact D+C+S match → `weight_user * 3`
   - D+C match → `weight_user * 2`
   - D-only match → `weight_user * 1`
   Distribute score to all `dcs_codes` rows sharing that D / D+C / D+C+S
4. Sum all contributions → **keyword_score** per candidate DCS
5. If `vendor_code` provided:
   - Look up all `vendor_dcs` rows for that vendor
   - Total vendor products = sum of all `product_count` for that vendor
   - For each candidate DCS: `vendor_affinity = (product_count / total) * 100 * weight_vendor_affinity`
   - Tag `vendor_match = True` if vendor has any products under that DCS
6. Normalize keyword_score and vendor_affinity_score each to 0-100
7. **combined_score** = weighted average of the two normalized scores
8. Sort: vendor matches first by combined_score desc, then non-vendor matches by
   combined_score desc
9. Only return `is_current=True` DCS codes

---

## FastAPI Server (`server/`)

All endpoints only return `is_current=True` DCS codes unless noted otherwise.

### `GET /dcs/possible`
```
params: item_name: str, vendor_code: str | None
returns: List[DCSResult]
```

### `GET /dcs/keywords/{dcs}`
```
returns: List[str]
```
All stemmed keywords associated with a DCS code (from `keywords` table only,
not `user_keywords`).

### `PUT /dcs/keywords/{dcs}`
```
body: { keywords: List[str], mode: "replace" | "merge" }
```
- `replace`: delete all existing `keywords` rows for this DCS, insert new list
- `merge`: upsert — add new keywords, increment weight on existing ones

### `GET /vendors/{vendor_code}/dcs`
```
returns: List[DCSResult]
```
All current DCS codes for a vendor, with full details.

### `GET /dcs`
```
params: wildcard: str
returns: List[DCSResult]
```
Partial/prefix match on the `dcs` field. Current codes only.

### `GET /dcs/{dcs}/vendors`
```
returns: List[str]
```
All vendor codes that have products under this DCS.

### `GET /user-keywords`
```
params: d: str, c: str | None, s: str | None
returns: List[UserKeyword]
```

### `POST /user-keywords`
```
body: { keyword: str, d: str, c: str | None, s: str | None }
```

### `DELETE /user-keywords/{id}`

---

## Pydantic Response Models (`server/schemas.py`)

```python
class DCSResult(BaseModel):
    dcs: str
    d: str
    c: str | None
    s: str | None
    type: str | None
    description: str | None
    is_current: bool
    keyword_score: float | None       # 0-100, present on scored endpoints
    vendor_affinity_score: float | None  # 0-100, present when vendor_code supplied
    combined_score: float | None      # 0-100, composite
    vendor_match: bool | None         # present when vendor_code supplied

class UserKeyword(BaseModel):
    id: int
    keyword: str
    d: str
    c: str | None
    s: str | None
    created_at: datetime
```

---

## CustomTkinter Client (`client/app.py`)

- Primary workflow: type a product description + optional vendor code → get ranked
  DCS suggestions
- Results display: DCS code, Type, Description, keyword score, vendor affinity score,
  combined score, vendor match indicator
- UI styling and layout detail is secondary — functionality first

---

## Key Dependencies
```
fastapi
uvicorn
sqlalchemy
pydantic
nltk
customtkinter
requests        # inventory API calls
openpyxl        # Excel canonical file support
python-dotenv   # env var management
```

NLTK data required: `stopwords`, `punkt`

---

## Implementation Notes
- SQLite: use `check_same_thread=False` for multi-threaded FastAPI access
- `db/models.py` and `db/database.py` are shared between init script and server —
  no duplication
- All upserts use SQLAlchemy `insert().on_conflict_do_update()` — init script is
  safe to re-run in incremental mode
- Inventory API credentials live in `.env`, never committed to source control
- `config.json` weight multipliers are loaded at server startup and can be reloaded
  without restart via a `POST /config/reload` endpoint (optional, implement later)

---

## Suggested Claude Code Opening Prompt

Paste this document into a new Claude Code session and start with:

> "Here is the full spec for a project I want to build. Please implement it module
> by module starting with `db/models.py` and `db/database.py`. Show me the code and
> ask me to confirm before moving to the next module."

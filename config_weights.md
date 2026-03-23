# DCS Lookup — Weight Configuration Reference

The four weights in `config.json` are **multipliers** applied at query time during scoring.
Changing them takes effect immediately — no data rebuild required.

---

## `custom_stop_words` (default: `[]`)

A list of words that are **never stored as keywords**, regardless of source (product names,
canonical descriptions, or user keywords). Applied during keyword extraction before stemming,
so add natural-language words — not their stemmed forms.

```json
"custom_stop_words": ["assorted", "misc", "new", "black", "white"]
```

- Words are matched case-insensitively.
- Single-character tokens are always dropped regardless of this list.
- Changes take effect on the **next database rebuild or incremental import** — existing
  keywords already in the database are not retroactively removed. Re-run
  `populate_db.py` with `full_refresh` to apply changes to stored data.
- At query time, the same filter is applied to inbound search text, so adding a word
  here also stops it from influencing lookups immediately (no rebuild needed for that).

---

## How scoring works (overview)

When a user searches for a product, each candidate DCS code receives two independent scores:

1. **Keyword score** — how well the search text matches keywords associated with that DCS code
2. **Vendor affinity score** — how heavily a given vendor sells under that DCS code (only when a vendor code is supplied)

Each score is independently normalized to **0–100**, then averaged into a **combined score** used for final ranking.

---

## The four weights

### `weight_product` (default: `1.0`)
Applied to keywords derived from **product names** (the `item_name` field in the inventory data).

- These keywords are noisy — product names vary wildly in phrasing, abbreviation, and specificity.
- A weight of `1.0` is the baseline. Lowering it reduces the influence of product name matches relative to other sources.

---

### `weight_canonical` (default: `2.0`)
Applied to keywords derived from the **canonical DCS list** (the `Type` and `Description` columns in the canonical CSV).

- These are curated, authoritative labels (e.g. "Climbing Shoes", "Footwear"). They are more reliable signals than product names.
- Higher than `weight_product` by default because a canonical description match is a stronger indicator of the correct DCS code.
- **Increasing this** makes canonical label matches dominate over raw product keyword matches.

---

### `weight_user` (default: `3.0`)
Applied to **user-defined keywords** entered manually via the UI or API.

- User keywords are the most intentional signal — a human has explicitly associated a word with a DCS level.
- Scored with a cascade multiplier based on specificity:
  - **D-level only** → `weight_user × 1` — broad match, distributed across all DCS codes in that department
  - **D+C level** → `weight_user × 2` — more specific, distributed across all codes in that department+class
  - **D+C+S level** → `weight_user × 3` — exact match to one DCS code
- **Increasing this** makes user-defined associations increasingly dominant in results. Useful once you have a well-curated set of user keywords.
- **Decreasing this** is appropriate early on, before the user_keywords table is well populated.

---

### `weight_vendor_affinity` (default: `2.0`)
Scales the **vendor affinity signal** when a vendor code is provided with the search.

- Vendor affinity is calculated as: `(vendor's product count under this DCS / vendor's total products) × 100`
- This raw percentage is then multiplied by `weight_vendor_affinity` before normalization.
- The result answers: "how much of this vendor's catalogue lives under this DCS code?"
- **Increasing this** pulls vendor-heavy DCS codes higher in results, even when keyword evidence is weaker.
- **Decreasing this** (toward `0.0`) makes vendor affinity a tiebreaker rather than a primary signal.
- Setting to `0.0` effectively disables vendor affinity — keyword score alone drives ranking.

---

## Relative balance

The weights only matter in relation to each other. Some useful reference points:

| Goal | Suggested adjustment |
|---|---|
| Trust curated labels more than product noise | Raise `weight_canonical`, lower `weight_product` |
| User keywords are well-curated and authoritative | Raise `weight_user` (e.g. to `5.0`) |
| User keywords are sparse / experimental | Lower `weight_user` (e.g. to `1.5`) |
| Vendor context should strongly influence results | Raise `weight_vendor_affinity` (e.g. to `4.0`) |
| Vendor context should only break ties | Lower `weight_vendor_affinity` (e.g. to `0.5`) |
| Pure keyword-only ranking (ignore vendor) | Set `weight_vendor_affinity` to `0.0` |

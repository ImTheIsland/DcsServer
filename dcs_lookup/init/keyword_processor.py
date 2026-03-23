"""
Keyword extraction and normalization.

Takes a raw product name string and returns a list of cleaned, stemmed keywords
suitable for insertion into the keywords table.
"""
import json
import re
from pathlib import Path

import nltk
from nltk.corpus import stopwords
from nltk.stem import PorterStemmer

# Ensure required NLTK data is present
for _pkg in ("stopwords", "punkt"):
    try:
        nltk.data.find(f"corpora/{_pkg}" if _pkg == "stopwords" else f"tokenizers/{_pkg}")
    except LookupError:
        nltk.download(_pkg, quiet=True)

_STOP_WORDS = set(stopwords.words("english"))
_STEMMER = PorterStemmer()

# Matches tokens that are purely numeric or unit-like: 2pk, 32oz, size10, 10, 5.5, etc.
_UNIT_RE = re.compile(r'^\d+(\.\d+)?(pk|oz|ml|l|g|kg|lb|lbs|ft|in|cm|mm|m|size\d*)?$', re.IGNORECASE)
_PURE_NUMERIC_RE = re.compile(r'^\d+(\.\d+)?$')

# Custom stop words loaded from config.json at the project root.
# Add domain-specific noise words there under the "custom_stop_words" key.
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"
_CUSTOM_STOP_WORDS: set[str] = set()
try:
    with open(_CONFIG_PATH) as _f:
        _CUSTOM_STOP_WORDS = {w.lower() for w in json.load(_f).get("custom_stop_words", [])}
except (FileNotFoundError, ValueError):
    pass


def stem_word(word: str) -> str:
    """Stem a single word using the same PorterStemmer used by extract_keywords."""
    return _STEMMER.stem(word.lower())


def extract_keywords(product_name: str) -> list[str]:
    """
    Process a product name into a list of stemmed, normalized keywords.

    Steps:
      1. Lowercase
      2. Tokenize on non-alphanumeric characters
      3. Drop single-character tokens
      4. Remove English stop words
      5. Remove custom stop words (from config.json "custom_stop_words")
      6. Remove purely numeric or unit-like tokens (e.g. 2pk, 32oz, size10)
      7. Stem with PorterStemmer (output is always lowercase)
      8. Deduplicate while preserving order
    """
    tokens = re.split(r'[^a-zA-Z0-9]+', product_name.lower())

    keywords = []
    seen = set()
    for token in tokens:
        if not token:
            continue
        if len(token) < 2:
            continue
        if token in _STOP_WORDS:
            continue
        if token in _CUSTOM_STOP_WORDS:
            continue
        if _PURE_NUMERIC_RE.match(token) or _UNIT_RE.match(token):
            continue
        stemmed = _STEMMER.stem(token)
        if stemmed and len(stemmed) >= 2 and stemmed not in seen:
            seen.add(stemmed)
            keywords.append(stemmed)

    return keywords

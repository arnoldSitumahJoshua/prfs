"""
fuzzy.py — Typo-tolerant catalogue search.

Two-tier strategy:
  1. Broad pre-filter in SQL (cheap) → pull ~500 candidates
  2. Precise scoring in Python → rank by relevance

Scoring (higher is better):
  1000  exact code match
   800  code starts with query
   700  name starts with query
   500  code contains query
   400  name contains query
   300  keyword contains query
   0-250  fuzzy name match (scaled by similarity)
   0-150  fuzzy code match
"""
from difflib import SequenceMatcher


def _similarity(a, b):
    """Return a 0..1 similarity between two strings (case-insensitive)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def score_item(item, query):
    """
    Score a single item against the query. Returns an int, 0 if not a match.
    `item` can be any object with attributes: code, name, keywords.
    """
    if not query:
        return 0

    q = query.lower().strip()
    code = (item.code or "").lower()
    name = (item.name or "").lower()
    kws  = (item.keywords or "").lower()

    # ---- Exact / prefix matches (highest priority) ----
    if code == q:
        return 1000
    if code.startswith(q):
        return 800
    if name.startswith(q):
        return 700
    if q in code:
        return 500
    if q in name:
        return 400
    if kws and q in kws:
        return 300

    # ---- Fuzzy matches (only if query is long enough to be meaningful) ----
    if len(q) < 3:
        return 0

    best = 0

    # Name: compare against the query and against the first N chars of name
    # (so "portlnd" matches "Portland Cement" via "portland")
    name_first = name[:max(len(q) + 3, 8)]
    sim_name = max(_similarity(q, name[:len(q) + 2]),
                   _similarity(q, name_first))
    if sim_name >= 0.72:
        best = max(best, int(sim_name * 250))

    # Code: usually shorter, so allow a slightly lower threshold
    code_first = code[:max(len(q) + 2, 6)]
    sim_code = _similarity(q, code_first)
    if sim_code >= 0.75:
        best = max(best, int(sim_code * 150))

    # Keywords: split and try each token
    if kws:
        for tok in kws.split():
            if len(tok) < 3:
                continue
            s = _similarity(q, tok)
            if s >= 0.80:
                best = max(best, int(s * 200))
                break

    return best


def rank_items(items, query, limit=15):
    """Return a sorted list of (item, score) tuples, filtered to matches only."""
    scored = []
    for it in items:
        s = score_item(it, query)
        if s > 0:
            scored.append((it, s))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def prefilter_query(query):
    """
    Return a short string to use in a LIKE pre-filter.
    For queries of 2+ characters we use the first 2 chars — broad enough to
    include typo candidates, narrow enough to avoid scanning the whole table.
    For very short queries we return the query as-is.
    """
    q = (query or "").strip().lower()
    if len(q) <= 2:
        return q
    return q[:2]
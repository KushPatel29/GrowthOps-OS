"""Hybrid retrieval over a small, curated corpus: BM25 keywords plus local MiniLM vectors.

The same shape as Ask Your Data's retrieval: at a few hundred entries an exact
in-memory search is simpler, deterministic and safer than a vector database.

* **Keyword (BM25)** catches exact metric vocabulary ("CPL", "UTM", "bounce").
  Tokens split snake_case and keep the joined form, so ``cost_per_mql`` matches
  both "cost per MQL" and the identifier itself.
* **Vector (cosine)** catches paraphrase ("did every buyer get access" has no
  word in common with "workflow dead-letter queue"). Each entry can carry
  several phrasings; an entry scores by its best phrasing.

The combined score is ``0.55 * cosine + 0.45 * saturated BM25``, both on a 0–1
scale, so a single absolute threshold can decide when to refuse. With no
embedding runtime the index runs keyword-only and reports ``mode="keyword"``.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field

STOPWORDS = frozenset(["a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "how", "i", "in", "is", "it", "its", "me", "my", "of", "on", "or", "our", "show", "tell", "that", "the", "their", "them", "there", "these", "this", "to", "us", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "you", "your", "can", "could", "should", "would", "please", "give", "get", "per"])
SYNONYMS = {"bitly": "short", "revenue": "cash", "sales": "cash", "hubspot": "crm", "ctr": "click",
            "ab": "experiment", "test": "experiment", "mqls": "mql", "leads": "lead", "emails": "email",
            "links": "link", "renewals": "renewal", "campaigns": "campaign", "buyers": "customer",
            "customers": "customer", "videos": "content", "video": "content", "junk": "spam", "bouncing": "bounce",
            "bounced": "bounce", "refunded": "refund", "renew": "renewal", "members": "subscription",
            "synced": "refreshed", "sync": "refreshed", "current": "fresh", "signups": "signup", "trusted": "trust",
            "tagged": "tag", "tagging": "tag", "tags": "tag"}
VECTOR_WEIGHT = 0.55
BM25_SATURATION = 4.0
EXACT_NAME_BONUS = 0.15  # a definition whose full name appears in the question ("warehouse ROAS") wins


def tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9_/]+", text.lower().replace("a/b", "ab"))
    out = []
    for token in raw:
        parts = [token, *token.replace("/", "_").split("_")] if ("_" in token or "/" in token) else [token]
        for part in parts:
            if not part or part in STOPWORDS:
                continue
            part = SYNONYMS.get(part, part)
            if len(part) > 3 and part.endswith("s") and not part.endswith(("ss", "us", "is")):
                part = part[:-1]
            out.append(part)
    return out


@dataclass
class Entry:
    id: str
    kind: str  # "intent" or "passage"
    title: str
    text: str
    phrasings: tuple[str, ...] = ()
    meta: dict = field(default_factory=dict)

    def keyword_text(self) -> str:
        # The title is the entry's name; weighting it keeps "human open rate" on its own definition.
        return " ".join((self.title, self.title, self.title, self.text, *self.phrasings))


class BM25:
    def __init__(self, documents: list[list[str]], k1: float = 1.4, b: float = 0.7) -> None:
        self.documents = [Counter(doc) for doc in documents]
        self.lengths = [len(doc) for doc in documents]
        self.average = sum(self.lengths) / max(len(documents), 1)
        frequency = Counter(term for doc in self.documents for term in doc)
        count = len(documents)
        self.idf = {term: math.log(1 + (count - df + 0.5) / (df + 0.5)) for term, df in frequency.items()}
        self.k1, self.b = k1, b

    def scores(self, query: list[str]) -> list[float]:
        result = []
        for doc, length in zip(self.documents, self.lengths):
            score = 0.0
            for term in set(query):
                tf = doc.get(term, 0)
                if tf:
                    norm = self.k1 * (1 - self.b + self.b * length / self.average)
                    score += self.idf.get(term, 0) * tf * (self.k1 + 1) / (tf + norm)
            result.append(score)
        return result


def resolve_mode(requested: str | None = None) -> str:
    """'hybrid' when the local embedding runtime is installed, else 'keyword'."""
    from growthops.embeddings import runtime_available

    requested = (requested or os.environ.get("GROWTHOPS_RETRIEVAL_MODE", "auto")).lower()
    if requested == "keyword":
        return "keyword"
    if runtime_available():
        return "hybrid"
    if requested == "hybrid":
        raise RuntimeError("GROWTHOPS_RETRIEVAL_MODE=hybrid but onnxruntime/tokenizers are not installed")
    return "keyword"


@dataclass
class Hit:
    entry: Entry
    score: float
    keyword: float
    vector: float | None
    matched_terms: int = 0


class HybridIndex:
    def __init__(self, entries: list[Entry], mode: str | None = None) -> None:
        self.entries = entries
        self.mode = resolve_mode(mode)
        self.bm25 = BM25([tokens(entry.keyword_text()) for entry in entries])
        self.vectors = None
        if self.mode == "hybrid":
            from growthops.embeddings import embed

            texts, owners = [], []
            for index, entry in enumerate(entries):
                for text in (entry.title + ". " + entry.text, *entry.phrasings):
                    texts.append(text)
                    owners.append(index)
            self.owners = owners
            self.vectors = embed(texts)

    def search(self, query: str, k: int = 5, kind: str | None = None) -> list[Hit]:
        query_terms = set(tokens(query))
        keyword = self.bm25.scores(list(query_terms))
        vector: list[float | None] = [None] * len(self.entries)
        if self.vectors is not None:
            from growthops.embeddings import embed

            similarity = self.vectors @ embed([query])[0]
            best: dict[int, float] = {}
            for owner, value in zip(self.owners, similarity.tolist()):
                best[owner] = max(best.get(owner, -1.0), value)
            vector = [best[index] for index in range(len(self.entries))]
        hits = []
        for index, entry in enumerate(self.entries):
            if kind and entry.kind != kind:
                continue
            kw = keyword[index] / (keyword[index] + BM25_SATURATION)
            vec = vector[index]
            score = kw if vec is None else VECTOR_WEIGHT * max(vec, 0.0) + (1 - VECTOR_WEIGHT) * kw
            matched = len(query_terms & set(self.bm25.documents[index]))
            name = set(tokens(entry.title))
            if entry.kind == "passage" and name and name <= query_terms:
                score += EXACT_NAME_BONUS
            hits.append(Hit(entry, round(score, 4), round(kw, 4), None if vec is None else round(vec, 4), matched))
        hits.sort(key=lambda hit: (-hit.score, hit.entry.id))
        return hits[:k]

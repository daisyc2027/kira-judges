import json
import math
import re
from pathlib import Path

from .embeddings import HashingEmbeddingProvider, cosine_similarity


TOKEN_RE = re.compile(r"[a-z0-9_]+")

TOPIC_ALIASES = {
    "cycle_basics": ["Menstrual cycle basics"],
    "mood": ["Mood and emotions"],
    "pms_pmdd": ["Mood and emotions", "Safety and escalation"],
    "sleep": ["Sleep"],
    "nutrition_cravings": ["Cravings, appetite, and nutrition"],
    "energy": ["Energy, motivation, and focus"],
    "focus": ["Energy, motivation, and focus"],
    "stress": ["Stress, anxiety, and nervous-system support"],
    "breathing": ["Stress, anxiety, and nervous-system support"],
    "mindfulness": ["Stress, anxiety, and nervous-system support"],
    "journaling": ["Stress, anxiety, and nervous-system support"],
    "urge_surfing": ["Stress, anxiety, and nervous-system support", "Cravings, appetite, and nutrition"],
    "safety": ["Safety and escalation"],
}


def tokenize(text):
    return set(TOKEN_RE.findall((text or "").lower()))


class KnowledgeRepository:
    def __init__(self, db=None, fallback_path=None, embedding_provider=None):
        self.db = db
        self.fallback_path = Path(fallback_path) if fallback_path else None
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self._fallback_chunks = None

    def _load_fallback_chunks(self):
        if self._fallback_chunks is not None:
            return self._fallback_chunks
        chunks = []
        if self.fallback_path and self.fallback_path.exists():
            with self.fallback_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        chunk = json.loads(line)
                        chunk.setdefault("embedding", self.embedding_provider.embed(chunk.get("text", "")))
                        chunks.append(chunk)
        self._fallback_chunks = chunks
        return chunks

    def _firestore_chunks(self):
        if not self.db:
            return []
        try:
            docs = self.db.collection("scientific_facts").stream()
            chunks = []
            for doc in docs:
                data = doc.to_dict()
                text = data.get("retrievalText") or data.get("text") or data.get("fact", "")
                chunks.append({
                    "chunk_id": data.get("chunkId", "rag_" + data.get("factId", doc.id).lower()),
                    "fact_id": data.get("factId", doc.id),
                    "text": text,
                    "metadata": {
                        "topic": data.get("topic", ""),
                        "subtopic": data.get("subtopic", ""),
                        "evidence_bucket": data.get("evidenceBucket", ""),
                        "evidence_strength": data.get("evidenceStrength", ""),
                        "population_scope": data.get("populationScope", ""),
                        "cycle_phase_context": data.get("cyclePhaseContext", ""),
                        "retrieval_keywords": "; ".join(data.get("retrievalKeywords", [])),
                        "source_ids": data.get("sourceIds", []),
                        "safety_relevant": data.get("safetyRelevant", False),
                    },
                    "embedding": data.get("embedding") or self.embedding_provider.embed(text),
                    "fact": data,
                })
            return chunks
        except Exception:
            return []

    def all_chunks(self):
        chunks = self._firestore_chunks()
        return chunks if chunks else self._load_fallback_chunks()

    def resolve_facts(self, fact_ids):
        wanted = set(fact_ids or [])
        if not wanted:
            return []
        return [chunk for chunk in self.all_chunks() if chunk.get("fact_id") in wanted]

    def select_relevant_cached_facts(self, query, cached_facts, limit=3):
        if not cached_facts:
            return []
        query_tokens = tokenize(query)
        scored = []
        for chunk in cached_facts:
            haystack = chunk.get("text", "") + " " + chunk.get("metadata", {}).get("retrieval_keywords", "")
            overlap = len(query_tokens & tokenize(haystack))
            if overlap:
                scored.append((overlap, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk for _, chunk in scored[:limit]]

    def needs_additional_retrieval(self, query, useful_cached_facts, topics):
        if not useful_cached_facts:
            return True
        if not topics:
            return False
        topic_names = set()
        for topic in topics:
            topic_names.update(TOPIC_ALIASES.get(topic, []))
        cached_topics = {chunk.get("metadata", {}).get("topic") for chunk in useful_cached_facts}
        return not bool(topic_names & cached_topics)

    def retrieve(self, query, topics=None, limit=5, include_safety=False):
        chunks = self.all_chunks()
        if not chunks:
            return []

        allowed_topics = set()
        for topic in topics or []:
            allowed_topics.update(TOPIC_ALIASES.get(topic, []))

        query_tokens = tokenize(query)
        query_embedding = self.embedding_provider.embed(query)
        scored = []
        for chunk in chunks:
            metadata = chunk.get("metadata", {})
            topic = metadata.get("topic", "")
            safety_relevant = metadata.get("safety_relevant", False)
            if allowed_topics and topic not in allowed_topics:
                continue
            if not include_safety and safety_relevant and "safety" not in (topics or []):
                continue

            text = chunk.get("text", "")
            keywords = metadata.get("retrieval_keywords", "")
            lexical = len(query_tokens & tokenize(text + " " + keywords))
            semantic = cosine_similarity(query_embedding, chunk.get("embedding") or self.embedding_provider.embed(text))
            evidence = metadata.get("evidence_strength", "").lower()
            evidence_boost = 0.2 if "strong" in evidence else 0.1 if "moderate" in evidence else 0.0
            score = semantic + math.log1p(lexical) + evidence_boost
            if lexical or semantic > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk for _, chunk in scored[:limit]]


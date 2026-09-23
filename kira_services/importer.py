import json
from datetime import datetime
from pathlib import Path

from .embeddings import HashingEmbeddingProvider


def _camel_fact(chunk):
    metadata = chunk.get("metadata", {})
    return {
        "factId": chunk["fact_id"],
        "chunkId": chunk["chunk_id"],
        "retrievalText": chunk["text"],
        "topic": metadata.get("topic", ""),
        "subtopic": metadata.get("subtopic", ""),
        "evidenceBucket": metadata.get("evidence_bucket", ""),
        "evidenceStrength": metadata.get("evidence_strength", ""),
        "populationScope": metadata.get("population_scope", ""),
        "cyclePhaseContext": metadata.get("cycle_phase_context", ""),
        "retrievalKeywords": [
            item.strip()
            for item in metadata.get("retrieval_keywords", "").replace(";", ",").split(",")
            if item.strip()
        ],
        "sourceIds": metadata.get("source_ids", []),
        "safetyRelevant": metadata.get("safety_relevant", False),
    }


def seed_knowledge_base(db, base_dir="knowledge_base", embedding_provider=None):
    base = Path(base_dir)
    embedding_provider = embedding_provider or HashingEmbeddingProvider()
    sources = json.loads((base / "sources.json").read_text(encoding="utf-8"))
    chunks = []
    with (base / "rag_chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                chunks.append(json.loads(line))

    batch = db.batch()
    for source in sources:
        ref = db.collection("scientific_sources").document(source["source_id"])
        payload = dict(source)
        payload["updatedAt"] = datetime.utcnow().isoformat()
        batch.set(ref, payload)

    for chunk in chunks:
        ref = db.collection("scientific_facts").document(chunk["fact_id"])
        payload = _camel_fact(chunk)
        payload["embedding"] = embedding_provider.embed(chunk["text"])
        payload["updatedAt"] = datetime.utcnow().isoformat()
        batch.set(ref, payload)

    batch.commit()
    return {"sources": len(sources), "facts": len(chunks)}


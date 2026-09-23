import hashlib
import math
import re


TOKEN_RE = re.compile(r"[a-z0-9_]+")


class HashingEmbeddingProvider:
    """Small deterministic embedding provider for Kira's curated fact set.

    This is not a replacement for a production embedding model, but it gives the
    retrieval layer a provider-shaped abstraction and a useful semantic-ish
    signal without adding network calls or secrets.
    """

    def __init__(self, dimensions=256):
        self.dimensions = dimensions

    def embed(self, text):
        vector = [0.0] * self.dimensions
        tokens = TOKEN_RE.findall((text or "").lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def embed_many(self, texts):
        return [self.embed(text) for text in texts]


def cosine_similarity(left, right):
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(left[i] * right[i] for i in range(size))


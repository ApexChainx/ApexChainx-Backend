"""Computes an ETag over the full assembled response body, instead of
only the first streamed chunk, to avoid false 304s on multi-chunk bodies.
"""
import hashlib
from typing import AsyncIterable


async def compute_full_body_etag(body_chunks: AsyncIterable[bytes]) -> str:
    hasher = hashlib.sha256()
    async for chunk in body_chunks:
        hasher.update(chunk)
    return hasher.hexdigest()

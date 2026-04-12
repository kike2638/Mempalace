"""
palace.py — Shared palace operations.

Consolidates collection access patterns used by both miners and the MCP server.
The embedding model is stored in collection metadata and read at open time.
"""

import logging
import os

from .backends.chroma import ChromaBackend

from .config import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    EmbeddingModelMismatchError,
    MempalaceConfig,
    get_embedding_function,
    read_collection_metadata,
)

logger = logging.getLogger("mempalace")

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    "coverage",
    ".mempalace",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    ".ipynb_checkpoints",
    ".eggs",
    "htmlcov",
    "target",
}

_DEFAULT_BACKEND = ChromaBackend()


def get_collection(
    palace_path: str,
    collection_name: str = "mempalace_drawers",
    create: bool = True,
    force: bool = False,
    model: str = None,
    chunk_size: int = None,
    chunk_overlap: int = None,
):
    """Get the palace collection through the backend layer.

    Reads the embedding model from collection metadata (set at init time).
    On first creation, stamps the model into metadata.
    Raises EmbeddingModelMismatchError on mismatch unless force=True.

    Args:
        model: Override model for creation (used by init/re-mine).
               If None, reads from existing collection metadata.
        chunk_size: Stored in metadata on creation.
        chunk_overlap: Stored in metadata on creation.
    """
    device = MempalaceConfig.detect_device()

    # Determine the target model:
    # 1. Explicit override (init/re-mine)
    # 2. Read from existing collection metadata
    # 3. Default
    if model:
        current_model = model
    else:
        existing_meta = read_collection_metadata(palace_path, collection_name)
        current_model = existing_meta.get("embedding_model", "chromadb-default")

    ef = get_embedding_function(model_name=current_model, device=device)

    # Build metadata for creation
    create_meta = {"embedding_model": current_model}
    if chunk_size is not None:
        create_meta["chunk_size"] = chunk_size
    if chunk_overlap is not None:
        create_meta["chunk_overlap"] = chunk_overlap

    try:
        col = _DEFAULT_BACKEND.get_collection(
            palace_path,
            collection_name=collection_name,
            create=False,
            embedding_function=ef,
        )
        stored_model = (col.metadata or {}).get("embedding_model")

        if stored_model is None:
            # Legacy palace — stamp with current model
            col.modify(metadata={**(col.metadata or {}), **create_meta})
        elif stored_model != current_model:
            if force:
                logger.warning(
                    "Embedding model mismatch (forced): %s -> %s",
                    stored_model, current_model,
                )
                col.modify(metadata={**(col.metadata or {}), **create_meta})
            else:
                raise EmbeddingModelMismatchError(stored_model, current_model)

        return col
    except EmbeddingModelMismatchError:
        raise
    except Exception:
        if not create:
            raise
        # Set defaults for chunk params on creation
        create_meta.setdefault("chunk_size", chunk_size or DEFAULT_CHUNK_SIZE)
        create_meta.setdefault("chunk_overlap", chunk_overlap or DEFAULT_CHUNK_OVERLAP)
        return _DEFAULT_BACKEND.get_collection(
            palace_path,
            collection_name=collection_name,
            create=True,
            embedding_function=ef,
            metadata=create_meta,
        )


def iter_all_metadatas(collection, where=None, page_size: int = 10000):
    """Yield every metadata entry in the collection, paginating past the page cap."""
    offset = 0
    while True:
        kwargs = {"include": ["metadatas"], "limit": page_size, "offset": offset}
        if where is not None:
            kwargs["where"] = where
        page = collection.get(**kwargs)
        metas = page.get("metadatas") or []
        if not metas:
            break
        for m in metas:
            yield m
        offset += len(metas)


def file_already_mined(collection, source_file: str, check_mtime: bool = False) -> bool:
    """Check if a file has already been filed in the palace."""
    try:
        results = collection.get(where={"source_file": source_file}, limit=1)
        if not results.get("ids"):
            return False
        if check_mtime:
            stored_meta = results.get("metadatas", [{}])[0]
            stored_mtime = stored_meta.get("source_mtime")
            if stored_mtime is None:
                return False
            current_mtime = os.path.getmtime(source_file)
            return abs(float(stored_mtime) - current_mtime) < 0.001
        return True
    except Exception:
        return False

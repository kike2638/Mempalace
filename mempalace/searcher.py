#!/usr/bin/env python3
"""
searcher.py — Find anything. Exact words.

Semantic search against the palace.
Returns verbatim text — the actual words, never summaries.
"""

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import chromadb

logger = logging.getLogger("mempalace_mcp")


def _embed_query_text(collection: Any, text: str) -> list[float]:
    """Chroma collection の埋め込み関数でクエリベクトルを取得する。"""
    ef = getattr(collection, "_embedding_function", None)
    if ef is None:
        return []
    try:
        vecs = ef([text])
        if vecs and isinstance(vecs[0], (list, tuple)):
            return [float(x) for x in vecs[0]]
    except Exception as e:
        logger.debug("query embedding failed: %s", e)
    return []


def _chroma_rows_to_hits(
    docs: list,
    metas: list,
    dists: list,
    ids: list,
    from_expansion: bool,
    embeddings_row: Optional[list] = None,
) -> list[dict[str, Any]]:
    hits = []
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists)):
        drawer_id = ids[i] if i < len(ids) else ""
        meta = meta or {}
        hit: dict[str, Any] = {
            "id": drawer_id,
            "metadata": meta,
            "text": doc,
            "wing": meta.get("wing", "unknown"),
            "room": meta.get("room", "unknown"),
            "source_file": Path(meta.get("source_file", "?")).name,
            "similarity": round(1 - dist, 3),
            "distance": dist,
            "_from_expansion": from_expansion,
        }
        if embeddings_row is not None and i < len(embeddings_row):
            emb = embeddings_row[i]
            if emb is not None:
                if hasattr(emb, "tolist"):
                    emb = emb.tolist()
                if isinstance(emb, list):
                    hit["embedding"] = [float(x) for x in emb]
        hits.append(hit)
    return hits


class SearchError(Exception):
    """Raised when search cannot proceed (e.g. no palace found)."""


def search(query: str, palace_path: str, wing: str = None, room: str = None, n_results: int = 5):
    """
    Search the palace. Returns verbatim drawer content.
    Optionally filter by wing (project) or room (aspect).
    """
    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("mempalace_drawers")
    except Exception:
        print(f"\n  No palace found at {palace_path}")
        print("  Run: mempalace init <dir> then mempalace mine <dir>")
        raise SearchError(f"No palace found at {palace_path}")

    # Build where filter
    where = {}
    if wing and room:
        where = {"$and": [{"wing": wing}, {"room": room}]}
    elif wing:
        where = {"wing": wing}
    elif room:
        where = {"room": room}

    try:
        kwargs = {
            "query_texts": [query],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)

    except Exception as e:
        print(f"\n  Search error: {e}")
        raise SearchError(f"Search error: {e}") from e

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    if not docs:
        print(f'\n  No results found for: "{query}"')
        return

    print(f"\n{'=' * 60}")
    print(f'  Results for: "{query}"')
    if wing:
        print(f"  Wing: {wing}")
    if room:
        print(f"  Room: {room}")
    print(f"{'=' * 60}\n")

    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
        similarity = round(1 - dist, 3)
        source = Path(meta.get("source_file", "?")).name
        wing_name = meta.get("wing", "?")
        room_name = meta.get("room", "?")

        print(f"  [{i}] {wing_name} / {room_name}")
        print(f"      Source: {source}")
        print(f"      Match:  {similarity}")
        print()
        # Print the verbatim text, indented
        for line in doc.strip().split("\n"):
            print(f"      {line}")
        print()
        print(f"  {'─' * 56}")

    print()


def search_memories(
    query: str,
    palace_path: Optional[str] = None,
    wing: Optional[str] = None,
    room: Optional[str] = None,
    n_results: int = 5,
    include_archived: bool = False,
    time_decay: bool = True,
    synapse_ltp_enabled: Optional[bool] = None,
    synapse_tagging_enabled: Optional[bool] = None,
    synapse_association_enabled: Optional[bool] = None,
    synapse_ltp_window_days: Optional[int] = None,
    synapse_ltp_max_boost: Optional[float] = None,
    synapse_tagging_window_hours: Optional[int] = None,
    synapse_tagging_max_boost: Optional[float] = None,
    synapse_profile: Optional[str] = None,
    synapse_half_life_days: Optional[int] = None,
    synapse_association_max_boost: Optional[float] = None,
    synapse_association_coefficient: Optional[float] = None,
) -> dict:
    """
    Programmatic search — returns a dict instead of printing.
    Used by the MCP server and other callers that need data.
    """
    from .config import MempalaceConfig

    _pipeline_start = time.monotonic()
    cfg = MempalaceConfig()
    if palace_path is None:
        palace_path = cfg.palace_path

    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("mempalace_drawers")
    except Exception as e:
        logger.error("No palace found at %s: %s", palace_path, e)
        return {
            "error": "No palace found",
            "hint": "Run: mempalace init <dir> && mempalace mine <dir>",
        }

    # Build where filter
    where = {}
    if wing and room:
        where = {"$and": [{"wing": wing}, {"room": room}]}
    elif wing:
        where = {"wing": wing}
    elif room:
        where = {"room": room}

    if not include_archived and wing is None:
        aw = cfg.synapse_soft_archive_target_wing
        excl = {"wing": {"$ne": aw}}
        if where:
            where = {"$and": [where, excl]}
        else:
            where = excl

    include_cols = ["documents", "metadatas", "distances"]
    if cfg.synapse_enabled:
        include_cols.append("embeddings")

    try:
        kwargs = {
            "query_texts": [query],
            "n_results": n_results,
            "include": include_cols,
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)
    except Exception as e:
        return {"error": f"Search error: {e}"}

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]
    ids = results.get("ids", [[]])[0]
    emb_row = results.get("embeddings", [[]])[0] if cfg.synapse_enabled else None

    hits = _chroma_rows_to_hits(
        docs, metas, dists, ids, from_expansion=False, embeddings_row=emb_row
    )

    result: dict[str, Any] = {
        "query": query,
        "filters": {"wing": wing, "room": room},
        "results": hits,
        "hits": hits,
    }

    # --- Synapse integration ---
    try:
        if cfg.synapse_enabled:
            from .synapse import SynapseDB
            from .synapse_profiles import (
                ProfileManager,
                compute_decay,
                global_merged_from_mempalace_config,
                hit_filed_age_days,
            )

            pm = ProfileManager(palace_path)
            per_query: dict[str, Any] = {}
            if synapse_half_life_days is not None:
                per_query["half_life_days"] = synapse_half_life_days
            if synapse_ltp_enabled is not None:
                per_query["ltp_enabled"] = synapse_ltp_enabled
            if synapse_ltp_window_days is not None:
                per_query["ltp_window_days"] = synapse_ltp_window_days
            if synapse_ltp_max_boost is not None:
                per_query["ltp_max_boost"] = synapse_ltp_max_boost
            if synapse_tagging_enabled is not None:
                per_query["tagging_enabled"] = synapse_tagging_enabled
            if synapse_tagging_window_hours is not None:
                per_query["tagging_window_hours"] = synapse_tagging_window_hours
            if synapse_tagging_max_boost is not None:
                per_query["tagging_max_boost"] = synapse_tagging_max_boost
            if synapse_association_enabled is not None:
                per_query["association_enabled"] = synapse_association_enabled
            if synapse_association_max_boost is not None:
                per_query["association_max_boost"] = synapse_association_max_boost
            if synapse_association_coefficient is not None:
                per_query["association_coefficient"] = synapse_association_coefficient

            profile = pm.resolve(
                synapse_profile,
                per_query_overrides=per_query or None,
                global_merged=global_merged_from_mempalace_config(cfg),
            )
            pd = profile.to_dict()

            result["synapse_requested_profile"] = synapse_profile
            result["synapse_profile_used"] = profile.name

            synapse_db = SynapseDB(palace_path)
            query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
            session_id = uuid.uuid4().hex[:16]
            total_candidates_in = 0

            query_embedding = _embed_query_text(col, query)

            expansion_metadata: dict[str, Any] = {"applied": False}
            expanded_terms: list[str] = []
            if pd.get("query_expansion_enabled", False):
                er = synapse_db.expand_query(
                    col,
                    query,
                    query_embedding,
                    max_expansions=int(pd.get("query_expansion_max_terms", 3)),
                    similarity_threshold=float(
                        pd.get("query_expansion_similarity_threshold", 0.65)
                    ),
                )
                expanded_terms = er.get("expansion_terms") or []
                boost = float(pd.get("query_expansion_boost", 0.7))
                expansion_metadata = {
                    "applied": True,
                    "original_query": query,
                    "similar_past_queries": er.get("similar_past_queries", []),
                    "expansion_terms": expanded_terms,
                    "expansion_boost": boost,
                }
            else:
                boost = float(pd.get("query_expansion_boost", 0.7))

            merged_by_id: dict[str, dict[str, Any]] = {}
            original_ids: set[str] = set()
            for h in hits:
                hid = h.get("id", "")
                merged_by_id[hid] = h
                if hid:
                    original_ids.add(hid)

            if expanded_terms:
                for term in expanded_terms:
                    eq = f"{query} {term}"
                    try:
                        qkwargs = {
                            "query_texts": [eq],
                            "n_results": n_results,
                            "include": include_cols,
                        }
                        if where:
                            qkwargs["where"] = where
                        exr = col.query(**qkwargs)
                        edocs = exr["documents"][0]
                        emetas = exr["metadatas"][0]
                        edists = exr["distances"][0]
                        eids = exr.get("ids", [[]])[0]
                        eer = (
                            exr.get("embeddings", [[]])[0]
                            if cfg.synapse_enabled
                            else None
                        )
                        exhits = _chroma_rows_to_hits(
                            edocs,
                            emetas,
                            edists,
                            eids,
                            from_expansion=True,
                            embeddings_row=eer,
                        )
                        for eh in exhits:
                            eid = eh.get("id", "")
                            if eid and eid not in merged_by_id:
                                merged_by_id[eid] = eh
                    except Exception as ex:
                        logger.warning("expansion query failed: %s", ex)

            result["hits"] = list(merged_by_id.values())
            total_candidates_in = len(merged_by_id)
            if expansion_metadata.get("applied"):
                expansion_metadata["results_from_original"] = len(original_ids)
                expansion_metadata["results_from_expansion"] = max(
                    0, len(merged_by_id) - len(original_ids)
                )
            result["synapse_query_expansion"] = expansion_metadata

            hit_drawer_ids = []
            for hit in result["hits"]:
                drawer_id = hit.get("metadata", {}).get("drawer_id", hit.get("id", ""))
                if drawer_id:
                    hit_drawer_ids.append(drawer_id)

            with synapse_db.connection() as conn:
                ltp_scores: dict[str, float] = {}
                if profile.ltp_enabled and hit_drawer_ids:
                    ltp_scores = synapse_db.get_ltp_scores_batch(
                        hit_drawer_ids,
                        window_days=profile.ltp_window_days,
                        max_boost=profile.ltp_max_boost,
                        conn=conn,
                    )

                assoc_scores: dict[str, float] = {}
                if profile.association_enabled and hit_drawer_ids:
                    assoc_scores = synapse_db.get_association_scores_batch(
                        hit_drawer_ids,
                        max_boost=profile.association_max_boost,
                        coefficient=profile.association_coefficient,
                        conn=conn,
                    )

                for hit in result["hits"]:
                    drawer_id = hit.get("metadata", {}).get("drawer_id", hit.get("id", ""))
                    filed_at = hit.get("metadata", {}).get("filed_at", None)
                    similarity = float(
                        hit.get("original_similarity", hit.get("similarity", 0.0))
                    )
                    age_days = hit_filed_age_days(filed_at)
                    decay = (
                        compute_decay(age_days, int(profile.half_life_days))
                        if time_decay
                        else 1.0
                    )

                    ltp = ltp_scores.get(drawer_id, 1.0) if profile.ltp_enabled else 1.0
                    tagging = (
                        SynapseDB.calculate_tagging_boost(
                            filed_at,
                            window_hours=profile.tagging_window_hours,
                            max_boost=profile.tagging_max_boost,
                        )
                        if profile.tagging_enabled
                        else 1.0
                    )
                    association = (
                        assoc_scores.get(drawer_id, 1.0)
                        if profile.association_enabled
                        else 1.0
                    )

                    final_score = similarity * decay * ltp * association * tagging
                    if hit.get("_from_expansion"):
                        final_score *= boost

                    hit["synapse_score"] = final_score
                    hit["synapse_factors"] = {
                        "similarity": similarity,
                        "decay": decay,
                        "ltp": ltp,
                        "association": association,
                        "tagging": tagging,
                    }
                    hit["synapse_profile"] = profile.name

                result["hits"].sort(
                    key=lambda h: h.get("synapse_score", h.get("similarity", 0.0)),
                    reverse=True,
                )

                # Same drawer: prefer original (non-expansion) — re-sort stable by
                # stripping expansion penalty when duplicate ids (merged_by_id already deduped)

                result["synapse_enabled"] = True
                result["synapse_profile"] = profile.to_dict()

                hits_after_score = result["hits"]

                # Phase 8 — Supersede
                if pd.get("supersede_filter_enabled", False):
                    sres = synapse_db.detect_superseded(
                        col,
                        [h.get("id") for h in hits_after_score if h.get("id")],
                        similarity_threshold=float(
                            pd.get("supersede_similarity_threshold", 0.86)
                        ),
                        min_age_gap_days=int(pd.get("supersede_min_age_gap_days", 7)),
                        max_candidates=int(pd.get("supersede_max_candidates", 10)),
                    )
                    filt = synapse_db.apply_supersede_filter(
                        hits_after_score,
                        sres,
                        action=str(pd.get("supersede_action", "filter")),
                    )
                    hits_after_score = filt["results"]
                    result["synapse_supersede"] = filt["synapse_supersede"]
                else:
                    result["synapse_supersede"] = {"checked": False}

                # Phase 9 — Consolidation resolve
                consolidation_metadata: dict[str, Any] = {"applied": False}
                if pd.get("include_consolidated_summaries", True):
                    include_sources = pd.get("include_consolidated_sources", False)
                    consolidated_removed: list[str] = []
                    consolidated_sources_nested = 0

                    if include_sources:
                        source_groups: dict[str, list[dict[str, Any]]] = {}
                        non_consolidated_hits: list[dict[str, Any]] = []
                        for hit in hits_after_score:
                            meta = hit.get("metadata") or {}
                            st = meta.get("status", "active")
                            if st == "consolidated":
                                into = meta.get("consolidated_into") or ""
                                if into:
                                    consolidated_sources_nested += 1
                                    text = hit.get("text") or ""
                                    source_groups.setdefault(into, []).append(
                                        {
                                            "id": hit.get("id", ""),
                                            "title": meta.get("title", text[:50]),
                                            "date": meta.get(
                                                "created_at",
                                                meta.get("filed_at", ""),
                                            ),
                                            "content_preview": text[:200],
                                        }
                                    )
                                else:
                                    non_consolidated_hits.append(hit)
                            else:
                                non_consolidated_hits.append(hit)

                        present = {h.get("id") for h in non_consolidated_hits}
                        for cid in dict.fromkeys(list(source_groups.keys())):
                            if cid and cid not in present:
                                try:
                                    got = col.get(
                                        ids=[cid],
                                        include=["documents", "metadatas", "embeddings"],
                                    )
                                    if got.get("ids"):
                                        doc = (got.get("documents") or [""])[0]
                                        meta = (got.get("metadatas") or [{}])[0] or {}
                                        emb = (got.get("embeddings") or [None])[0]
                                        dist = 0.5
                                        nh = _chroma_rows_to_hits(
                                            [doc],
                                            [meta],
                                            [dist],
                                            [cid],
                                            from_expansion=False,
                                            embeddings_row=[emb] if emb else None,
                                        )[0]
                                        nh["similarity"] = 0.5
                                        nh["synapse_score"] = 0.5
                                        non_consolidated_hits.append(nh)
                                        present.add(cid)
                                except Exception:
                                    pass

                        for hit in non_consolidated_hits:
                            meta = hit.get("metadata") or {}
                            if meta.get("status") == "consolidated_summary":
                                cid = hit.get("id", "")
                                if cid in source_groups:
                                    hit["synapse_consolidation"] = {
                                        "is_consolidated": True,
                                        "source_count": len(source_groups[cid]),
                                        "sources": source_groups[cid],
                                    }
                                elif meta.get("source_drawers"):
                                    try:
                                        sids = json.loads(meta["source_drawers"])
                                        if isinstance(sids, list):
                                            hit["synapse_consolidation"] = {
                                                "is_consolidated": True,
                                                "source_count": len(sids),
                                                "sources": [{"id": sid} for sid in sids],
                                            }
                                    except (json.JSONDecodeError, TypeError):
                                        pass

                        hits_after_score = non_consolidated_hits
                        consolidation_metadata = {
                            "applied": True,
                            "consolidated_sources_hidden": 0,
                            "consolidated_sources_nested": consolidated_sources_nested,
                            "include_sources_as_metadata": True,
                            "include_sources": True,
                        }
                    else:
                        new_hits: list[dict[str, Any]] = []
                        to_fetch_summary: list[str] = []
                        for hit in hits_after_score:
                            meta = hit.get("metadata") or {}
                            if meta.get("status") == "consolidated":
                                consolidated_removed.append(hit.get("id", ""))
                                into = meta.get("consolidated_into")
                                if into:
                                    to_fetch_summary.append(str(into))
                            else:
                                new_hits.append(hit)

                        present = {h.get("id") for h in new_hits}
                        for cid in dict.fromkeys(to_fetch_summary):
                            if cid and cid not in present:
                                try:
                                    got = col.get(
                                        ids=[cid],
                                        include=["documents", "metadatas", "embeddings"],
                                    )
                                    if got.get("ids"):
                                        doc = (got.get("documents") or [""])[0]
                                        meta = (got.get("metadatas") or [{}])[0] or {}
                                        emb = (got.get("embeddings") or [None])[0]
                                        dist = 0.5
                                        nh = _chroma_rows_to_hits(
                                            [doc],
                                            [meta],
                                            [dist],
                                            [cid],
                                            from_expansion=False,
                                            embeddings_row=[emb] if emb else None,
                                        )[0]
                                        nh["similarity"] = 0.5
                                        nh["synapse_score"] = 0.5
                                        new_hits.append(nh)
                                        present.add(cid)
                                except Exception:
                                    pass

                        hits_after_score = new_hits
                        consolidation_metadata = {
                            "applied": True,
                            "consolidated_sources_hidden": len(consolidated_removed),
                            "consolidated_sources_nested": 0,
                            "include_sources_as_metadata": False,
                            "include_sources": False,
                        }
                result["synapse_consolidation"] = consolidation_metadata

                # Phase 5 — MMR
                if pd.get("mmr_enabled", False):
                    mmr_out = synapse_db.apply_mmr(
                        hits_after_score,
                        query_embedding,
                        lambda_param=float(pd.get("mmr_lambda", 0.7)),
                        final_k=int(pd.get("mmr_final_k", 5)),
                    )
                    hits_after_score = mmr_out["results"]
                    result["synapse_mmr"] = mmr_out["mmr_metadata"]
                else:
                    result["synapse_mmr"] = {"applied": False}

                for h in hits_after_score:
                    h.pop("_from_expansion", None)

                result["hits"] = hits_after_score
                result["results"] = hits_after_score

                phases_applied: list[str] = []
                phases_skipped: list[str] = []
                if result.get("synapse_query_expansion", {}).get("applied"):
                    phases_applied.append("query_expansion")
                else:
                    phases_skipped.append("query_expansion")

                ss = result.get("synapse_supersede") or {}
                if ss.get("checked"):
                    phases_applied.append(
                        "supersede_" + str(ss.get("action", "filter"))
                    )
                else:
                    phases_skipped.append("supersede")

                sc = result.get("synapse_consolidation") or {}
                if sc.get("applied"):
                    phases_applied.append("consolidation")
                else:
                    phases_skipped.append("consolidation")

                sm = result.get("synapse_mmr") or {}
                if sm.get("applied"):
                    phases_applied.append("mmr")
                else:
                    phases_skipped.append("mmr")

                result["synapse_pipeline"] = {
                    "phases_applied": phases_applied,
                    "phases_skipped": phases_skipped,
                    "total_candidates_in": total_candidates_in,
                    "total_results_out": len(hits_after_score),
                    "profile_used": result.get("synapse_profile_used", "default"),
                    "elapsed_ms": round(
                        (time.monotonic() - _pipeline_start) * 1000, 1
                    ),
                }

                if cfg.synapse_log_retrievals:
                    log_ids = [
                        hit.get("metadata", {}).get("drawer_id", hit.get("id", ""))
                        for hit in hits_after_score
                    ]
                    log_ids = [x for x in log_ids if x]
                    if log_ids:
                        try:
                            synapse_db.log_retrieval(
                                log_ids, query_hash, session_id, conn=conn
                            )
                        except Exception as e:
                            logger.warning(
                                "Synapse log_retrieval failed (non-fatal): %s", e
                            )

            try:
                synapse_db.log_query(
                    query,
                    query_embedding,
                    [h.get("id", "") for h in result["hits"]],
                    [
                        float(h.get("synapse_score", h.get("similarity", 0.0)))
                        for h in result["hits"]
                    ],
                )
            except Exception:
                pass
        else:
            result["synapse_enabled"] = False
            result["synapse_query_expansion"] = {"applied": False}
            result["synapse_supersede"] = {"checked": False}
            result["synapse_consolidation"] = {"applied": False}
            result["synapse_mmr"] = {"applied": False}
    except Exception as e:
        logger.warning("Synapse scoring skipped: %s", e)
        result["synapse_enabled"] = False
        result["synapse_query_expansion"] = {"applied": False}
        result["synapse_supersede"] = {"checked": False}
        result["synapse_consolidation"] = {"applied": False}
        result["synapse_mmr"] = {"applied": False}

    if not result.get("synapse_enabled"):
        result.pop("synapse_pipeline", None)

    return result

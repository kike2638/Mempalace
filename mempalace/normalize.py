#!/usr/bin/env python3
"""
normalize.py — Convert any chat export format to MemPalace transcript format.

Supported:
    - Plain text with > markers (pass through)
    - Claude.ai JSON export
    - ChatGPT conversations.json
    - Claude Code JSONL
    - OpenAI Codex CLI JSONL
    - Slack JSON export
    - Plain text (pass through for paragraph chunking)

No API key. No internet. Everything local.
"""

import json
import os
from pathlib import Path
from typing import Optional


class ChatGPTNormalizeError(ValueError):
    """Raised when a ChatGPT export cannot be safely normalized."""


class ChatGPTBranchAmbiguityError(ChatGPTNormalizeError):
    """Raised when a ChatGPT mapping tree has multiple candidate branches."""


def normalize(filepath: str) -> str:
    """
    Load a file and normalize to transcript format if it's a chat export.
    Plain text files pass through unchanged.
    """
    try:
        file_size = os.path.getsize(filepath)
    except OSError as e:
        raise IOError(f"Could not read {filepath}: {e}")
    if file_size > 500 * 1024 * 1024:  # 500 MB safety limit
        raise IOError(f"File too large ({file_size // (1024*1024)} MB): {filepath}")
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        raise IOError(f"Could not read {filepath}: {e}")

    if not content.strip():
        return content

    # Already has > markers — pass through
    lines = content.split("\n")
    if sum(1 for line in lines if line.strip().startswith(">")) >= 3:
        return content

    # Try JSON normalization
    ext = Path(filepath).suffix.lower()
    if ext in (".json", ".jsonl") or content.strip()[:1] in ("{", "["):
        normalized = _try_normalize_json(content)
        if normalized:
            return normalized

    return content


def _try_normalize_json(content: str) -> Optional[str]:
    """Try all known JSON chat schemas."""

    normalized = _try_claude_code_jsonl(content)
    if normalized:
        return normalized

    normalized = _try_codex_jsonl(content)
    if normalized:
        return normalized

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None

    for parser in (_try_claude_ai_json, _try_chatgpt_json, _try_slack_json):
        normalized = parser(data)
        if normalized:
            return normalized

    return None


def _try_claude_code_jsonl(content: str) -> Optional[str]:
    """Claude Code JSONL sessions."""
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
    messages = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        msg_type = entry.get("type", "")
        message = entry.get("message", {})
        if msg_type in ("human", "user"):
            text = _extract_content(message.get("content", ""))
            if text:
                messages.append(("user", text))
        elif msg_type == "assistant":
            text = _extract_content(message.get("content", ""))
            if text:
                messages.append(("assistant", text))
    if len(messages) >= 2:
        return _messages_to_transcript(messages)
    return None


def _try_codex_jsonl(content: str) -> Optional[str]:
    """OpenAI Codex CLI sessions (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl).

    Uses only event_msg entries (user_message / agent_message) which represent
    the canonical conversation turns. response_item entries are skipped because
    they include synthetic context injections and duplicate the real messages.
    """
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
    messages = []
    has_session_meta = False
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        entry_type = entry.get("type", "")
        if entry_type == "session_meta":
            has_session_meta = True
            continue

        if entry_type != "event_msg":
            continue

        payload = entry.get("payload", {})
        if not isinstance(payload, dict):
            continue

        payload_type = payload.get("type", "")
        msg = payload.get("message")
        if not isinstance(msg, str):
            continue
        text = msg.strip()
        if not text:
            continue

        if payload_type == "user_message":
            messages.append(("user", text))
        elif payload_type == "agent_message":
            messages.append(("assistant", text))

    if len(messages) >= 2 and has_session_meta:
        return _messages_to_transcript(messages)
    return None


def _try_claude_ai_json(data) -> Optional[str]:
    """Claude.ai JSON export: flat messages list or privacy export with chat_messages."""
    if isinstance(data, dict):
        data = data.get("messages", data.get("chat_messages", []))
    if not isinstance(data, list):
        return None

    # Privacy export: array of conversation objects with chat_messages inside each
    if data and isinstance(data[0], dict) and "chat_messages" in data[0]:
        all_messages = []
        for convo in data:
            if not isinstance(convo, dict):
                continue
            chat_msgs = convo.get("chat_messages", [])
            for item in chat_msgs:
                if not isinstance(item, dict):
                    continue
                role = item.get("role", "")
                text = _extract_content(item.get("content", ""))
                if role in ("user", "human") and text:
                    all_messages.append(("user", text))
                elif role in ("assistant", "ai") and text:
                    all_messages.append(("assistant", text))
        if len(all_messages) >= 2:
            return _messages_to_transcript(all_messages)
        return None

    # Flat messages list
    messages = []
    for item in data:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "")
        text = _extract_content(item.get("content", ""))
        if role in ("user", "human") and text:
            messages.append(("user", text))
        elif role in ("assistant", "ai") and text:
            messages.append(("assistant", text))
    if len(messages) >= 2:
        return _messages_to_transcript(messages)
    return None


def _try_chatgpt_json(data) -> Optional[str]:
    """ChatGPT conversations.json with mapping tree."""
    if not isinstance(data, dict) or "mapping" not in data:
        return None
    mapping = data["mapping"]
    if not isinstance(mapping, dict):
        raise ChatGPTNormalizeError("Invalid ChatGPT export: mapping must be an object")

    root_id = _find_chatgpt_root_id(mapping)
    if not root_id:
        raise ChatGPTNormalizeError("Invalid ChatGPT export: could not find mapping root")

    reachable_ids = _collect_chatgpt_reachable_ids(mapping, root_id)
    active_node_id = _resolve_chatgpt_active_node_id(data, mapping, root_id)
    path_ids = _build_chatgpt_path(mapping, active_node_id, root_id, reachable_ids)
    if not path_ids:
        raise ChatGPTNormalizeError("Invalid ChatGPT export: could not resolve active conversation path")

    messages = []
    for node_id in path_ids:
        node = mapping.get(node_id, {})
        msg = node.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("author", {}).get("role", "")
        text = _extract_chatgpt_text(msg.get("content", {}))
        if role == "user" and text:
            messages.append(("user", text))
        elif role == "assistant" and text:
            messages.append(("assistant", text))
    if len(messages) >= 2:
        return _messages_to_transcript(messages)
    raise ChatGPTNormalizeError("Invalid ChatGPT export: active conversation path did not yield enough messages")


def _find_chatgpt_root_id(mapping: dict) -> Optional[str]:
    """Prefer the synthetic root; fall back to any parent-less node."""
    root_id = None
    fallback_root = None
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        if node.get("parent") is None:
            if node.get("message") is None:
                root_id = node_id
                break
            if fallback_root is None:
                fallback_root = node_id
    return root_id or fallback_root


def _resolve_chatgpt_active_node_id(data: dict, mapping: dict, root_id: Optional[str]) -> Optional[str]:
    """Use the export's active node when available; otherwise require a single path."""
    if "current_node" in data:
        current_node = data.get("current_node")
        if current_node in mapping:
            return current_node
        raise ChatGPTNormalizeError("Invalid ChatGPT export: current_node does not reference a mapping node")
    if not root_id:
        return None

    leaf_ids = _collect_chatgpt_leaf_ids(mapping, root_id)
    if len(leaf_ids) == 1:
        return leaf_ids[0]
    if len(leaf_ids) > 1:
        raise ChatGPTBranchAmbiguityError(
            "Ambiguous ChatGPT mapping tree: multiple candidate conversation branches without current_node"
        )
    return root_id


def _collect_chatgpt_leaf_ids(mapping: dict, root_id: str) -> list[str]:
    """Return reachable leaves from root using only valid child references."""
    leaf_ids = []
    for node_id in _collect_chatgpt_reachable_ids(mapping, root_id):
        node = mapping.get(node_id, {})
        if not isinstance(node, dict):
            continue
        child_ids = [child_id for child_id in node.get("children", []) if child_id in mapping]
        if not child_ids:
            leaf_ids.append(node_id)

    return leaf_ids


def _collect_chatgpt_reachable_ids(mapping: dict, root_id: str) -> list[str]:
    """Return nodes reachable from root following children edges only."""
    reachable_ids = []
    stack = [root_id]
    visited = set()

    while stack:
        node_id = stack.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        reachable_ids.append(node_id)

        node = mapping.get(node_id, {})
        if not isinstance(node, dict):
            continue
        child_ids = [child_id for child_id in node.get("children", []) if child_id in mapping]
        stack.extend(reversed(child_ids))

    return reachable_ids


def _build_chatgpt_path(
    mapping: dict, active_node_id: Optional[str], root_id: Optional[str], reachable_ids: list[str]
) -> list[str]:
    """Walk from the active node back to root and return the ordered path."""
    if not active_node_id or active_node_id not in mapping:
        return []

    path_ids = []
    current_id = active_node_id
    visited = set()

    while current_id and current_id not in visited:
        visited.add(current_id)
        path_ids.append(current_id)
        node = mapping.get(current_id, {})
        if not isinstance(node, dict):
            break
        parent_id = node.get("parent")
        current_id = parent_id if parent_id in mapping else None

    path_ids.reverse()
    if root_id and (not path_ids or path_ids[0] != root_id):
        return []
    reachable_id_set = set(reachable_ids)
    if any(node_id not in reachable_id_set for node_id in path_ids):
        return []
    return path_ids


def _extract_chatgpt_text(content) -> str:
    """Extract plain text from ChatGPT message content blocks."""
    if isinstance(content, dict):
        parts = content.get("parts", [])
        if isinstance(parts, list):
            return " ".join(str(part) for part in parts if isinstance(part, str) and part).strip()
    return ""


def _try_slack_json(data) -> Optional[str]:
    """
    Slack channel export: [{"type": "message", "user": "...", "text": "..."}]
    Optimized for 2-person DMs. In channels with 3+ people, alternating
    speakers are labeled user/assistant to preserve the exchange structure.
    """
    if not isinstance(data, list):
        return None
    messages = []
    seen_users = {}
    last_role = None
    for item in data:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        user_id = item.get("user", item.get("username", ""))
        text = item.get("text", "").strip()
        if not text or not user_id:
            continue
        if user_id not in seen_users:
            # Alternate roles so exchange chunking works with any number of speakers
            if not seen_users:
                seen_users[user_id] = "user"
            elif last_role == "user":
                seen_users[user_id] = "assistant"
            else:
                seen_users[user_id] = "user"
        last_role = seen_users[user_id]
        messages.append((seen_users[user_id], text))
    if len(messages) >= 2:
        return _messages_to_transcript(messages)
    return None


def _extract_content(content) -> str:
    """Pull text from content — handles str, list of blocks, or dict."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts).strip()
    if isinstance(content, dict):
        return content.get("text", "").strip()
    return ""


def _messages_to_transcript(messages: list, spellcheck: bool = True) -> str:
    """Convert [(role, text), ...] to transcript format with > markers."""
    if spellcheck:
        try:
            from mempalace.spellcheck import spellcheck_user_text

            _fix = spellcheck_user_text
        except ImportError:
            _fix = None
    else:
        _fix = None

    lines = []
    i = 0
    while i < len(messages):
        role, text = messages[i]
        if role == "user":
            if _fix is not None:
                text = _fix(text)
            lines.append(f"> {text}")
            if i + 1 < len(messages) and messages[i + 1][0] == "assistant":
                lines.append(messages[i + 1][1])
                i += 2
            else:
                i += 1
        else:
            lines.append(text)
            i += 1
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python normalize.py <filepath>")
        sys.exit(1)
    filepath = sys.argv[1]
    result = normalize(filepath)
    quote_count = sum(1 for line in result.split("\n") if line.strip().startswith(">"))
    print(f"\nFile: {os.path.basename(filepath)}")
    print(f"Normalized: {len(result)} chars | {quote_count} user turns detected")
    print("\n--- Preview (first 20 lines) ---")
    print("\n".join(result.split("\n")[:20]))

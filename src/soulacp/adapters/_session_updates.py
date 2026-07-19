"""Validation for stable ACP session updates surfaced through callbacks."""

from __future__ import annotations

from typing import Any

from soulacp.config import _is_finite_number
from soulacp.errors import ACPProtocolError

_CONTENT_CHUNK_UPDATES = frozenset(
    {
        "user_message_chunk",
        "agent_message_chunk",
        "agent_thought_chunk",
    }
)
_STABLE_SESSION_UPDATES = _CONTENT_CHUNK_UPDATES | {
    "tool_call",
    "tool_call_update",
    "plan",
    "available_commands_update",
    "current_mode_update",
    "config_option_update",
    "session_info_update",
    "usage_update",
}
_TOOL_KINDS = frozenset(
    {
        "read",
        "edit",
        "delete",
        "move",
        "search",
        "execute",
        "think",
        "fetch",
        "switch_mode",
        "other",
    }
)
_TOOL_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})
_PLAN_PRIORITIES = frozenset({"high", "medium", "low"})
_PLAN_STATUSES = frozenset({"pending", "in_progress", "completed"})


def _validate_meta(value: dict[str, Any], source: str) -> None:
    meta = value.get("_meta")
    if meta is not None and not isinstance(meta, dict):
        raise ACPProtocolError(f"{source} _meta must be an object or null")


def _validate_annotations(value: Any, source: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ACPProtocolError(f"{source} annotations must be an object or null")
    audience = value.get("audience")
    if audience is not None and (
        not isinstance(audience, list)
        or any(not isinstance(role, str) or role not in {"assistant", "user"} for role in audience)
    ):
        raise ACPProtocolError(f"{source} annotations.audience must contain only assistant or user")
    last_modified = value.get("lastModified")
    if last_modified is not None and not isinstance(last_modified, str):
        raise ACPProtocolError(f"{source} annotations.lastModified must be a string or null")
    priority = value.get("priority")
    if priority is not None and (
        isinstance(priority, bool) or not isinstance(priority, (int, float)) or not _is_finite_number(priority)
    ):
        raise ACPProtocolError(f"{source} annotations.priority must be a finite number or null")
    _validate_meta(value, f"{source} annotations")


def _require_string(value: dict[str, Any], field: str, source: str) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise ACPProtocolError(f"{source} requires string {field}")
    return result


def _validate_optional_strings(value: dict[str, Any], fields: tuple[str, ...], source: str) -> None:
    for field in fields:
        field_value = value.get(field)
        if field_value is not None and not isinstance(field_value, str):
            raise ACPProtocolError(f"{source} {field} must be a string or null")


def _validate_content_block(content: Any, source: str) -> None:
    if not isinstance(content, dict):
        raise ACPProtocolError(f"{source} content must be an object")
    content_type = content.get("type")
    if content_type == "text":
        _require_string(content, "text", source)
    elif isinstance(content_type, str) and content_type in {"image", "audio"}:
        _require_string(content, "data", source)
        _require_string(content, "mimeType", source)
        if content_type == "image":
            _validate_optional_strings(content, ("uri",), source)
    elif content_type == "resource_link":
        _require_string(content, "name", source)
        _require_string(content, "uri", source)
        _validate_optional_strings(content, ("description", "mimeType", "title"), source)
        size = content.get("size")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or not -(2**63) <= size < 2**63):
            raise ACPProtocolError(f"{source} size must be an int64 integer or null")
    elif content_type == "resource":
        resource = content.get("resource")
        if not isinstance(resource, dict):
            raise ACPProtocolError(f"{source} resource must be an object")
        _require_string(resource, "uri", f"{source} resource")
        text = resource.get("text")
        blob = resource.get("blob")
        if not isinstance(text, str) and not isinstance(blob, str):
            raise ACPProtocolError(f"{source} resource requires string text or blob")
        _validate_optional_strings(resource, ("mimeType",), f"{source} resource")
        _validate_meta(resource, f"{source} resource")
    else:
        raise ACPProtocolError(f"{source} has an unknown content type")

    _validate_annotations(content.get("annotations"), source)
    _validate_meta(content, source)


def _validate_content_chunk(update: dict[str, Any], source: str) -> None:
    _validate_content_block(update.get("content"), source)
    message_id = update.get("messageId")
    if message_id is not None and not isinstance(message_id, str):
        raise ACPProtocolError(f"{source} messageId must be a string or null")


def _validate_tool_call_content(value: Any, source: str) -> None:
    if not isinstance(value, list):
        raise ACPProtocolError(f"{source} content must be an array or null")
    for index, item in enumerate(value):
        item_source = f"{source} content entry {index}"
        if not isinstance(item, dict):
            raise ACPProtocolError(f"{item_source} must be an object")
        item_type = item.get("type")
        if item_type == "content":
            _validate_content_block(item.get("content"), item_source)
        elif item_type == "diff":
            _require_string(item, "path", item_source)
            _require_string(item, "newText", item_source)
            _validate_optional_strings(item, ("oldText",), item_source)
        elif item_type == "terminal":
            _require_string(item, "terminalId", item_source)
        else:
            raise ACPProtocolError(f"{item_source} has an unknown type")
        _validate_meta(item, item_source)


def _validate_tool_locations(value: Any, source: str) -> None:
    if not isinstance(value, list):
        raise ACPProtocolError(f"{source} locations must be an array or null")
    for index, location in enumerate(value):
        item_source = f"{source} location {index}"
        if not isinstance(location, dict):
            raise ACPProtocolError(f"{item_source} must be an object")
        _require_string(location, "path", item_source)
        line = location.get("line")
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or not 0 <= line < 2**32):
            raise ACPProtocolError(f"{item_source} line must be a uint32 integer or null")
        _validate_meta(location, item_source)


def _validate_tool_call(update: dict[str, Any], source: str, *, partial: bool) -> None:
    _require_string(update, "toolCallId", source)
    if partial:
        _validate_optional_strings(update, ("title",), source)
    else:
        _require_string(update, "title", source)

    kind = update.get("kind")
    if (kind is not None or (not partial and "kind" in update)) and (
        not isinstance(kind, str) or kind not in _TOOL_KINDS
    ):
        raise ACPProtocolError(f"{source} kind is invalid")
    status = update.get("status")
    if (status is not None or (not partial and "status" in update)) and (
        not isinstance(status, str) or status not in _TOOL_STATUSES
    ):
        raise ACPProtocolError(f"{source} status is invalid")
    content = update.get("content")
    if content is not None or (not partial and "content" in update):
        _validate_tool_call_content(content, source)
    locations = update.get("locations")
    if locations is not None or (not partial and "locations" in update):
        _validate_tool_locations(locations, source)


def _validate_plan(update: dict[str, Any], source: str) -> None:
    entries = update.get("entries")
    if not isinstance(entries, list):
        raise ACPProtocolError(f"{source} requires an entries array")
    for index, entry in enumerate(entries):
        item_source = f"{source} entry {index}"
        if not isinstance(entry, dict):
            raise ACPProtocolError(f"{item_source} must be an object")
        _require_string(entry, "content", item_source)
        priority = entry.get("priority")
        if not isinstance(priority, str) or priority not in _PLAN_PRIORITIES:
            raise ACPProtocolError(f"{item_source} priority is invalid")
        status = entry.get("status")
        if not isinstance(status, str) or status not in _PLAN_STATUSES:
            raise ACPProtocolError(f"{item_source} status is invalid")
        _validate_meta(entry, item_source)


def validate_stable_session_update(update: dict[str, Any]) -> None:
    """Validate stable update metadata and callback-only payload structures."""
    update_type = update.get("sessionUpdate")
    if update_type not in _STABLE_SESSION_UPDATES:
        return
    _validate_meta(update, update_type)
    if update_type in _CONTENT_CHUNK_UPDATES:
        _validate_content_chunk(update, update_type)
    elif update_type == "tool_call":
        _validate_tool_call(update, update_type, partial=False)
    elif update_type == "tool_call_update":
        _validate_tool_call(update, update_type, partial=True)
    elif update_type == "plan":
        _validate_plan(update, update_type)


def validate_permission_tool_call(value: Any) -> None:
    """Validate the ToolCallUpdate embedded in an ACP v1 permission request."""
    source = "permission request toolCall"
    if not isinstance(value, dict):
        raise ACPProtocolError(f"{source} must be an object")
    _validate_meta(value, source)
    _validate_tool_call(value, source, partial=True)

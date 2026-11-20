"""Make an ADK agent's A2UI output render in the ADK dev UI (`adk web`).

`adk web` has a built-in A2UI renderer, but it only fires when a response part is
a text/plain Blob wrapped in <a2a_datapart_json>...</a2a_datapart_json> with
custom_metadata {"a2a:response": "true"}. This callback takes the A2UI JSON the
model emits as text and rewraps it into exactly that format.

Wire it up with `after_model_callback=a2ui_callback` on your Agent. Pin the A2UI
schema to **version 0.8** (this callback keys off v0.8 messages: beginRendering,
surfaceUpdate, dataModelUpdate). Copy this file next to your agent.py.

Robustness notes (why this file is more than a one-liner):
  * The model often *self-wraps* — it imitates the wrapped format it sees in its
    own history and emits `<a2a_datapart_json>{"kind":"data","data":{...}}</...>`
    or concatenated JSON objects. This callback strips those tags, splits
    concatenated objects, and unwraps `{"kind":"data","data":{...}}` envelopes.
  * On large / deeply-nested surfaces the model sometimes emits *invalid* JSON
    (e.g. a `]` where `}}` belonged). When that happens we can only partially
    parse the payload and would be left with a lone `beginRendering` and no body,
    which renders as a BLANK surface. To avoid that bad UX we require a surface to
    actually have renderable content; if it doesn't, we return a short plain-text
    fallback instead of a blank card.
"""

import json
import re

from google.genai import types
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse

# A2UI message kinds this renderer understands (v0.8).
_A2UI_KEYS = ("beginRendering", "surfaceUpdate", "dataModelUpdate", "deleteSurface")

# Tags the model may wrap around its output (its own render wrapper, or the SDK's).
_TAG_RE = re.compile(r"</?(?:a2a_datapart_json|a2ui-json)>")

# Shown when the model emitted A2UI we could not fully parse (usually malformed
# JSON on a large surface). Better than a blank card or a wall of raw JSON.
_FALLBACK_TEXT = (
    "I couldn't render that view. Could you ask again, maybe for a simpler summary?"
)

# adk web can only render an <Image> whose url is a real, fetchable http(s) link.
# Models frequently emit an Image pointing at a bare artifact filename or a
# relative path (e.g. "recipe_image_123.png"), which renders as a broken-image
# icon. We swap those for this short note; the generated media still shows up in
# the adk web Artifacts panel.
_HTTP_URL_RE = re.compile(r"^https?://", re.I)
_IMAGE_NOTE = "Image generated — open the Artifacts panel to view it."


def _wrap_a2ui_part(a2ui_message: dict) -> types.Part:
    """Wrap a single A2UI message for rendering in adk web."""
    datapart_json = json.dumps(
        {
            "kind": "data",
            "metadata": {"mimeType": "application/json+a2ui"},
            "data": a2ui_message,
        }
    )
    blob_data = (
        b"<a2a_datapart_json>" + datapart_json.encode("utf-8") + b"</a2a_datapart_json>"
    )
    return types.Part(
        inline_data=types.Blob(
            data=blob_data,
            mime_type="text/plain",
        )
    )


def _iter_json_values(text: str):
    """Yield top-level JSON values from a string of concatenated objects/arrays.

    Tolerates junk between values (tags, whitespace, commas). Stops at the first
    unparseable value, so a valid prefix is still returned (partial recovery).
    """
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] not in "{[":
            idx += 1
        if idx >= n:
            break
        try:
            value, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break  # malformed from here on; keep whatever we already parsed
        yield value
        idx = end


def _extract_a2ui_messages(text: str) -> list[dict]:
    """Pull A2UI messages out of the model's raw text output.

    Handles: markdown fences, self-wrap tags, plain arrays, concatenated objects,
    and `{"kind":"data","data":{...}}` envelopes. Returns a flat list of A2UI
    messages (each keyed by one of _A2UI_KEYS).
    """
    # Strip markdown fences.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    # Strip any wrapper tags so the JSON scanner sees clean payload.
    text = _TAG_RE.sub("", text).strip()

    values: list = []
    for value in _iter_json_values(text):
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)

    messages: list[dict] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        # Unwrap {"kind":"data","data":{...}} envelopes the model may imitate.
        inner = value.get("data")
        if isinstance(inner, dict) and any(k in inner for k in _A2UI_KEYS):
            messages.append(inner)
        elif any(k in value for k in _A2UI_KEYS):
            messages.append(value)
    return messages


def _heal_and_sanitize_components(messages: list[dict]) -> None:
    """Repair dangling child references, missing roots, and un-fetchable images in place."""
    all_defined: set = set()
    first_card: str | None = None
    first_id: str | None = None

    for m in messages:
        surface = m.get("surfaceUpdate")
        if not isinstance(surface, dict):
            continue
        comps = surface.get("components")
        if not isinstance(comps, list):
            continue

        # Sanitize images and collect IDs
        for c in comps:
            if not isinstance(c, dict):
                continue
            cid = c.get("id")
            if isinstance(cid, str):
                all_defined.add(cid)
                if first_id is None:
                    first_id = cid

            comp = c.get("component")
            if not isinstance(comp, dict):
                continue

            if "Card" in comp and isinstance(cid, str) and first_card is None:
                first_card = cid

            if "Image" in comp:
                img = comp.get("Image")
                url = img.get("url") if isinstance(img, dict) else None
                literal = url.get("literalString") if isinstance(url, dict) else None
                if not (isinstance(literal, str) and _HTTP_URL_RE.match(literal)):
                    c["component"] = {
                        "Text": {
                            "text": {"literalString": _IMAGE_NOTE},
                            "usageHint": "body",
                        }
                    }

    # Repair dangling child references in containers (Column, Row, Card)
    for m in messages:
        surface = m.get("surfaceUpdate")
        if not isinstance(surface, dict):
            continue
        comps = surface.get("components")
        if not isinstance(comps, list):
            continue

        for c in comps:
            if not isinstance(c, dict):
                continue
            comp = c.get("component")
            if not isinstance(comp, dict):
                continue
            for spec in comp.values():
                if not isinstance(spec, dict):
                    continue
                # If child reference is dangling, drop it
                if "child" in spec and spec["child"] not in all_defined:
                    spec.pop("child", None)
                # If explicitList contains dangling IDs, prune them
                children = spec.get("children")
                if isinstance(children, dict):
                    exp = children.get("explicitList")
                    if isinstance(exp, list):
                        children["explicitList"] = [cid for cid in exp if cid in all_defined]

    # Ensure beginRendering root points to a defined component
    fallback_root = first_card or first_id
    if fallback_root:
        for m in messages:
            br = m.get("beginRendering")
            if isinstance(br, dict):
                if not br.get("root") or br["root"] not in all_defined:
                    br["root"] = fallback_root


def _extract_text_fallback(text: str, messages: list[dict]) -> str:
    """Extract human-readable markdown text from components or raw text if surface fails."""
    lines: list[str] = []

    # Try extracting literal strings from parsed components
    for m in messages:
        su = m.get("surfaceUpdate")
        if isinstance(su, dict):
            for c in su.get("components") or []:
                if isinstance(c, dict):
                    comp = c.get("component") or {}
                    for v in comp.values():
                        if isinstance(v, dict):
                            t = v.get("text")
                            if isinstance(t, dict) and isinstance(t.get("literalString"), str):
                                s = t["literalString"].strip()
                                if s and s != _IMAGE_NOTE:
                                    usage = v.get("usageHint")
                                    if usage in ("h1", "h2"):
                                        lines.append(f"\n### {s}\n")
                                    else:
                                        lines.append(f"- {s}")

    if lines:
        return "\n".join(lines).strip()

    # Fallback to regex extraction of literalString from raw text
    matches = re.findall(r'"literalString"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text)
    if matches:
        regex_lines = []
        for m in matches:
            try:
                decoded = bytes(m, "utf-8").decode("unicode_escape", errors="ignore").strip()
            except Exception:
                decoded = m.strip()
            if decoded and decoded != _IMAGE_NOTE and len(decoded) > 1:
                regex_lines.append(f"- {decoded}")
        if regex_lines:
            return "\n".join(regex_lines)

    return "抱歉，剛才為您規劃的行程內容較為豐富未能完全排版。請嘗試稍候或提出更具體的問題，我將為您提供最清晰的規劃！"


def _component_ids_and_refs(components: list) -> tuple[set, set]:
    """Return (defined ids, referenced child ids) for a component list."""
    ids: set = set()
    refs: set = set()
    for c in components:
        if not isinstance(c, dict):
            continue
        if "id" in c:
            ids.add(c["id"])
        comp = c.get("component")
        if not isinstance(comp, dict):
            continue
        for spec in comp.values():  # e.g. {"Card": {...}} / {"Column": {...}}
            if not isinstance(spec, dict):
                continue
            if isinstance(spec.get("child"), str):
                refs.add(spec["child"])
            children = spec.get("children")
            if isinstance(children, dict):
                for cid in children.get("explicitList") or []:
                    if isinstance(cid, str):
                        refs.add(cid)
    return ids, refs


def _surface_is_renderable(messages: list[dict]) -> bool:
    """True only if the messages form a surface adk web can actually draw.

    Guards against the two blank-card failure modes flash models produce:
      * a lone `beginRendering` with no `surfaceUpdate` body (malformed JSON), and
      * a surface whose `root` (or a child ref) points at an id that was never
        defined — the whole tree then renders as nothing.
    dataModelUpdate / deleteSurface messages are always considered renderable.
    """
    all_ids: set = set()
    all_refs: set = set()
    roots: list = []
    has_body = False
    for m in messages:
        if "dataModelUpdate" in m or "deleteSurface" in m:
            return True
        br = m.get("beginRendering")
        if isinstance(br, dict) and isinstance(br.get("root"), str):
            roots.append(br["root"])
        su = m.get("surfaceUpdate")
        if isinstance(su, dict) and su.get("components"):
            has_body = True
            ids, refs = _component_ids_and_refs(su["components"])
            all_ids |= ids
            all_refs |= refs
    if not has_body:
        return False
    if any(root not in all_ids for root in roots):
        return False  # root points at an undefined component -> blank
    if all_refs - all_ids:
        return False  # dangling child references -> blank
    return True


def a2ui_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Convert A2UI JSON in text output to rendered components (or a clean fallback)."""
    if not llm_response.content or not llm_response.content.parts:
        return None

    for part in llm_response.content.parts:
        text = (part.text or "").strip()
        if not text:
            continue
        # Cheap gate: only touch parts that look like A2UI, leave prose alone.
        if not any(k in text for k in _A2UI_KEYS):
            continue

        messages = _extract_a2ui_messages(text)
        if not messages:
            # If JSON extract failed completely, try recovering text instead of giving an error
            recovered_text = _extract_text_fallback(text, [])
            return LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part(text=recovered_text)]
                )
            )

        # Repair missing IDs, dangling child refs, and sanitize images in place
        _heal_and_sanitize_components(messages)

        if not _surface_is_renderable(messages):
            # If surface still cannot be rendered, recover readable markdown text
            recovered_text = _extract_text_fallback(text, messages)
            return LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part(text=recovered_text)]
                )
            )

        new_parts = [_wrap_a2ui_part(m) for m in messages]
        return LlmResponse(
            content=types.Content(role="model", parts=new_parts),
            custom_metadata={"a2a:response": "true"},
        )

    return None

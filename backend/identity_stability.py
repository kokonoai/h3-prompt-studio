"""Compact, deterministic cast-identity guards for H3 prompts."""
from __future__ import annotations

import re


def _anchor(value, limit=260):
    """Keep the first useful visible-identity facts without bloating the prompt."""
    value = re.sub(r"\s+", " ", value.strip()) if isinstance(value, str) else ""
    if not value:
        return ""
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?。！？])\s*", value) if piece.strip()]
    chosen = ""
    for piece in pieces:
        candidate = (chosen + " " + piece).strip()
        if chosen and len(candidate) > limit:
            break
        chosen = candidate
        if len(chosen) >= limit:
            break
    chosen = chosen or value
    if len(chosen) > limit:
        chosen = chosen[:limit].rsplit(" ", 1)[0] or chosen[:limit]
        chosen = chosen.rstrip(" ,;:，；：") + "…"
    # The full canonical description already appears in the subject definition.
    # Lowercasing its opening letter keeps this nearby row visibly secondary and
    # avoids presenting the same authored sentence twice as two authorities.
    if chosen[:1].isalpha():
        chosen = chosen[:1].lower() + chosen[1:]
    return chosen


def visible_identity_lock(subjects, visible_ids, label_fn=None):
    """Render a local one-body/one-reference partition for an ensemble shot.

    Generic duplicate bans are easy for a video model to ignore when two cast
    members share a palette or silhouette. Repeating a compact identity anchor
    beside the actual shot keeps every body attached to exactly one named row.
    """
    by_id = {item.get("id"): item for item in subjects if isinstance(item, dict)}
    ordered = [ident for ident in dict.fromkeys(visible_ids) if ident in by_id]
    if len(ordered) < 2:
        return ""
    label_fn = label_fn or (lambda ident: str(by_id[ident].get("name", "character")))
    rows = []
    for ident in ordered:
        subject = by_id[ident]
        label = label_fn(ident)
        anchor = _anchor(subject.get("description", ""))
        row = ("- " + label + " = one body only"
               + (("; preserve only this identity anchor: " + anchor) if anchor else
                  "; preserve only its assigned reference and established design"))
        if row[-1] not in ".!?。！？…":
            row += "."
        rows.append(row)
    return "\n".join([
        "STRICT ONE-TO-ONE VISIBLE IDENTITY LOCK:",
        *rows,
        ("Every visible body must match exactly one row. Never transfer body shape, face, surface pattern, "
         "clothing or signature accessories between rows; never blend two rows into a hybrid. When identities "
         "share a colour, size, species or silhouette, preserve their different geometry and unique markers. "
         "If the frame is crowded, use stable spacing or partial occlusion—never an extra, mirrored or replacement body."),
    ])

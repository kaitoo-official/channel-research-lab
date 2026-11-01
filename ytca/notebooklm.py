"""Split a NotebookLM answer that a PERSON copied out of NotebookLM into per-video materials.

Expected layout (produced by the prompt in pilot/notebooklm_prompt.md):
    === VIDEO: https://www.youtube.com/watch?v=XXXXXXXXXXX ===
    ...summary for that video...
    === VIDEO: XXXXXXXXXXX ===
    ...
Text before the first marker, and any section for a video that is not registered, is reported and skipped.
Nothing here talks to NotebookLM; the person pastes the text into a local file.
"""
import re

from .materials import add_material
from .store import StoreError
from .urls import UrlError, normalize_video

MARKER = re.compile(r"^\s*=+\s*VIDEO\s*[:：]\s*(\S+)\s*=+\s*$", re.M)


def split_sections(text):
    parts = MARKER.split(text or "")
    out, problems = [], []
    if parts and parts[0].strip():
        problems.append({"section": "preamble", "reason": "text before the first VIDEO marker was ignored"})
    for i in range(1, len(parts), 2):
        ref, body = parts[i], parts[i + 1].strip()
        try:
            vid = normalize_video(ref)
        except UrlError as e:
            problems.append({"section": ref, "reason": str(e)})
            continue
        if not body:
            problems.append({"section": vid, "reason": "empty section"})
            continue
        out.append((vid, body))
    return out, problems


def import_split(store, key, text, human_reviewed, coverage="UNKNOWN"):
    if not human_reviewed:
        raise StoreError("HUMAN_REVIEW_REQUIRED", "confirm that a person checked the NotebookLM output")
    sections, problems = split_sections(text)
    known = store.videos(key)["videos"]
    added, duplicates = [], []
    for vid, body in sections:
        if vid not in known:
            problems.append({"section": vid, "reason": "video not registered (add the URL first)"})
            continue
        m, new = add_material(store, key, vid, "notebooklm_summary", text=body, human_reviewed=True,
                              coverage=coverage, note="split from a NotebookLM answer")
        (added if new else duplicates).append({"video_id": vid, "material_id": m["material_id"]})
    return {"added": added, "duplicates": duplicates, "problems": problems}

"""Deterministic grounding check for the gap subagent's extractor (Stage 4).

No LLM. The LLM no longer transcribes a full quote (its plain-text rendering
of in-paper LaTeX, e.g. "p_g(x)" for source "$p_g(\\bm{x})$", almost never
matches byte-for-byte). Instead it gives a short *anchor* — a few distinctive
words it expects to occur exactly once in the section. `find_anchor` locates
that anchor deterministically and expands it to its containing sentence; the
*stored* quote is always a literal substring of paper.json, so grounding is
structural rather than dependent on LLM transcription.
"""
from __future__ import annotations

import re

import pysbd

# Matches `\command{inner}` so we can drop the wrapper and keep `inner` —
# e.g. \bm{x} -> x, \mathbf{q} -> q. One level of nesting only; good enough
# for the simple style/formatting macros that show up around inline math.
_LATEX_WRAPPER_RE = re.compile(r"\\[a-zA-Z]+\{([^{}]*)\}")
# Bare commands with no braces, e.g. \alpha, \cdot — dropped entirely.
_LATEX_BARE_CMD_RE = re.compile(r"\\[a-zA-Z]+")

_SEGMENTER = pysbd.Segmenter(language="en", clean=False)


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace and strip simple LaTeX wrappers/math delimiters,
    while recording which original index produced each output character.

    `index_map[i]` is the offset in `text` of the character that produced
    `normalized[i]`, so a match span in the normalized string can be mapped
    back to a real, original-text offset.
    """
    out: list[str] = []
    index_map: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "$":
            i += 1
            continue
        if ch == "\\":
            m = _LATEX_WRAPPER_RE.match(text, i)
            if m:
                inner_start, inner_end = m.span(1)
                for j in range(inner_start, inner_end):
                    out.append(text[j])
                    index_map.append(j)
                i = m.end()
                continue
            m2 = _LATEX_BARE_CMD_RE.match(text, i)
            if m2:
                i = m2.end()
                continue
        if ch.isspace():
            if out and out[-1] == " ":
                i += 1
                continue
            out.append(" ")
            index_map.append(i)
            i += 1
            continue
        out.append(ch)
        index_map.append(i)
        i += 1
    return "".join(out), index_map


def _find_all_spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Non-overlapping occurrences of `needle` in `haystack`."""
    if not needle:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx < 0:
            break
        spans.append((idx, idx + len(needle)))
        start = idx + len(needle)
    return spans


def _sentence_spans(section_text: str) -> list[tuple[int, int]]:
    """[start, end) offsets of each sentence in `section_text`, in order.

    Uses pysbd (rule-based; handles "et al.", "Fig. 3", decimals like
    "3.17", and other abbreviations that break a naive split on '.') to get
    sentence strings, then locates each one in the original text so the
    spans are real offsets rather than pysbd's (re-joined) copies.
    """
    sentences = _SEGMENTER.segment(section_text)
    spans: list[tuple[int, int]] = []
    cursor = 0
    for raw in sentences:
        sent = raw.strip()
        if not sent:
            continue
        idx = section_text.find(sent, cursor)
        if idx < 0:
            # Tolerant fallback: pysbd may have normalized internal
            # whitespace. Search from the same cursor, whitespace-collapsed.
            norm_doc, doc_map = _normalize_with_map(section_text[cursor:])
            norm_sent = " ".join(sent.split())
            rel = norm_doc.find(norm_sent)
            if rel < 0:
                continue
            idx = cursor + doc_map[rel]
            end_rel = rel + len(norm_sent) - 1
            end = cursor + doc_map[end_rel] + 1
        else:
            end = idx + len(sent)
        spans.append((idx, end))
        cursor = end
    return spans


def _expand_to_sentences(
    spans: list[tuple[int, int]], match_start: int, match_end: int
) -> tuple[int, int]:
    """Union of every sentence span overlapping [match_start, match_end)."""
    overlapping = [
        (s, e) for s, e in spans if s < match_end and e > match_start
    ]
    if not overlapping:
        return match_start, match_end
    start = min(s for s, _ in overlapping)
    end = max(e for _, e in overlapping)
    return min(start, match_start), max(end, match_end)


def find_anchor(section_text: str, anchor: str) -> dict:
    """Locate `anchor` in `section_text` and expand it to its sentence(s).

    Returns one of:
      {"status": "not_found"}
      {"status": "ambiguous", "offsets": [[s, e], ...]}
      {"status": "matched", "char_offset": [s, e], "quote": str}

    "matched" requires the anchor to occur exactly once (after whitespace/
    LaTeX-tolerant normalization) — multiple occurrences are reported as
    "ambiguous" rather than silently resolved to the first hit, since
    guessing would corrupt the evidence trail.
    """
    anchor = (anchor or "").strip()
    if not anchor:
        return {"status": "not_found"}

    norm_hay, index_map = _normalize_with_map(section_text)
    norm_needle, _ = _normalize_with_map(anchor)
    if not norm_needle:
        return {"status": "not_found"}

    norm_spans = _find_all_spans(norm_hay, norm_needle)
    if not norm_spans:
        return {"status": "not_found"}

    orig_spans = []
    for nstart, nend in norm_spans:
        ostart = index_map[nstart]
        oend = index_map[nend - 1] + 1
        orig_spans.append((ostart, oend))

    if len(orig_spans) > 1:
        return {"status": "ambiguous", "offsets": [list(s) for s in orig_spans]}

    match_start, match_end = orig_spans[0]
    sentence_spans = _sentence_spans(section_text)
    qstart, qend = _expand_to_sentences(sentence_spans, match_start, match_end)
    return {
        "status": "matched",
        "char_offset": [qstart, qend],
        "quote": section_text[qstart:qend],
    }

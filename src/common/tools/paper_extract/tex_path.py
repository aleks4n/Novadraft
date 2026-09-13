"""Tex extraction path.

Regex-based passes, no full LaTeX parser: math, tables and algorithm environments
are preserved verbatim; the preamble and pure-formatting commands are stripped;
``\\input`` is resolved recursively; citations/refs are rewritten to stable tokens.
"""
from __future__ import annotations

import re
from pathlib import Path

from .canonical import canonicalize

# --- comments / preamble ------------------------------------------------------

_COMMENT_RE = re.compile(r"(?<!\\)%.*?$", re.MULTILINE)


def _strip_comments(text: str) -> str:
    return _COMMENT_RE.sub("", text)


# --- \input / \include recursion ---------------------------------------------

_INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")


def _resolve_inputs(text: str, base_dir: Path, depth: int = 0) -> str:
    if depth > 6:
        return text

    def _sub(m: re.Match) -> str:
        name = m.group(1).strip()
        candidates = [base_dir / name, base_dir / f"{name}.tex"]
        for c in candidates:
            if c.exists() and c.is_file():
                inner = _strip_comments(c.read_text(errors="replace"))
                return _resolve_inputs(inner, c.parent, depth + 1)
        return ""

    return _INPUT_RE.sub(_sub, text)


# --- body extraction ----------------------------------------------------------

_BODY_RE = re.compile(r"\\begin\{document\}(.*?)\\end\{document\}", re.DOTALL)


def _extract_body(text: str) -> str:
    m = _BODY_RE.search(text)
    return m.group(1) if m else text


# --- formatting cleanup -------------------------------------------------------

_DROP_NOARG = [
    "maketitle", "tableofcontents", "newpage", "clearpage", "pagebreak",
    "linebreak", "noindent", "centering", "small", "footnotesize", "large",
    "Large", "huge", "normalsize", "bf", "it", "rm", "tt", "em",
]
_DROP_ENVS = ["figure", "figure*", "wrapfigure", "subfigure"]

# \emph{x}, \textbf{x}, etc. -> x
_UNWRAP_RE = re.compile(
    r"\\(?:emph|textbf|textit|textsc|textsf|texttt|textrm|underline|uline)\s*\{([^{}]*)\}"
)
# \footnote{x} -> (footnote: x)
_FOOTNOTE_RE = re.compile(r"\\footnote\s*\{([^{}]*)\}")
# \cite[\citep,\citet]{a,b} -> [CITE:a] [CITE:b]
_CITE_RE = re.compile(r"\\(?:cite[a-zA-Z]*)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
# \ref / \eqref / \autoref -> [REF:label]
_REF_RE = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref)\s*\{([^}]+)\}")
# \label{x} -> drop
_LABEL_RE = re.compile(r"\\label\s*\{[^}]*\}")
# generic no-arg control sequences from DROP_NOARG
_DROP_NOARG_RE = re.compile(r"\\(?:" + "|".join(_DROP_NOARG) + r")\b")


def _drop_envs(text: str, names: list[str]) -> tuple[str, list[str]]:
    captured = []
    for n in names:
        pattern = re.compile(
            r"\\begin\{" + re.escape(n) + r"\}(.*?)\\end\{" + re.escape(n) + r"\}",
            re.DOTALL,
        )
        # Capture caption only, drop the rest
        def _sub(m: re.Match) -> str:
            inner = m.group(1)
            cap = re.search(r"\\caption\s*\{((?:[^{}]|\{[^{}]*\})*)\}", inner)
            if cap:
                return f"\n[FIGURE: {cap.group(1).strip()}]\n"
            return ""
        text = pattern.sub(_sub, text)
    return text, captured


def _resolve_citations(text: str) -> str:
    def _sub(m: re.Match) -> str:
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        return " ".join(f"[CITE:{k}]" for k in keys)
    return _CITE_RE.sub(_sub, text)


def _cleanup(text: str) -> str:
    text, _ = _drop_envs(text, _DROP_ENVS)
    text = _LABEL_RE.sub("", text)
    text = _resolve_citations(text)
    text = _REF_RE.sub(lambda m: f"[REF:{m.group(1).strip()}]", text)
    text = _FOOTNOTE_RE.sub(lambda m: f" (footnote: {m.group(1).strip()})", text)
    # Repeatedly unwrap nested formatting like \textbf{\emph{x}}
    for _ in range(4):
        new = _UNWRAP_RE.sub(lambda m: m.group(1), text)
        if new == text:
            break
        text = new
    text = _DROP_NOARG_RE.sub("", text)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- table capture ------------------------------------------------------------

_TABLE_ENV_RE = re.compile(
    r"\\begin\{(table\*?|tabular\*?)\}(.*?)\\end\{\1\}", re.DOTALL
)


def _extract_tables(text: str) -> tuple[str, list[dict]]:
    """Replace table environments with [TABLE:tab_N] tokens and capture them."""
    tables: list[dict] = []

    def _sub(m: re.Match) -> str:
        idx = len(tables) + 1
        block = m.group(0)
        cap = re.search(r"\\caption\s*\{((?:[^{}]|\{[^{}]*\})*)\}", block)
        lbl = re.search(r"\\label\s*\{([^}]+)\}", block)
        tab_id = lbl.group(1) if lbl else f"tab_{idx}"
        tables.append({
            "id": tab_id,
            "caption": (cap.group(1).strip() if cap else None),
            "latex": block,
        })
        return f"\n[TABLE:{tab_id}]\n"

    new_text = _TABLE_ENV_RE.sub(_sub, text)
    return new_text, tables


# --- section splitting --------------------------------------------------------

_SECTION_RE = re.compile(r"\\section\*?\s*\{((?:[^{}]|\{[^{}]*\})*)\}")


def _split_sections(text: str, abstract: str | None) -> dict[str, str]:
    sections: dict[str, str] = {}
    if abstract:
        sections["abstract"] = abstract.strip()

    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        sections["body"] = text.strip()
        return sections

    # Anything before the first \section is treated as preface (ignored unless useful)
    for i, m in enumerate(matches):
        heading = re.sub(r"\\[a-zA-Z]+\s*\{?[^}]*\}?", "", m.group(1)).strip()
        key = canonicalize(heading)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # Preserve subsections inline as \subsection{...} markers (already in body)
        if key in sections:
            sections[key] += "\n\n" + body
        else:
            sections[key] = body
    return sections


# --- abstract & title ---------------------------------------------------------

_ABSTRACT_RE = re.compile(
    r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL
)


def _extract_abstract(text: str) -> tuple[str | None, str]:
    m = _ABSTRACT_RE.search(text)
    if not m:
        return None, text
    return m.group(1).strip(), text[:m.start()] + text[m.end():]


# --- bibliography (inline thebibliography or external .bbl) -------------------

_BIB_BLOCK_RE = re.compile(
    r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", re.DOTALL
)
_BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\})", re.DOTALL)


def _extract_bibliography(text: str, base_dir: Path) -> tuple[dict, str]:
    """Pull \\begin{thebibliography} blocks; also try to load a sibling .bbl."""
    bib: dict[str, dict] = {}

    # Inline
    for block in _BIB_BLOCK_RE.finditer(text):
        for m in _BIBITEM_RE.finditer(block.group(0)):
            key = m.group(1).strip()
            raw = re.sub(r"\s+", " ", m.group(2)).strip()
            bib[key] = {"raw": raw}
    text_wo_bib = _BIB_BLOCK_RE.sub("", text)

    # External .bbl files in the tex tree
    for bbl in base_dir.glob("*.bbl"):
        content = bbl.read_text(errors="replace")
        for m in _BIBITEM_RE.finditer(content + r"\end{thebibliography}"):
            key = m.group(1).strip()
            if key in bib:
                continue
            bib[key] = {"raw": re.sub(r"\s+", " ", m.group(2)).strip()}

    return bib, text_wo_bib


# --- entry point --------------------------------------------------------------

def extract_tex(main_tex: Path, tex_dir: Path) -> dict:
    raw = main_tex.read_text(errors="replace")
    raw = _strip_comments(raw)
    resolved = _resolve_inputs(raw, main_tex.parent)
    body = _extract_body(resolved)

    abstract, body = _extract_abstract(body)
    bibliography, body = _extract_bibliography(body, tex_dir)
    body, tables = _extract_tables(body)
    body = _cleanup(body)

    sections = _split_sections(body, abstract)
    full_text = "\n\n".join(f"# {k}\n\n{v}" for k, v in sections.items())

    unmapped = []
    for m in _SECTION_RE.finditer(body):
        heading = re.sub(r"\\[a-zA-Z]+\s*\{?[^}]*\}?", "", m.group(1)).strip().lower()
        key = canonicalize(heading)
        if key not in {
            "abstract", "introduction", "background", "related_work", "method",
            "experiments", "results", "discussion", "limitations", "conclusion",
            "future_work", "references", "acknowledgments",
        }:
            unmapped.append(heading)

    return {
        "sections": sections,
        "full_text": full_text,
        "tables": tables,
        "bibliography": bibliography,
        "unmapped_headings": sorted(set(unmapped)),
        "unresolved_macros": [],
        "warnings": [],
    }

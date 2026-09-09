"""Parse policy markdown into retrievable chunks.

Chunking strategy: one chunk per document section. Sections are authored by hand
with an explicit slug, so a chunk id such as `return-policy#refund-timing` is stable
across re-seeds. That stability is what makes citations meaningful; a chunker
that split on token count would renumber chunks whenever the text was edited and
every citation in the eval dataset would rot.

File format::

    ---
    doc_id: return-policy
    policy_type: return
    title_vi: ...
    title_en: ...
    source_url: ...
    updated_at: 2026-02-01
    ---

    ## [slug] Vietnamese heading | English heading

    @vi
    Vietnamese body text.

    @en
    English body text.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from app.db.schema import PolicyChunk

POLICY_DIR = Path(__file__).parent / "policies"

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_SECTION = re.compile(r"^##\s+\[([a-z0-9\-]+)\]\s*(.*)$", re.MULTILINE)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER.match(text)
    if not match:
        raise ValueError("policy file is missing a frontmatter block")

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        # split once only: source_url values contain a colon.
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()

    return meta, text[match.end() :]


def _split_languages(body: str) -> tuple[str, str]:
    """Pull the @vi and @en blocks out of one section body."""
    vi_marker = body.find("@vi")
    en_marker = body.find("@en")
    if vi_marker == -1 or en_marker == -1:
        raise ValueError("section is missing an @vi or @en block")
    if en_marker < vi_marker:
        raise ValueError("@en block must follow the @vi block")

    vietnamese = body[vi_marker + len("@vi") : en_marker].strip()
    english = body[en_marker + len("@en") :].strip()
    return vietnamese, english


def parse_file(path: Path) -> list[PolicyChunk]:
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))

    required = {"doc_id", "policy_type", "title_vi", "title_en", "source_url", "updated_at"}
    missing = required - meta.keys()
    if missing:
        raise ValueError(f"{path.name} frontmatter is missing: {sorted(missing)}")

    updated_at = datetime.strptime(meta["updated_at"], "%Y-%m-%d").replace(tzinfo=timezone.utc)

    matches = list(_SECTION.finditer(body))
    if not matches:
        raise ValueError(f"{path.name} contains no '## [slug] ...' sections")

    chunks: list[PolicyChunk] = []
    for index, match in enumerate(matches):
        slug = match.group(1)
        heading = match.group(2)

        # Heading is "Vietnamese title | English title".
        if "|" in heading:
            section_vi, section_en = (part.strip() for part in heading.split("|", 1))
        else:
            section_vi = section_en = heading.strip()

        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        text_vi, text_en = _split_languages(body[match.end() : end])

        # Both languages plus both headings go into one embedded field, so a
        # Vietnamese question and an English question retrieve the same chunk.
        embedding_source = "\n\n".join(
            [meta["title_vi"], meta["title_en"], section_vi, section_en, text_vi, text_en]
        )

        chunks.append(
            PolicyChunk(
                chunk_id=f"{meta['doc_id']}#{slug}",
                doc_id=meta["doc_id"],
                policy_type=meta["policy_type"],  # type: ignore[arg-type]
                title_vi=meta["title_vi"],
                title_en=meta["title_en"],
                section_vi=section_vi,
                section_en=section_en,
                chunk_index=index,
                text_vi=text_vi,
                text_en=text_en,
                embedding_source=embedding_source,
                source_url=f"{meta['source_url']}#{slug}",
                updated_at=updated_at,
            )
        )

    return chunks


def parse_all(directory: Path | None = None) -> list[PolicyChunk]:
    directory = directory or POLICY_DIR
    chunks: list[PolicyChunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(parse_file(path))
    return chunks


if __name__ == "__main__":
    parsed = parse_all()
    print(f"Parsed {len(parsed)} chunks from {len(list(POLICY_DIR.glob('*.md')))} documents\n")
    for chunk in parsed:
        print(f"  {chunk.chunk_id:<42} {len(chunk.text_vi):>5} vi  {len(chunk.text_en):>5} en")

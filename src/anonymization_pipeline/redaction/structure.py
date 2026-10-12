"""Layout structure: a DoclingDocument of the pages, censored with the same tags as the Markdown.

The structure is rebuilt from a whitelist of fields of the docling conversion, never copied: text,
tables, pictures, positions and the tree, without images, annotations, metadata, hyperlinks, file
name and the content layers the Markdown does not hold (text inside pictures, hidden text). Pages
the VLM transcribed have no docling items: their Markdown becomes one block of flowing text.

The censored structure travels with the result (JSON, also attached to the censored PDF); the
encrypted map holds, for every replacement, the element, the offsets and the original value, so
the original structure can be rebuilt with the key.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from docling_core.types.doc import (
    BoundingBox,
    CodeItem,
    ContentLayer,
    CoordOrigin,
    DocItem,
    DocItemLabel,
    DoclingDocument,
    FormulaItem,
    GroupItem,
    ListItem,
    NodeItem,
    PictureItem,
    ProvenanceItem,
    SectionHeaderItem,
    Size,
    TableCell,
    TableData,
    TableItem,
    TextItem,
    TitleItem,
)
from pydantic import ValidationError

from anonymization_pipeline.domain import Document, Group, Occurrence
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.redaction import leftovers, retag

NAME = "documento"
ATTACHMENT = (
    "documento.anon.docling.json"  # name of the structure attached to the censored PDF
)
A4 = (595.0, 842.0)
FLOW_MARGIN = 36.0  # points around the flowing text of a page without docling items
_TEXT_LABELS = {
    DocItemLabel.CAPTION,
    DocItemLabel.CHECKBOX_SELECTED,
    DocItemLabel.CHECKBOX_UNSELECTED,
    DocItemLabel.FOOTNOTE,
    DocItemLabel.PAGE_FOOTER,
    DocItemLabel.PAGE_HEADER,
    DocItemLabel.PARAGRAPH,
    DocItemLabel.REFERENCE,
    DocItemLabel.TEXT,
    DocItemLabel.HANDWRITTEN_TEXT,
}
# JSON keys whose strings are structure (references, enums, versions), never document text
_STRUCTURAL_KEYS = {
    "schema_name",
    "version",
    "name",
    "self_ref",
    "cref",
    "$ref",
    "orientation",
    "label",
    "content_layer",
    "coord_origin",
    "code_language",
    "script",
}
_WORD = re.compile(r"\w+")
_MISMATCH = "La mappa non corrisponde alla struttura"

# a text field of the structure: element reference and, for tables, the index of the cell
Field = tuple[str, int | None]


class StructureError(ValueError):
    """Invalid structure or map, or a structure that does not match its map (Italian message)."""


class StructureLeak(Exception):
    """The censored structure holds text the censored Markdown does not (no values in the message)."""


@dataclass
class Censored:
    """A censored structure.

    Attributes:
        document: The structure with the tags.
        elements: Map entries: element, cell, offsets in the censored text, tag, original value.
        marks: Positions of the tags in every censored field (for the renderer).
        texts: Every censored field, in document order.
        occurrences: Every replacement (with the category), in document order.
    """

    document: DoclingDocument
    elements: list[dict[str, Any]] = field(default_factory=list)
    marks: dict[Field, list[tuple[int, int]]] = field(default_factory=dict)
    texts: list[str] = field(default_factory=list)
    occurrences: list[Occurrence] = field(default_factory=list)


def page_size(doc: Document, index: int, cfg: PipelineConfig) -> tuple[float, float]:
    """Size in points of a page: from docling, else from the rendering, else A4."""
    if doc.docling is not None and (index + 1) in doc.docling.pages:
        size = doc.docling.pages[index + 1].size
        return size.width, size.height
    image = doc.pages[index].image
    if image is not None:
        return (
            image.width * 72 / cfg.images.pdf_dpi,
            image.height * 72 / cfg.images.pdf_dpi,
        )
    return A4


def _prov(item: DocItem, pages: set[int], length: int) -> list[ProvenanceItem]:
    return [
        ProvenanceItem(page_no=p.page_no, bbox=p.bbox, charspan=(0, length))
        for p in item.prov
        if p.page_no in pages
    ]


def _add_text(
    dst: DoclingDocument,
    item: TextItem,
    parent: NodeItem | None,
    prov: list[ProvenanceItem],
) -> TextItem:
    common: dict[str, Any] = {
        "prov": prov[0],
        "parent": parent,
        "content_layer": item.content_layer,
        "formatting": item.formatting,
    }
    if isinstance(item, TitleItem):
        new: TextItem = dst.add_title(item.text, **common)
    elif isinstance(item, SectionHeaderItem):
        new = dst.add_heading(item.text, level=item.level, **common)
    elif isinstance(item, ListItem):
        new = dst.add_list_item(
            item.text, enumerated=item.enumerated, marker=item.marker, **common
        )
    elif isinstance(item, CodeItem):
        new = dst.add_code(item.text, **common)
    elif isinstance(item, FormulaItem):
        new = dst.add_formula(item.text, **common)
    else:
        label = item.label if item.label in _TEXT_LABELS else DocItemLabel.TEXT
        new = dst.add_text(label, item.text, **common)
    new.prov = prov
    return new


def _table(item: TableItem) -> TableData:
    cells = [
        TableCell(
            bbox=c.bbox,
            row_span=c.row_span,
            col_span=c.col_span,
            start_row_offset_idx=c.start_row_offset_idx,
            end_row_offset_idx=c.end_row_offset_idx,
            start_col_offset_idx=c.start_col_offset_idx,
            end_col_offset_idx=c.end_col_offset_idx,
            text=c.text,
            column_header=c.column_header,
            row_header=c.row_header,
            row_section=c.row_section,
        )
        for c in item.data.table_cells
    ]
    return TableData(
        table_cells=cells, num_rows=item.data.num_rows, num_cols=item.data.num_cols
    )


@dataclass
class _Copier:
    """Copies the items of ``src`` on ``pages`` and in ``layers`` into ``dst``, each once."""

    src: DoclingDocument
    dst: DoclingDocument
    pages: set[int]
    layers: set[ContentLayer]
    seen: set[str] = field(default_factory=set)

    def captions(self, item: TableItem | PictureItem, parent: NodeItem | None) -> None:
        for ref in item.captions:
            caption = ref.resolve(self.src)
            if isinstance(caption, TextItem) and caption.self_ref not in self.seen:
                self.seen.add(caption.self_ref)
                if prov := _prov(caption, self.pages, len(caption.text)):
                    new = self.dst.add_text(
                        DocItemLabel.CAPTION,
                        caption.text,
                        prov=prov[0],
                        parent=parent,
                        content_layer=caption.content_layer,
                    )
                    new.prov = prov

    def copy(self, node: NodeItem, parent: NodeItem | None) -> None:
        for ref in node.children:
            item = ref.resolve(self.src)
            if item.content_layer not in self.layers or item.self_ref in self.seen:
                continue
            self.seen.add(item.self_ref)
            if isinstance(item, GroupItem):
                group = self.dst.add_group(
                    label=item.label,
                    name=item.label.value,
                    parent=parent,
                    content_layer=item.content_layer,
                )
                self.copy(item, group)
            elif isinstance(item, TextItem):
                if prov := _prov(item, self.pages, len(item.text)):
                    self.copy(item, _add_text(self.dst, item, parent, prov))
            elif isinstance(item, TableItem):
                if prov := _prov(item, self.pages, 0):
                    table = self.dst.add_table(
                        _table(item),
                        prov=prov[0],
                        parent=parent,
                        content_layer=item.content_layer,
                    )
                    table.prov = prov
                    self.captions(item, parent)
            elif isinstance(item, PictureItem) and (prov := _prov(item, self.pages, 0)):
                # never the image, its annotations or the text inside it
                picture = self.dst.add_picture(
                    prov=prov[0], parent=parent, content_layer=item.content_layer
                )
                picture.prov = prov
                self.captions(item, parent)


def build_structure(doc: Document, cfg: PipelineConfig) -> DoclingDocument:
    """The structure of the document IN CLEAR TEXT (it is censored by ``censor``)."""
    out = DoclingDocument(name=NAME)
    for page in doc.pages:
        width, height = page_size(doc, page.index, cfg)
        out.add_page(page.index + 1, Size(width=width, height=height))
    from_docling = {p.index + 1 for p in doc.pages if p.extracted_with == "docling"}
    if doc.docling is not None and from_docling:
        layers = {ContentLayer.BODY}
        if cfg.phases.extraction.native_pdf.docling.furniture:
            layers.add(ContentLayer.FURNITURE)
        _Copier(doc.docling, out, from_docling, layers).copy(doc.docling.body, None)
    for page in doc.pages:
        if page.index + 1 in from_docling or not page.text:
            continue
        size = out.pages[page.index + 1].size
        out.add_text(
            DocItemLabel.TEXT,
            page.text,
            prov=ProvenanceItem(
                page_no=page.index + 1,
                bbox=BoundingBox(
                    l=FLOW_MARGIN,
                    t=size.height - FLOW_MARGIN,
                    r=size.width - FLOW_MARGIN,
                    b=FLOW_MARGIN,
                    coord_origin=CoordOrigin.BOTTOMLEFT,
                ),
                charspan=(0, len(page.text)),
            ),
        )
    return out


def fields(structure: DoclingDocument) -> Iterator[tuple[Field, str]]:
    """Every text field: ``((ref, None), text)`` for texts, ``((ref, cell index), text)`` for cells."""
    for item in structure.texts:
        yield (item.self_ref, None), item.text
    for table in structure.tables:
        for i, cell in enumerate(table.data.table_cells):
            yield (table.self_ref, i), cell.text


def _set(structure: DoclingDocument, where: Field, text: str) -> None:
    ref, cell = where
    item = _resolve(structure, ref)
    if cell is None:
        if not isinstance(item, TextItem):
            raise StructureError(_MISMATCH)
        item.text = item.orig = text
        for prov in item.prov:
            prov.charspan = (0, len(text))
    else:
        if not isinstance(item, TableItem) or not 0 <= cell < len(
            item.data.table_cells
        ):
            raise StructureError(_MISMATCH)
        item.data.table_cells[cell].text = text


def _resolve(structure: DoclingDocument, ref: str) -> NodeItem:
    match = re.fullmatch(r"#/(texts|tables)/(\d+)", ref)
    items: list = (
        getattr(structure, match.group(1)) if match else []
    )  # only the fields censor writes
    if not match or int(match.group(2)) >= len(items):
        raise StructureError(_MISMATCH)
    return items[int(match.group(2))]


def censor(
    structure: DoclingDocument, groups: list[Group], cfg: PipelineConfig
) -> Censored:
    """Replace the values with the tags the Markdown got (``redact`` assigned ``group.tag``)."""
    out = Censored(structure.model_copy(deep=True))
    where = [w for w, _ in fields(structure)]
    texts, occurrences = retag([t for _, t in fields(structure)], groups, cfg)
    for (ref, cell), text, found in zip(where, texts, occurrences, strict=True):
        _set(out.document, (ref, cell), text)
        out.texts.append(text)
        out.occurrences += found
        if found:
            out.marks[(ref, cell)] = [(o.start, o.end) for o in found]
        out.elements += [
            {
                "ref": ref,
                "cella": cell,
                "inizio": o.start,
                "fine": o.end,
                "sostituto": o.replacement,
                "originale": o.original,
            }
            for o in found
        ]
    return out


def canonical(structure: DoclingDocument) -> bytes:
    """Stable JSON of a structure (the fingerprint in the map is computed on it)."""
    return json.dumps(
        structure.export_to_dict(),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(structure: DoclingDocument) -> str:
    return hashlib.sha256(canonical(structure)).hexdigest()


def _strings(data: Any, key: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(data, dict):
        for k, v in data.items():
            yield from _strings(v, str(k))
    elif isinstance(data, list):
        for v in data:
            yield from _strings(v, key)
    elif isinstance(data, str):
        yield key, data


def check(
    censored: Censored,
    markdown: str,
    occurrences: list[Occurrence],
    cfg: PipelineConfig,
) -> None:
    """Fail closed if the censored structure could hold data the censored Markdown does not.

    Every word of every string of the JSON (except references and enums) must be in the censored
    Markdown, and no replaced value may be left in clear text in any field.

    Raises:
        StructureLeak: The structure cannot be returned.
    """
    allowed = set(_WORD.findall(markdown))
    for key, value in _strings(censored.document.export_to_dict()):
        if key not in _STRUCTURAL_KEYS and not set(_WORD.findall(value)) <= allowed:
            raise StructureLeak("text not in the censored Markdown")
    values = {(o.original, o.category) for o in [*occurrences, *censored.occurrences]}
    if leftovers(censored.texts, values, cfg):
        raise StructureLeak("replaced value in clear text")


def map_section(original: DoclingDocument, censored: Censored) -> dict[str, Any]:
    """Keys added to the map content to rebuild the original structure."""
    return {
        "sha256_struttura": fingerprint(censored.document),
        "sha256_struttura_originale": fingerprint(original),
        "elementi": censored.elements,
    }


def load(data: bytes) -> DoclingDocument:
    """A structure from its JSON.

    Raises:
        StructureError: Not a valid structure.
    """
    try:
        return DoclingDocument.model_validate_json(data)
    except ValidationError as exc:
        raise StructureError("Struttura del documento non valida") from exc


def _entries(elements: list[Any]) -> dict[Field, list[tuple[int, int, str, str]]]:
    """Map entries by field: start, end, tag, original value."""
    out: dict[Field, list[tuple[int, int, str, str]]] = {}
    try:
        for e in elements:
            cell = None if e["cella"] is None else int(e["cella"])
            out.setdefault((str(e["ref"]), cell), []).append(
                (
                    int(e["inizio"]),
                    int(e["fine"]),
                    str(e["sostituto"]),
                    str(e["originale"]),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise StructureError(_MISMATCH) from exc
    return out


def restore(censored: DoclingDocument, content: dict[str, Any]) -> DoclingDocument:
    """The original structure from the censored one and the decrypted map content.

    Raises:
        StructureError: The map has no structure, or does not match this structure.
    """
    elements = content.get("elementi")
    if not isinstance(elements, list) or "sha256_struttura" not in content:
        raise StructureError(
            "La mappa non contiene la struttura del documento (prodotta senza PDF)"
        )
    if fingerprint(censored) != content["sha256_struttura"]:
        raise StructureError("La struttura del documento è stata modificata")
    out = censored.model_copy(deep=True)
    current = dict(fields(out))
    for where, entries in _entries(elements).items():
        if where not in current:
            raise StructureError(_MISMATCH)
        text = current[where]
        for start, end, tag, original in sorted(entries, reverse=True):
            if text[start:end] != tag:
                raise StructureError(_MISMATCH)
            text = text[:start] + original + text[end:]
        _set(out, where, text)
    if fingerprint(out) != content.get("sha256_struttura_originale"):
        raise StructureError(_MISMATCH)
    return out

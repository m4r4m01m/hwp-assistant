"""Local source explanations for new reference plans; canonical drafts stay intact."""
import re

POLICY = "reference-local-notes-v1"


def localize_notes(blocks, sources, settings):
    from .report_contract import citation_marker, citation_style_for_scope, presentation_text

    known = {source.id: source for source in sources}
    used, result = set(), []
    cursor, serial = 0, 0
    while cursor < len(blocks):
        text, kind, forced, meta = blocks[cursor]
        group = [blocks[cursor]]
        cursor += 1
        # A caption and all native table/diagram cells share one numbering scope.
        # Every prose block is still a whole logical paragraph at this point.
        visual_kind = meta.get("note_visual_kind") if meta.get("note_visual_caption") else None
        if visual_kind and cursor < len(blocks) and blocks[cursor][3].get("note_visual_group"):
            visual_id = blocks[cursor][3]["note_visual_group"]
            while cursor < len(blocks) and blocks[cursor][3].get("note_visual_group") == visual_id:
                group.append(blocks[cursor]); cursor += 1
        identifiers = list(dict.fromkeys(identifier for value, role, _, _ in group if role not in ("title", "heading")
                                        for identifier in re.findall(r"\[(S\d+)\]", value) if identifier in known))
        if not identifiers:
            result.extend(group)
            continue
        serial += 1
        scope = f"note-{serial}"
        numbers = {identifier: i for i, identifier in enumerate(identifiers, 1)}
        style = citation_style_for_scope(settings.get("citation_style", "STARS"), len(identifiers))
        used.update(identifiers)
        for value, role, page_break, metadata in group:
            marks = []
            rendered = presentation_text(value, settings, known, marks=marks, marker_numbers=numbers)
            metadata = {**metadata, "note_scope": scope, "citation_source_ids": identifiers}
            if marks and settings.get("citation_superscript"):
                metadata["citation_ranges"] = marks
            result.append((rendered, role, page_break, metadata))
        parent = meta.get("style_kind", kind)
        note_role = "table_source" if visual_kind else "note"
        for identifier in identifiers:
            source = known[identifier]
            marker = citation_marker(f"S{numbers[identifier]}", style)
            # This optional policy applies only to explanation labels. Inline
            # ranges continue to use full 주1), 주2) markers at their source token.
            if settings.get("citation_note_abbreviated") and style == "NOTE" and numbers[identifier] > 1:
                marker = f"{numbers[identifier]})"
            value = f"{marker} {source.title} ({source.published or '발행일 미확인'}) · {source.url}"
            result.append((value, "source", False, {
                "style_kind": note_role, "note_scope": scope, "note_source_id": identifier,
                "note_parent_role": parent, "note_marker": marker, "note_visual_kind": visual_kind,
            }))
    # Keep supplied but uncited references without pretending they annotate a
    # nearby claim. No cited source is duplicated into a trailing notes section.
    remaining = list(sources) if settings.get("reference_list") == "ALL" else [source for source in sources if source.id not in used]
    if remaining:
        result.append(("참고자료", "heading", True, {}))
        for source in remaining:
            value = f"{source.title} ({source.published or '발행일 미확인'}) · {source.url}"
            result.append((value, "source", False, {"unreferenced_source_id": source.id}))
    result[0][3]["local_notes"] = POLICY
    return result

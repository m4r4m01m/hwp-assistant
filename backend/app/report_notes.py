"""Local source explanations for new reference plans; canonical drafts stay intact."""
import re

POLICY = "reference-local-notes-v1"
TITLE_PRESENTATION_POLICY = "reference-note-titles-v1"
GROUPING_POLICY = "reference-note-groups-v1"
SECTION_GROUPING_POLICY = "reference-note-section-groups-v2"


def without_source_tokens(value, source_ids):
    """Remove only identified citation tokens, retaining every other character."""
    return re.sub(r"\[(S\d+)\]", lambda match: "" if match[1] in source_ids else match[0], value)


def localize_notes(blocks, sources, settings, *, presentation_policy=None, grouping_policy=None):
    from .report_contract import citation_marker, citation_style_for_scope, presentation_text
    from .standard_policy import BODY_ROLES

    known = {source.id: source for source in sources}
    titles_only = presentation_policy == TITLE_PRESENTATION_POLICY
    grouped = grouping_policy in (GROUPING_POLICY, SECTION_GROUPING_POLICY)
    section_grouped = grouping_policy == SECTION_GROUPING_POLICY
    def source_text(source):
        # Only a new plan opts in. The canonical draft retains the complete
        # title, URL and supplied date, including dates that need confirmation.
        return source.title if titles_only else f"{source.title} ({source.published or '발행일 미확인'}) · {source.url}"
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
        elif grouped and kind == "body" and meta.get("note_body_role") in BODY_ROLES:
            # New plans share source explanations across the uninterrupted
            # prose under a heading. A new square is another point, not another
            # copy of the same source. Explicit headings and visuals remain
            # independent scopes; the old opt-in retains argument-level notes.
            level = BODY_ROLES.index(meta["note_body_role"])
            while cursor < len(blocks):
                _, next_kind, _, next_meta = blocks[cursor]
                next_role = next_meta.get("note_body_role")
                if (next_kind != "body" or next_role not in BODY_ROLES
                        or not section_grouped and BODY_ROLES.index(next_role) <= level):
                    break
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
        common_table_source = identifiers[0] if grouped and visual_kind == "table" and len(identifiers) == 1 else None
        if common_table_source and any(not without_source_tokens(value, {common_table_source}).strip() for value, _, _, _ in group):
            # A source-only cell is itself a label. Keep that table's original
            # mapped marks rather than turning a valid cell into empty text.
            common_table_source = None
        used.update(identifiers)
        for value, role, page_break, metadata in group:
            marks = []
            metadata = {**metadata, "note_scope": scope, "citation_source_ids": identifiers}
            if common_table_source:
                # The printed source applies to the table. Preserve the exact
                # original per-cell/caption anchors in its frozen plan so the
                # shared display cannot erase which claims actually cited it.
                metadata.update(table_common_source_id=common_table_source, canonical_citation_text=value,
                                canonical_citations=[dict(source_id=match[1], start=match.start(), end=match.end())
                                                     for match in re.finditer(r"\[(S\d+)\]", value) if match[1] in known])
                value = without_source_tokens(value, {common_table_source})
                metadata.pop("citation_ranges", None)
            rendered = presentation_text(value, settings, known, marks=marks, marker_numbers=numbers)
            if marks and settings.get("citation_superscript"):
                metadata["citation_ranges"] = marks
            result.append((rendered, role, page_break, metadata))
        parent = meta.get("style_kind", kind)
        note_role = "table_source" if visual_kind else "note"
        for identifier in identifiers:
            source = known[identifier]
            marker = citation_marker(f"S{numbers[identifier]}", style)
            omitted_prefix = None
            # This optional policy applies only to explanation labels. Inline
            # ranges continue to use full 주1), 주2) markers at their source token.
            if settings.get("citation_note_abbreviated") and style == "NOTE" and numbers[identifier] > 1:
                marker = f"{numbers[identifier]})"
                omitted_prefix = "주"
            if common_table_source:
                marker = "자료:"
            value = f"{marker} {source_text(source)}"
            metadata = {
                "style_kind": note_role, "note_scope": scope, "note_source_id": identifier,
                "note_parent_role": parent, "note_marker": marker, "note_visual_kind": visual_kind,
            }
            if omitted_prefix:
                # Freeze the new geometry instruction with this plan. Existing
                # abbreviated plans without it retain their original layout.
                metadata["note_omitted_prefix"] = omitted_prefix
            result.append((value, "source", False, metadata))
    # Keep supplied but uncited references without pretending they annotate a
    # nearby claim. No cited source is duplicated into a trailing notes section.
    remaining = list(sources) if settings.get("reference_list") == "ALL" else [source for source in sources if source.id not in used]
    if remaining:
        result.append(("참고자료", "heading", True, {}))
        for source in remaining:
            value = source_text(source)
            result.append((value, "source", False, {"unreferenced_source_id": source.id}))
    result[0][3]["local_notes"] = POLICY
    if titles_only:
        result[0][3]["note_presentation"] = TITLE_PRESENTATION_POLICY
    if grouped:
        result[0][3]["note_grouping"] = grouping_policy
    return result

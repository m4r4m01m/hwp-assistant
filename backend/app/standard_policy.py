"""Opt-in presentation controls. Absent keys retain historical settings/hashes."""
BODY_ROLES = ("square", "circle", "dash", "dot")
HEADING_ROLES = ("heading", "subheading", "minor_heading", "numbered_4", "numbered_5", "numbered_6")
OPTIONAL_KEYS = ("enabled_body_roles", "enabled_heading_roles", "heading_structure_policy", "line_break_unit", "line_space_policy", "body_tracking_percent", "cover_mode", "toc_mode", "reference_list", "table_anchor_role", "table_anchor_percent", "citation_note_abbreviated")
LINE_POLICY_EXCLUDED_ROLES = frozenset(("title", "cover_title", "cover_date", "cover_department", "toc_title", "toc_major", "toc_minor"))
# The pinned HWPX parser and renderer preserve 0..75; these opt-in choices
# retain the earlier values and leave absent/ORIGINAL frozen settings untouched.
SPACE_COMPRESSION_PERCENT = {"NONE": 0, "TIGHT": 10, "TIGHTER": 20, "TIGHT_30": 30, "TIGHT_50": 50}


def cover_enabled(settings):
    return settings.get("cover_mode", "ORIGINAL") == "ON" or (settings.get("cover_mode", "ORIGINAL") == "ORIGINAL" and settings.get("preset_id") in ("appendix-2", "appendix-3"))


def toc_mode(settings):
    mode = settings.get("toc_mode", "ORIGINAL")
    if mode == "ORIGINAL":
        mode = {"appendix-2": "SEPARATE", "appendix-3": "COVER"}.get(settings.get("preset_id"), "NONE")
    return "SEPARATE" if mode == "COVER" and not cover_enabled(settings) else mode


def table_geometry(settings, page_width):
    """Point units: anchor interpolates between the bullet and following text."""
    role = settings.get("table_anchor_role", "PAGE")
    if role == "PAGE":
        width = page_width * settings["table_width_percent"] / 100
        return (page_width - width) / 2, width
    left = settings.get(role + "_left_indent_pt", 0)
    if role == "note" and not left:
        left = settings.get("circle_left_indent_pt", 0) + max(0, -settings.get("circle_first_line_indent_pt", 0))
    first = settings.get(role + "_first_line_indent_pt", 0)
    bullet = left + min(0, first)
    anchor = bullet + (left - bullet) * settings.get("table_anchor_percent", 50) / 100
    right = max(0, settings.get(role + "_right_indent_pt", 0))
    return max(0, anchor), (page_width - max(0, anchor) - right) * settings["table_width_percent"] / 100


def organize_paragraphs(paragraphs, settings, *, lead_summaries=None):
    """Opt-in new-plan structure: retain disabled headings as body context, not duplicate headings."""
    if settings.get("heading_structure_policy") != "CONTENT_V1":
        return list(paragraphs)
    import re
    # A Unicode syllable range also matches weekdays such as '(금)'. Only the
    # fourteen Korean ordinal glyphs are structural labels safe to remove.
    ordinals = "가나다라마바사아자차카타파하"
    patterns = ((rf"^[{ordinals}]\.\s+", "subheading"), (r"^(?:\(\d+\)|\d+\))\s+", "minor_heading"),
                (rf"^(?:\([{ordinals}]\)|[{ordinals}]\))\s+", "numbered_4"), (r"^[①-⑳]\s+", "numbered_5"),
                (r"^[㉮-㉻]\s+", "numbered_6"))
    enabled = settings.get("enabled_heading_roles", ",".join(HEADING_ROLES)).split(",")
    result, context = [], []
    for paragraph in paragraphs:
        found = next(((re.match(pattern, paragraph), role) for pattern, role in patterns if re.match(pattern, paragraph)), None)
        if found and found[1] not in enabled:
            context.append(paragraph[found[0].end():])
            continue
        if context:
            prefix = " · ".join(context)
            if found:
                result.append("□ " + prefix)
            else:
                # Keep an eligible lead-summary at the start so its existing ON/OFF contract still applies.
                from .report_contract import PARENTHETICAL_LABELS
                label = (lead_summaries or {}).get(paragraph)
                eligible = re.escape(label) if label is not None else PARENTHETICAL_LABELS
                lead = re.match(r"^([□ㅇ○\-․]\s+)(\((?:" + eligible + r")\)\s*)?", paragraph)
                paragraph = (lead[0] + prefix + ": " + paragraph[lead.end():]) if lead else "□ " + prefix + ": " + paragraph
            context = []
        result.append(paragraph)
    if context:
        result.append("□ " + " · ".join(context))
    return result


def validate_roles(value, roles):
    if value is None:
        return value
    if not isinstance(value, str) or not value or value != ",".join(role for role in roles if role in value.split(",")):
        raise ValueError("문단 내용 기호와 문단 구분은 각각 하나 이상을 순서대로 선택하세요.")
    return value


def enabled_role(role, settings):
    roles = BODY_ROLES if role in BODY_ROLES else HEADING_ROLES if role in HEADING_ROLES else ()
    if not roles:
        return role
    key = "enabled_body_roles" if role in BODY_ROLES else "enabled_heading_roles"
    enabled = settings.get(key)
    if enabled is None or role in enabled.split(","):
        return role
    chosen = [item for item in roles if item in enabled.split(",")]
    if not chosen:
        raise ValueError("최소 한 항목을 사용해야 합니다.")
    prior = [item for item in chosen if roles.index(item) < roles.index(role)]
    return prior[-1] if prior else chosen[0]


def line_fields():
    from .reference_source import roles_source, HH
    roles = roles_source()
    selected = [source["para"] for role, source in roles.items() if role not in LINE_POLICY_EXCLUDED_ROLES]
    condense = sorted({int(para.get("condense", "0")) for para in selected})
    breaking = {(para.find(f"{{{HH}}}breakSetting").get("breakNonLatinWord"),
                 para.find(f"{{{HH}}}breakSetting").get("breakLatinWord")) for para in selected}
    original_break = "WORD" if breaking == {("BREAK_WORD", "KEEP_WORD")} else "CHARACTER" if breaking == {("KEEP_WORD", "BREAK_WORD")} else None
    break_label = {"WORD": "단어·어절 유지", "CHARACTER": "글자 단위"}.get(original_break, "역할별 줄바꿈")
    original_compression = next((key for key, value in SPACE_COMPRESSION_PERCENT.items() if condense == [value]), None)
    compression_label = "·".join(str(value) + "%" for value in condense)
    break_help = {
        "ORIGINAL": "기준서에 지정된 문단별 줄바꿈을 유지합니다.",
        "WORD": "한글 어절·영문 단어를 가급적 같은 줄에 둡니다. 남은 폭에 들어가지 않으면 다음 줄로 넘깁니다.",
        "CHARACTER": "단어 중간에서도 글자 단위로 줄을 바꿉니다.",
    }
    space_help = {
        "ORIGINAL": f"기준서의 공백 압축값({compression_label})을 유지합니다.",
        "NONE": "단어 사이 빈칸을 압축하지 않습니다.",
        **{key: f"다음 단어가 들어갈 때만 그 줄의 빈칸을 최대 {percent}% 줄입니다. 글자 간격은 바꾸지 않습니다."
           for key, percent in SPACE_COMPRESSION_PERCENT.items() if percent},
    }
    return [
        dict(key="citation_note_abbreviated", label="하단 주석의 ‘주’ 반복 생략", group="면 표시·주석", type="boolean", optional=True, default=False,
             help="본문 윗첨자는 주1), 주2)를 유지합니다. 아래 설명만 주1) 설명, 2) 설명으로 표시합니다. 번호 고정과 자동의 번호 주석에 적용하며 별표(*, **)는 그대로 표시합니다."),
        dict(key="line_break_unit", label="줄바꿈 단위", group="줄바꿈·줄 채움", type="select", optional=True, default="ORIGINAL",
             options=[dict(value=key, label=label) for key, label in (("ORIGINAL", f"기준서 값 · {break_label}"), ("WORD", "단어·어절 유지"), ("CHARACTER", "글자 단위"))],
             help="본문·문단 구분·표·주석의 자동 줄바꿈 단위를 선택합니다.", help_by_value=break_help,
             option_aliases={"ORIGINAL": original_break} if original_break else {}),
        dict(key="line_space_policy", label="줄 끝 공백 압축", group="줄바꿈·줄 채움", type="select", optional=True, default="ORIGINAL",
             options=[dict(value="ORIGINAL", label=f"기준서 값 · 공백 압축 {compression_label}"),
                      *[dict(value=key, label=f"빈칸 너비의 최대 {percent}%" if percent else "사용 안 함 · 0%") for key, percent in SPACE_COMPRESSION_PERCENT.items()]],
             help="다음 단어가 같은 줄에 들어갈 수 있도록 빈칸만 줄입니다.", help_by_value=space_help,
             option_aliases={"ORIGINAL": original_compression} if original_compression else {}),
        dict(key="body_tracking_percent", label="문단 내용 자간 보정", group="줄바꿈·줄 채움", type="number", optional=True, default=0, min=-20, max=50, step=1, unit="%",
             help="각 기호 항목의 자간에 더하는 값입니다. -5%처럼 음수는 좁히고, 0%는 유지하며 양수는 넓힙니다."),
        dict(key="cover_mode", label="표지", group="문서 구성", type="select", optional=True, default="ORIGINAL", options=[dict(value=v, label=l) for v, l in (("ORIGINAL", "기본 별첨 구성"), ("OFF", "없음"), ("ON", "사용"))]),
        dict(key="toc_mode", label="목차", group="문서 구성", type="select", optional=True, default="ORIGINAL", options=[dict(value=v, label=l) for v, l in (("ORIGINAL", "기본 별첨 구성"), ("NONE", "없음"), ("SEPARATE", "별도 쪽"), ("COVER", "표지 안 · 표지가 없으면 별도 쪽"))]),
        dict(key="reference_list", label="참고·출처 목록", group="문서 구성", type="select", optional=True, default="LOCAL", options=[dict(value=v, label=l) for v, l in (("LOCAL", "인용 위치 주석 · 미인용 자료만 끝에"), ("ALL", "주석과 별도 전체 출처 목록"))], help="출처는 삭제하지 않습니다. 전체 목록을 켜면 같은 출처를 문서 끝에서도 확인할 수 있습니다."),
        dict(key="table_anchor_role", label="표 너비 기준", group="표 구조", type="select", optional=True, default="PAGE", options=[dict(value=v, label=l) for v,l in (("PAGE", "용지 본문 폭"), ("square", "□ 항목 들여쓰기"), ("circle", "ㅇ 항목 들여쓰기"), ("dash", "- 항목 들여쓰기"), ("dot", "․ 항목 들여쓰기"), ("note", "주석 들여쓰기"))]),
        dict(key="table_anchor_percent", label="기호와 내용 사이 시작점", group="표 구조", type="number", optional=True, default=50, min=0, max=100, step=1, unit="%", disabled_when=dict(key="table_anchor_role", value="PAGE"), help="0%는 기호 시작점, 50%는 기호와 내용 시작점의 중간, 100%는 내용 시작점입니다. 오른쪽은 해당 문단의 오른쪽 여백까지 채웁니다."),
    ]

"""Display-only JSON prefixes. These values can never approve or publish a report."""
import json
import re


def partial_json(raw):
    """Read an unfinished JSON value, retaining only received string characters."""
    pos = 0
    def space():
        nonlocal pos
        while pos < len(raw) and raw[pos].isspace(): pos += 1
    def string():
        nonlocal pos
        start = pos; pos += 1
        while pos < len(raw):
            if raw[pos] == '"':
                pos += 1
                return json.loads(raw[start:pos]).encode('utf-16', 'surrogatepass').decode('utf-16', 'ignore'), True
            if raw[pos] == '\\':
                size = 6 if raw[pos:pos+2] == '\\u' else 2
                if pos + size > len(raw): break
                pos += size
            else: pos += 1
        # Decode a complete prefix only. Remove a lone high surrogate until its
        # matching low surrogate arrives; don't invent a replacement character.
        value = json.loads(raw[start:pos] + '"')
        value = value.encode('utf-16', 'surrogatepass').decode('utf-16', 'ignore')
        return value, False
    def value(depth=0):
        nonlocal pos
        if depth > 8: raise ValueError('Report nesting limit')
        space()
        if pos >= len(raw): return None, False
        if raw[pos] == '"': return string()
        if raw[pos] not in '[{': raise ValueError('Unexpected report value')
        obj = raw[pos] == '{'; close = '}' if obj else ']'
        result = {} if obj else []; pos += 1
        while True:
            space()
            if pos >= len(raw): return result, False
            if raw[pos] == close: pos += 1; return result, True
            if obj:
                if raw[pos] != '"': raise ValueError('Unexpected key')
                key, complete = string()
                if not complete: return result, False
                space()
                if pos >= len(raw): return result, False
                if raw[pos] != ':': raise ValueError('Missing colon')
                pos += 1
            item, complete = value(depth + 1)
            if item is not None:
                if obj: result[key] = item
                else: result.append(item)
            if not complete: return result, False
            space()
            if pos >= len(raw): return result, False
            if raw[pos] == close: pos += 1; return result, True
            if raw[pos] != ',': raise ValueError('Missing comma')
            pos += 1
    return value()[0]


def preview_blocks(raw):
    try: draft = partial_json(raw)
    except (ValueError, UnicodeError): return []
    if not isinstance(draft, dict): return []
    blocks = []
    def clean(value, limit=800):
        return re.sub(r'[\x00-\x1f\x7f]', '', value)[:limit] if isinstance(value, str) else ''
    entries = draft.get('emphasis')
    emphasis = [{'text': clean(e.get('text'), 30), 'style': e['style']}
                for e in (entries[:12] if isinstance(entries, list) else []) if isinstance(e, dict)
                and clean(e.get('text'), 30) and e.get('style') in ('bold', 'blue', 'bold_blue')]
    def add(text, kind, limit=800):
        if not isinstance(text, str) or not text: return
        text = clean(text, limit)
        if kind == 'body':
            kind = 'square' if text.startswith('□') else 'circle' if text.startswith(('ㅇ', '○')) else 'dash' if text.startswith('-') else kind
        blocks.append({'text': text, 'kind': kind})
        if emphasis: blocks[-1]['emphasis'] = emphasis
    add(draft.get('title'), 'title', 120)
    if draft.get('summary'):
        add('핵심 요약', 'heading'); add(draft.get('summary'), 'body', 700)
    sections = draft.get('sections')
    if isinstance(sections, list):
        table_number = diagram_number = 0
        for i, section in enumerate(sections[:6], 1):
            if not isinstance(section, dict): continue
            if isinstance(section.get('heading'), str): add(f"{i}. {section['heading']}", 'heading', 130)
            if isinstance(section.get('paragraphs'), list):
                for p in section['paragraphs'][:9]: add(p, 'body')
            tables = section.get('tables')
            for table in tables[:1] if isinstance(tables, list) else []:
                if not isinstance(table, dict): continue
                table_number += 1
                columns = table.get('columns'); rows = table.get('rows')
                blocks.append({'text': f"표 {table_number}. {clean(table.get('caption'), 90)}", 'kind': 'table',
                    'columns': [clean(c, 20) for c in columns[:4]] if isinstance(columns, list) else [],
                    'rows': [[clean(c, 52) for c in row[:4]] for row in rows[:5] if isinstance(row, list)] if isinstance(rows, list) else [],
                    'emphasis': emphasis})
            diagrams = section.get('diagrams')
            for diagram in diagrams[:1] if isinstance(diagrams, list) else []:
                if not isinstance(diagram, dict): continue
                diagram_number += 1
                steps = diagram.get('steps')
                blocks.append({'text': f"그림 {diagram_number}. {clean(diagram.get('caption'), 90)}", 'kind': 'diagram',
                    'steps': [clean(s, 48) for s in steps[:4]] if isinstance(steps, list) else [], 'emphasis': emphasis})
    return blocks

"""Codex app-server report deltas; no new model/provider, no document writes."""
import json
import re
import time
from .report_contract import ReportDraft, canonical
from .report_partial import preview_blocks
from .report_rpc import ReportRpc
from .report_errors import ReportGenerationError, upstream_details
from .report_research import apply_research_policy, research_policy

DISABLED = ('shell_tool', 'unified_exec', 'apps', 'plugins', 'browser_use', 'computer_use',
            'image_generation', 'multi_agent', 'skill_search', 'hooks', 'memories',
            'auth_elicitation', 'mentions_v2', 'remote_plugin', 'tool_suggest')


def stream_command(settings, instructions, research):
    args = [settings.executable, 'app-server', '--listen', 'stdio://']
    overrides = {'approval_policy': 'never', 'sandbox_mode': 'read-only', 'model_provider': 'openai',
        'notify': [], 'project_doc_max_bytes': 0, 'model_instructions_file': str(instructions),
        'developer_instructions': '', 'web_search': 'live' if research else 'disabled',
        'model_reasoning_effort': 'medium', 'model_verbosity': 'medium', 'service_tier': 'default'}
    if settings.model: overrides['model'] = settings.model
    for key, value in overrides.items(): args += ['-c', key + '=' + json.dumps(value, ensure_ascii=False)]
    for feature in DISABLED: args += ['--disable', feature]
    args += ['--enable', 'skip_host_skill_discovery']
    return args


def generate_stream(value, settings, directory, prompt, contract, progress, active, *, started=None):
    from pathlib import Path
    started = started if started is not None else time.monotonic()
    instructions = Path(directory) / 'report-instructions.txt'
    instructions.write_text(prompt, encoding='utf-8')
    state = {'stage': 'starting', 'blocks': [], 'activity': '', 'research_calls': 0, 'timings': {}}
    progress(state)
    evidence = {'executor': 'codex-app-server', 'model': settings.model, 'contract': contract,
                'completed': False, 'web_search_calls': 0, 'shell_tool': False, 'streamed': True}
    pending, buffers, searches = [], {}, {}
    started_searches = set()
    first_text = None
    last_emit = 0
    pending_emit = False
    final = None
    upstream_error = {}
    thread_id = turn_id = None
    def emit(force=False):
        nonlocal last_emit, pending_emit
        pending_emit = True
        now = time.monotonic()
        if force or now - last_emit >= .2:
            state['elapsed_ms'] = round((now - started) * 1000)
            progress(state); last_emit = now; pending_emit = False
    def flush_pending():
        # Flush only a received update suppressed by the write throttle. The
        # same worker keeps owner/status checks and cancellation/cleanup intact;
        # silence alone never creates a new progress snapshot.
        if pending_emit: emit()
    def check_request(event):
        if 'method' in event and 'id' in event:
            # No approval/tool execution/user input/credential requests are accepted.
            raise ReportGenerationError('scope_violation')
    def completed_message(item):
        nonlocal final
        if item.get('phase') not in (None, 'final_answer') or item.get('delivery') is not None:
            return  # Commentary/async messages do not finish the turn.
        text = item.get('text', '')
        if not isinstance(text, str): raise ReportGenerationError('protocol_error')
        if len(text) > 131072: raise ReportGenerationError('output_limit')
        # Completed messages are authoritative; never recover a final from delta
        # buffers. A later non-JSON final cannot reuse an earlier JSON message.
        final = text if text.lstrip().startswith('{') else None
        if final is not None:
            state['blocks'] = preview_blocks(text); emit(True)
    with ReportRpc(stream_command(settings, instructions, value['research']), environment=dict(settings.environment),
                   cwd=directory, timeout=settings.timeout, active=active) as rpc:
        serial = 0
        def call(method, params):
            nonlocal serial
            serial += 1; request_id = serial
            rpc.send({'id': request_id, 'method': method, 'params': params})
            while True:
                event = rpc.receive(); check_request(event)
                if event.get('id') == request_id:
                    if 'error' in event: raise ReportGenerationError('protocol_error')
                    return event['result']
                pending.append(event)
                if len(pending) > 256: raise ReportGenerationError('protocol_error')
        call('initialize', {'clientInfo': {'name': 'hwp_assistant', 'title': 'HWP Assistant', 'version': '0.1.0'}})
        rpc.send({'method': 'initialized'})
        account = call('account/read', {'refreshToken': False}).get('account') or {}
        if account.get('type') != 'chatgpt': raise ReportGenerationError('login_required')
        config = call('config/read', {'includeLayers': False})['config']
        # app-server has no --ignore-user-config. Disable every configured MCP
        # before thread/start, pin report instructions/tools and reject custom routes.
        if config.get('openai_base_url') or config.get('chatgpt_base_url') not in (None, 'https://chatgpt.com/backend-api/', 'https://chatgpt.com/backend-api'):
            raise ReportGenerationError('configuration_error')
        if (config.get('model_providers') or {}).get('openai'):
            raise ReportGenerationError('configuration_error')
        overrides = {}
        for name in config.get('mcp_servers') or {}:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', name): raise ReportGenerationError('configuration_error')
            overrides[f'mcp_servers.{name}.enabled'] = False
        thread = call('thread/start', {'cwd': directory, 'model': settings.model, 'modelProvider': 'openai',
            'sandbox': 'read-only', 'approvalPolicy': 'never', 'ephemeral': True, 'config': overrides,
            'baseInstructions': prompt, 'developerInstructions': ''})
        if (settings.model and thread['model'] != settings.model) or thread['modelProvider'] != 'openai' or thread['approvalPolicy'] != 'never' or thread['sandbox']['type'] != 'readOnly' or thread.get('instructionSources'):
            raise ReportGenerationError('configuration_error')
        thread_id = thread['thread']['id']
        evidence.update(codex_thread_id=thread_id, model=thread['model'], reasoning_effort=thread.get('reasoningEffort'))
        state['timings']['startup_ms'] = round((time.monotonic() - started) * 1000)
        state['stage'] = 'waiting'; emit(True)
        result = call('turn/start', {'threadId': thread_id,
            'input': [{'type': 'text', 'text': 'Report request (data):\n' + canonical(value), 'text_elements': []}],
            'outputSchema': ReportDraft.generation_schema()})
        turn_id = result['turn']['id']
        while True:
            event = pending.pop(0) if pending else rpc.receive(on_idle=flush_pending)
            check_request(event)
            method, params = event.get('method'), event.get('params', {})
            if params.get('threadId', thread_id) != thread_id or params.get('turnId', turn_id) != turn_id: continue
            item = params.get('item', {})
            if method == 'error':
                # A retry notice is not a completed/failed turn. Wait for the
                # authoritative turn/completed event within the same deadline.
                upstream_error = upstream_details(params.get('error'))
            if method in ('item/started', 'item/completed'):
                kind, item_id = item.get('type'), item.get('id')
                if kind not in ('userMessage', 'agentMessage', 'reasoning', 'webSearch', 'plan', 'contextCompaction'):
                    raise ReportGenerationError('scope_violation')
                if kind == 'webSearch':
                    if not value['research']: raise ReportGenerationError('scope_violation')
                    action = item.get('action') or {}
                    label = action.get('query') or item.get('query') or action.get('url') or '공개자료 확인'
                    state.update(stage='researching', activity=str(label)[:240])
                    if method == 'item/started':
                        started_searches.add(item_id)
                        searches.setdefault(item_id, [time.monotonic(), None])
                    else:
                        if item_id not in searches: searches[item_id] = [time.monotonic(), None]
                        if searches[item_id][1] is None:
                            searches[item_id][1] = time.monotonic(); evidence['web_search_calls'] += 1
                        state['research_calls'] = evidence['web_search_calls']
                    emit(True)
                if kind == 'agentMessage' and method == 'item/completed':
                    completed_message(item)
            if method == 'item/agentMessage/delta':
                item_id = params['itemId']
                text = buffers.get(item_id, '') + params['delta']
                buffers[item_id] = text
                if sum(map(len, buffers.values())) > 131072 or len(buffers) > 32: raise ReportGenerationError('output_limit')
                if text.lstrip().startswith('{'):
                    blocks = preview_blocks(text)
                    if blocks:
                        first = first_text is None
                        if first_text is None:
                            first_text = time.monotonic()
                            state['timings']['first_text_ms'] = round((first_text - started) * 1000)
                        state.update(stage='writing', blocks=blocks); emit(first)
            if method == 'thread/tokenUsage/updated': evidence['usage'] = params.get('tokenUsage', {}).get('total', {})
            if method == 'turn/completed':
                if params['turn']['id'] != turn_id: continue
                if params['turn']['status'] != 'completed':
                    raise ReportGenerationError('model_failed', **(upstream_details(params['turn'].get('error')) or upstream_error))
                # Codex 0.153 also supplies the final agent message in the
                # terminal turn summary. This is a completed-item fallback,
                # never permission to promote an incomplete delta buffer.
                for completed in params['turn'].get('items', []):
                    if completed.get('type') == 'agentMessage':
                        completed_message(completed)
                    elif completed.get('type') not in ('userMessage', 'reasoning', 'webSearch', 'plan', 'contextCompaction'):
                        raise ReportGenerationError('scope_violation')
                if final is None: raise ReportGenerationError('incomplete_output')
                if value['research'] and not evidence['web_search_calls'] and research_policy(value) == 'require_completed':
                    raise ReportGenerationError('research_unavailable')
                ended = time.monotonic()
                state['timings'].update(generation_ms=round((ended-started)*1000),
                    text_ms=round((ended-first_text)*1000) if first_text else None)
                # Search calls can overlap. Measure their union, not summed time.
                intervals = sorted((a, b) for a, b in searches.values() if b is not None)
                duration = 0; edge = 0
                for a, b in intervals: duration += max(0, b-max(a, edge)); edge = max(edge, b)
                state['timings']['research_ms'] = round(duration * 1000)
                state['stage'] = 'validating'; emit(True)
                draft = ReportDraft.model_validate_json(final).model_dump()
                if 'research_notice' in json.loads(final):
                    raise ReportGenerationError('invalid_report')  # Server-owned, never model assertions.
                evidence.update(completed=True, timings=dict(state['timings']),
                    web_search_started=len(started_searches),
                    web_search_incomplete=sum(searches[item_id][1] is None for item_id in started_searches))
                return apply_research_policy(value, draft, evidence)

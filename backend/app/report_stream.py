"""Codex app-server report deltas; no new model/provider, no document writes."""
import json
import re
import time
from .report_contract import ReportDraft, canonical
from .report_partial import preview_blocks
from .report_rpc import ReportRpc

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
    first_text = None
    last_emit = 0
    pending_emit = False
    final = None
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
            raise RuntimeError('Unexpected Codex report server request')
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
                    if 'error' in event: raise RuntimeError('Codex report protocol rejected ' + method)
                    return event['result']
                pending.append(event)
                if len(pending) > 256: raise RuntimeError('Report startup event limit')
        call('initialize', {'clientInfo': {'name': 'hwp_assistant', 'title': 'HWP Assistant', 'version': '0.1.0'}})
        rpc.send({'method': 'initialized'})
        account = call('account/read', {'refreshToken': False}).get('account') or {}
        if account.get('type') != 'chatgpt': raise RuntimeError('Existing ChatGPT login required')
        config = call('config/read', {'includeLayers': False})['config']
        # app-server has no --ignore-user-config. Disable every configured MCP
        # before thread/start, pin report instructions/tools and reject custom routes.
        if config.get('openai_base_url') or config.get('chatgpt_base_url') not in (None, 'https://chatgpt.com/backend-api/', 'https://chatgpt.com/backend-api'):
            raise RuntimeError('Custom report provider route is not supported')
        if (config.get('model_providers') or {}).get('openai'):
            raise RuntimeError('Custom OpenAI provider override is not supported')
        overrides = {}
        for name in config.get('mcp_servers') or {}:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', name): raise RuntimeError('Unsupported MCP configuration key')
            overrides[f'mcp_servers.{name}.enabled'] = False
        thread = call('thread/start', {'cwd': directory, 'model': settings.model, 'modelProvider': 'openai',
            'sandbox': 'read-only', 'approvalPolicy': 'never', 'ephemeral': True, 'config': overrides,
            'baseInstructions': prompt, 'developerInstructions': ''})
        if (settings.model and thread['model'] != settings.model) or thread['modelProvider'] != 'openai' or thread['approvalPolicy'] != 'never' or thread['sandbox']['type'] != 'readOnly' or thread.get('instructionSources'):
            raise RuntimeError('Report execution configuration mismatch')
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
            if method in ('item/started', 'item/completed'):
                kind, item_id = item.get('type'), item.get('id')
                if kind not in ('userMessage', 'agentMessage', 'reasoning', 'webSearch', 'plan', 'contextCompaction'):
                    raise RuntimeError('Tool outside the report scope')
                if kind == 'webSearch':
                    if not value['research']: raise RuntimeError('Unexpected report research')
                    action = item.get('action') or {}
                    label = action.get('query') or item.get('query') or action.get('url') or '공개자료 확인'
                    state.update(stage='researching', activity=str(label)[:240])
                    if method == 'item/started': searches[item_id] = [time.monotonic(), None]
                    else:
                        if item_id not in searches: searches[item_id] = [time.monotonic(), None]
                        if searches[item_id][1] is None:
                            searches[item_id][1] = time.monotonic(); evidence['web_search_calls'] += 1
                        state['research_calls'] = evidence['web_search_calls']
                    emit(True)
                if kind == 'agentMessage' and method == 'item/completed':
                    text = item.get('text', '')
                    if len(text) > 131072: raise RuntimeError('Report output size limit')
                    if text.lstrip().startswith('{'):
                        final = text; state['blocks'] = preview_blocks(text); emit(True)
            if method == 'item/agentMessage/delta':
                item_id = params['itemId']
                text = buffers.get(item_id, '') + params['delta']
                buffers[item_id] = text
                if sum(map(len, buffers.values())) > 131072 or len(buffers) > 32: raise RuntimeError('Report stream size limit')
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
                if params['turn']['status'] != 'completed' or final is None: raise RuntimeError('Report did not complete')
                if value['research'] and not evidence['web_search_calls']: raise RuntimeError('No completed public research')
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
                evidence.update(completed=True, timings=dict(state['timings']))
                return draft, evidence

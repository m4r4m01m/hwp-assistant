"""Codex app-server report deltas; no new model/provider, no document writes."""
import json
import re
import time
from hashlib import sha256
from .report_contract import ReportDraft, canonical
from .report_partial import preview_blocks
from .report_rpc import ReportRpc
from .report_errors import ReportGenerationError, upstream_details, retain_upstream_details
from .report_research import apply_research_policy, research_policy
from .report_structure import generation_schema, check_outline

DISABLED = ('shell_tool', 'unified_exec', 'apps', 'plugins', 'browser_use', 'computer_use',
            'image_generation', 'multi_agent', 'skill_search', 'hooks', 'memories',
            'auth_elicitation', 'mentions_v2', 'remote_plugin', 'tool_suggest')


def stream_command(settings, instructions, research):
    args = [settings.executable, 'app-server', '--listen', 'stdio://']
    overrides = {'approval_policy': 'never', 'sandbox_mode': 'read-only', 'model_provider': 'openai',
        'notify': [], 'project_doc_max_bytes': 0, 'model_instructions_file': str(instructions),
        'developer_instructions': '', 'web_search': 'live' if research else 'disabled',
        'model_reasoning_effort': 'medium', 'model_verbosity': 'medium', 'service_tier': 'default'}
    if research:
        # code_mode_only models otherwise hide web.run inside functions.exec.
        # Expose only the already permitted web namespace to report research.
        overrides['features.code_mode.direct_only_tool_namespaces'] = ['web']
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
    item_types, completed_items = {}, {}
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
    def identify_item(item_id, kind):
        if not isinstance(item_id, str) or not item_id.strip():
            raise ReportGenerationError('protocol_error')
        if item_id in item_types and item_types[item_id] != kind:
            raise ReportGenerationError('protocol_error')
        item_types[item_id] = kind
    def observe_item(item, *, completed, summary=False):
        # The same checks cover lifecycle events and terminal summary fallback.
        # Started items may acquire query/action/results on completion; only
        # repeated *completed* payloads must agree. Never retain search content.
        if not isinstance(item, dict): raise ReportGenerationError('protocol_error')
        kind, item_id = item.get('type'), item.get('id')
        if kind not in ('userMessage', 'agentMessage', 'reasoning', 'webSearch', 'plan', 'contextCompaction'):
            raise ReportGenerationError('scope_violation')
        if kind == 'webSearch' and not value['research']:
            raise ReportGenerationError('scope_violation')
        identify_item(item_id, kind)
        if kind == 'webSearch':
            action = item.get('action')
            if 'query' in item and not isinstance(item['query'], str):
                raise ReportGenerationError('protocol_error')
            if item.get('results') is not None and not isinstance(item['results'], list):
                raise ReportGenerationError('protocol_error')
            if action is not None and (not isinstance(action, dict) or action.get('type') not in ('search', 'openPage', 'findInPage', 'other')):
                raise ReportGenerationError('protocol_error')
            if action is not None:
                fields = {'search': ('query',), 'openPage': ('url',), 'findInPage': ('url', 'pattern'), 'other': ()}
                if any(action.get(key) is not None and not isinstance(action[key], str) for key in fields[action['type']]):
                    raise ReportGenerationError('protocol_error')
                queries = action.get('queries') if action['type'] == 'search' else None
                if queries is not None and (not isinstance(queries, list) or any(not isinstance(query, str) for query in queries)):
                    raise ReportGenerationError('protocol_error')
            if summary and action is None:
                # 0.153 has no webSearch.status. History may retain a started
                # snapshot (null action/results); turn success cannot promote
                # that snapshot. A completed history payload has an action.
                completed = False
            fingerprint = [item.get('query'), action, item.get('results')]
        elif kind == 'agentMessage':
            fingerprint = [item.get('text'), item.get('phase'), item.get('delivery')]
        else:
            fingerprint = item
        if completed:
            digest = sha256(canonical(fingerprint).encode('utf-8')).hexdigest()
            if item_id in completed_items:
                if completed_items[item_id] != digest:
                    raise ReportGenerationError('protocol_error')
                return  # Replayed events/summary must not recount or revive an old final.
            completed_items[item_id] = digest
        if kind == 'webSearch':
            state.update(stage='researching', activity='공개자료 확인')
            if not completed:
                started_searches.add(item_id)
                searches.setdefault(item_id, [time.monotonic(), None])
            else:
                searches.setdefault(item_id, [time.monotonic(), None])
                searches[item_id][1] = time.monotonic()
                evidence['web_search_calls'] += 1
                state['research_calls'] = evidence['web_search_calls']
            emit(True)
        if kind == 'agentMessage' and completed:
            completed_message(item)
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
            'outputSchema': generation_schema(value)})
        turn_id = result['turn']['id']
        while True:
            event = pending.pop(0) if pending else rpc.receive(on_idle=flush_pending)
            check_request(event)
            method, params = event.get('method'), event.get('params', {})
            if not isinstance(params, dict): raise ReportGenerationError('protocol_error')
            if params.get('threadId') != thread_id: continue
            if method == 'turn/completed':
                # Pinned TurnCompletedNotification has threadId + turn, not a
                # required outer turnId. Validate the authoritative nested ID.
                turn = params.get('turn')
                if not isinstance(turn, dict): raise ReportGenerationError('protocol_error')
                if turn.get('id') != turn_id or params.get('turnId', turn_id) != turn_id: continue
            elif params.get('turnId') != turn_id:
                continue
            item = params.get('item', {})
            if method == 'error':
                # A retry notice is not a completed/failed turn. Wait for the
                # authoritative turn/completed event within the same deadline.
                upstream_error = retain_upstream_details(upstream_error, params.get('error'))
            if method in ('item/started', 'item/completed'):
                observe_item(item, completed=method == 'item/completed')
            if method == 'item/agentMessage/delta':
                item_id = params.get('itemId')
                identify_item(item_id, 'agentMessage')
                if not isinstance(params.get('delta'), str): raise ReportGenerationError('protocol_error')
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
                # 0.153 currently summarizes only the final agent message.
                # Also handle completed search payloads if supplied; this is
                # defensive compatibility, not proof of a missing live event.
                for completed in params['turn'].get('items', []):
                    observe_item(completed, completed=True, summary=True)
                if final is None: raise ReportGenerationError('incomplete_output')
                if value['research'] and not evidence['web_search_calls'] and research_policy(value) == 'require_completed':
                    raise ReportGenerationError('research_unavailable', **upstream_error)
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
                check_outline(value, draft)
                evidence.update(completed=True, timings=dict(state['timings']),
                    web_search_started=len(started_searches),
                    web_search_incomplete=sum(searches[item_id][1] is None for item_id in started_searches))
                if upstream_error:
                    evidence['upstream_diagnostics'] = dict(upstream_error)
                return apply_research_policy(value, draft, evidence)

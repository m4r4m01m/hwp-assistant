"""Synthetic child process speaking the installed Codex protocol; never calls AI."""
import json
import sys
import time
from pathlib import Path

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

folder = Path(sys.argv[1])
mode = sys.argv[2]
draft = json.loads((folder / 'draft.json').read_text(encoding='utf-8'))
def send(value): print(json.dumps(value, ensure_ascii=False), flush=True)
def event(method, **params): send({'method': method, 'params': {'threadId': 'thread', 'turnId': 'turn', **params}})
for line in sys.stdin:
    request = json.loads(line); method = request['method']; rid = request.get('id')
    if method == 'initialized': continue
    result = {}
    if method == 'account/read': result = {'account': {'type': 'chatgpt'}}
    if method == 'config/read': result = {'config': {'mcp_servers': {'test_disabled': {}}, 'chatgpt_base_url': 'https://chatgpt.com/backend-api/'}}
    if method == 'thread/start':
        assert request['params']['config'] == {'mcp_servers.test_disabled.enabled': False}
        assert request['params']['ephemeral'] is True
        result = {'thread': {'id': 'thread'}, 'model': 'test-model', 'modelProvider': 'openai',
                  'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly'}, 'instructionSources': [], 'reasoningEffort': 'medium'}
    if method == 'turn/start':
        (folder / 'turn-request.json').write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        result = {'turn': {'id': 'turn'}}
    send({'id': rid, 'result': result})
    if method != 'turn/start': continue
    event('item/started', item={'id': 'search', 'type': 'webSearch', 'action': {'type': 'search', 'query': '합성 조회'}})
    event('item/completed', item={'id': 'search', 'type': 'webSearch'})
    if mode == 'tool': event('item/started', item={'id': 'tool', 'type': 'commandExecution'})
    raw = json.dumps(draft, ensure_ascii=False)
    cut = raw.index('summary') - 3 if mode != 'e2e' else raw.index('summary') + len('summary": "') + 24
    event('item/agentMessage/delta', itemId='answer', delta=raw[:cut])
    (folder / 'delta-sent').touch()
    while not (folder / 'release').exists(): time.sleep(.01)
    if mode == 'disconnect': sys.exit(0)
    if mode == 'e2e':
        middle = raw.index('sources') - 3
        event('item/agentMessage/delta', itemId='answer', delta=raw[cut:middle])
        cut = middle
        (folder / 'second-sent').touch()
        while not (folder / 'finish').exists(): time.sleep(.01)
    event('item/agentMessage/delta', itemId='answer', delta=raw[cut:])
    # A different request's event must not contaminate this report.
    send({'method': 'item/agentMessage/delta', 'params': {'threadId': 'foreign', 'turnId': 'turn', 'itemId': 'bad', 'delta': '{"title":"WRONG"}'}})
    event('item/completed', item={'id': 'answer', 'type': 'agentMessage', 'text': '{}' if mode == 'invalid' else raw})
    event('thread/tokenUsage/updated', tokenUsage={'total': {'outputTokens': 50}})
    event('turn/completed', turn={'id': 'turn', 'status': 'failed' if mode == 'failed' else 'completed'})

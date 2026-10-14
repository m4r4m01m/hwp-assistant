"""Opt-in synthetic rich report with the existing Codex login; no DB/document writes."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.codex_runner import capture_settings
from app.report_runner import generate_report
from app.report_contract import ReportDraft, report_blocks
from app.report_rpc import ReportRpc

parser = argparse.ArgumentParser()
parser.add_argument('--actual-codex', action='store_true')
parser.add_argument('--out', required=True)
args = parser.parse_args()
if not args.actual_codex: parser.error('Explicit --actual-codex opt-in required')
target = Path(args.out)
if target.exists(): parser.error('Choose a new path; historical evidence is immutable')
start = time.monotonic(); snapshots = []
def progress(value):
    snapshots.append({'elapsed_ms': round((time.monotonic() - start) * 1000), 'stage': value['stage'],
                      'kinds': [b['kind'] for b in value['blocks']]})
proof = {'actual_codex_calls': 1, 'real_engine': False, 'human_approval': False, 'document_saved': False, 'research': False}
receive = ReportRpc.receive
def inspected_receive(rpc):
    event = receive(rpc)
    if event.get('method') == 'turn/completed':
        error = event.get('params', {}).get('turn', {}).get('error')
        if error: proof['synthetic_turn_error'] = error
    return event
try:
    with patch.object(ReportRpc, 'receive', inspected_receive):
        draft, evidence = generate_report({'id': str(uuid.uuid4()), 'research': False, 'prompt':
        '가상 금융회사의 보안 대응 훈련 계획을 경영진에게 보고하는 짧은 보고서를 작성해줘. 모두 합성 시험용 제안이야. '
        '3개 절로 구성하고 역할·조치·기한은 표 하나, 신고→확인→대응→복구 순서는 흐름도 하나로 표현해줘. '
        '본문에는 해석만 짧게 두고 표 내용을 반복하지 마. 핵심 조치와 기한에는 굵기·파란색 강조를 선택해줘. '
        '실제 사고·통계·법률·출처를 만들지 말고 외부 검색은 하지 마.'}, capture_settings(), progress)
    value = ReportDraft.model_validate(draft)
    assert sum(len(s.tables) for s in value.sections) >= 1
    assert sum(len(s.diagrams) for s in value.sections) >= 1
    assert value.emphasis and len(report_blocks(draft, concise=True, rich=True)) <= 180
    proof.update(result='passed', draft=draft, execution=evidence)
except Exception as exc:
    proof.update(result='failed', error_type=type(exc).__name__, error=str(exc))
proof.update(total_ms=round((time.monotonic() - start) * 1000), snapshots=snapshots)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: proof[k] for k in ('result', 'total_ms')}), flush=True)
sys.exit(0 if proof['result'] == 'passed' else 1)

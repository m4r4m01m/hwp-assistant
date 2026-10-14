"""Opt-in, single synthetic report using the existing Codex ChatGPT login.

No HWP document, database, approval, save or web search is involved.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.codex_runner import capture_settings
from app.report_runner import generate_report

parser = argparse.ArgumentParser()
parser.add_argument('--actual-codex', action='store_true')
parser.add_argument('--out', required=True)
args = parser.parse_args()
if not args.actual_codex: parser.error('An explicit --actual-codex opt-in is required')
target = Path(args.out)
if target.exists(): parser.error('Preserve earlier evidence; choose a new output path')
started = time.monotonic()
snapshots = []
def progress(value):
    record = {'elapsed_ms':round((time.monotonic()-started)*1000), 'stage':value['stage'],
              'paragraphs':len(value['blocks']), 'characters':sum(len(p['text']) for p in value['blocks'])}
    snapshots.append(record)
    if value['blocks'] and not any(s['paragraphs'] for s in snapshots[:-1]):
        print(json.dumps({'first_visible':record}), flush=True)
proof = {'actual_codex_calls':1, 'real_engine':False, 'human_approval':False, 'document_saved':False,
         'topic':'synthetic security awareness training plan', 'research':False}
try:
    draft, evidence = generate_report({'id':str(uuid.uuid4()), 'research':False,
        'prompt':'가상의 금융회사 보안인식 교육 계획을 짧게 작성해줘. 모두 합성 시험용 제안이며 실제 사건, 통계, 법적 주장 없이 작성해줘. 3개 절마다 짧은 네모 문단 하나씩, 요약 한 문단, 확인 사항 하나로 구성해줘.'}, capture_settings(), progress)
    proof.update(result='passed', execution=evidence, draft=draft)
except Exception as exc:
    proof.update(result='failed', error_type=type(exc).__name__, error=str(exc))
finally:
    proof.update(snapshots=snapshots, total_ms=round((time.monotonic()-started)*1000))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:proof[k] for k in ('result','total_ms')}),flush=True)
sys.exit(0 if proof['result']=='passed' else 1)

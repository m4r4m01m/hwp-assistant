"""Actual stdio child + controlled barriers, synthetic model events only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Event, Thread
from types import SimpleNamespace
import pytest
from app import report_stream, reports
from app.codex_runner import CodexSettings
from app.report_partial import partial_json, preview_blocks
from app.report_contract import ReportDraft
from app.report_rpc import ReportRpc, ReportStopped
from test_reports import DRAFT, report_env, create, ready
from test_collaboration import secured, as_user


def test_every_prefix_is_display_only_and_handles_split_unicode():
    draft = {**DRAFT, 'title': '한글 "문장" \\ 😀', 'summary': '□ (요약) 문장입니다.'}
    for ascii in (True, False):
        raw = json.dumps(draft, ensure_ascii=ascii)
        for i in range(len(raw) + 1):
            blocks = preview_blocks(raw[:i])
            assert all('text' in b and isinstance(b['text'], str) for b in blocks)
            json.dumps(blocks, ensure_ascii=False).encode('utf-8')
        assert partial_json(raw) == draft
        assert preview_blocks(raw)[0]['text'] == draft['title']
    assert preview_blocks('{"title":"<script>alert(1)</script>"}')[0]['text'].startswith('<script>')  # React escapes; no HTML parsing.
    assert preview_blocks('{"title": 42') == []
    assert preview_blocks('{"title":"unfinished\\uD8') == [{'text':'unfinished','kind':'title'}]


@pytest.mark.parametrize('mode', ['complete', 'failed', 'invalid', 'disconnect', 'tool', 'cancel', 'timeout'])
def test_rpc_barrier_stream_precedes_completion_and_cleanup(tmp_path, monkeypatch, mode):
    (tmp_path / 'draft.json').write_text(json.dumps(DRAFT), encoding='utf-8')
    args = [sys.executable, str(Path(__file__).with_name('report_rpc_fixture.py')), str(tmp_path), mode]
    monkeypatch.setattr(report_stream, 'stream_command', lambda *args_: args)
    settings = CodexSettings(sys.executable, 'test-model', '', tuple(os.environ.items()), timeout=1 if mode == 'timeout' else 10)
    snapshots, outcome = [], []
    partial, cancelled = Event(), Event()
    def progress(value):
        snapshots.append(copy.deepcopy(value))
        if value['blocks']: partial.set()
    def run():
        try: outcome.append(report_stream.generate_stream({'research':True, 'prompt':'합성 보고서'}, settings, str(tmp_path), 'report', 'test', progress, lambda: not cancelled.is_set()))
        except Exception as exc: outcome.append(exc)
    thread = Thread(target=run); thread.start()
    try:
        if mode != 'tool':
            assert partial.wait(5)
            assert not outcome  # Actual received title exists while completion is gated.
            assert snapshots[-1]['blocks'][0]['text'] == DRAFT['title']
        if mode == 'cancel': cancelled.set()
        elif mode != 'timeout': (tmp_path / 'release').touch()
        thread.join(5)
        assert not thread.is_alive()
        if mode == 'complete':
            result, evidence = outcome[0]
            assert result == ReportDraft.model_validate(DRAFT).model_dump() and evidence['completed']
            assert evidence['web_search_calls'] == 1
            assert evidence['timings']['first_text_ms'] <= evidence['timings']['generation_ms']
            assert all('WRONG' not in str(v) for v in snapshots)
        else:
            assert isinstance(outcome[0], ReportStopped if mode == 'cancel' else TimeoutError if mode == 'timeout' else Exception)
    finally:
        cancelled.set(); (tmp_path / 'release').touch(); thread.join(12)


def test_partial_snapshot_owner_refresh_retry_cancel_fences(report_env, monkeypatch):
    (client, app, _, _), _ = report_env
    entered, release, exited = Event(), Event(), Event()
    calls = []
    def generate(value, settings, progress, active):
        calls.append(value)
        try:
            progress({'stage':'writing','blocks':[{'kind':'title','text':'생성 중 실제 수신 내용'}], 'timings':{}, 'activity':'','research_calls':0})
            entered.set(); assert release.wait(5)
            progress({'stage':'writing','blocks':[{'kind':'title','text':'늦은 내용'}]})
            return DRAFT, {}
        finally: exited.set()
    monkeypatch.setattr(reports, 'generate_report', generate)
    try:
        body = create(client); assert entered.wait(2)
        url = '/api/report-tasks/' + body['id']
        partial = client.get(url).json()
        assert partial['status'] == 'running' and partial['draft'] is None
        assert partial['execution']['progress']['blocks'][0]['text'] == '생성 중 실제 수신 내용'
        assert client.get(url).json() == partial
        assert client.post('/api/report-tasks',json=body).json() == partial and len(calls) == 1
        assert client.get('/api/documents').json() == []
        assert 'progress' not in client.get('/api/report-tasks').json()[0]['execution']
        as_user(client, 'bob'); assert client.get(url).status_code == 404
        as_user(client, 'alice')
        cancelled = client.post(url + '/cancel').json()
        release.set(); assert exited.wait(2)
        assert client.get(url).json() == cancelled
        assert client.get('/api/documents').json() == []
    finally: release.set(); exited.wait(5)


def test_throttled_rich_delta_flushes_while_child_waits_for_completion(tmp_path, monkeypatch):
    """A last delta inside the throttle window must reach the UI during silence."""
    from report_rich_fixture import RICH_DRAFT
    (tmp_path / 'draft.json').write_text(json.dumps(RICH_DRAFT), encoding='utf-8')
    args = [sys.executable, str(Path(__file__).with_name('report_rpc_fixture.py')), str(tmp_path), 'e2e']
    monkeypatch.setattr(report_stream, 'stream_command', lambda *args_: args)
    second_parsed, suppressed, advance_clock, partial, rich, cancelled = (Event() for _ in range(6))
    original_preview = report_stream.preview_blocks
    def preview(raw):
        blocks = original_preview(raw)
        if any(block['kind'] == 'diagram' for block in blocks): second_parsed.set()
        return blocks
    def clock():
        # The second emit captures zero before signaling the test, so it is
        # deterministically throttled even when its worker immediately resumes.
        if second_parsed.is_set() and not suppressed.is_set():
            suppressed.set()
            return 0.0
        return .21 if advance_clock.is_set() else 0.0
    monkeypatch.setattr(report_stream, 'preview_blocks', preview)
    monkeypatch.setattr(report_stream, 'time', SimpleNamespace(monotonic=clock))
    snapshots, outcome = [], []
    def progress(value):
        snapshots.append(copy.deepcopy(value))
        if value['blocks']: partial.set()
        if any(block['kind'] == 'diagram' for block in value['blocks']): rich.set()
    def run():
        try:
            outcome.append(report_stream.generate_stream({'research': True, 'prompt': '합성 rich stream'},
                CodexSettings(sys.executable, 'test-model', '', tuple(os.environ.items()), timeout=10),
                str(tmp_path), 'report', 'test', progress, lambda: not cancelled.is_set()))
        except Exception as exc: outcome.append(exc)
    thread = Thread(target=run); thread.start()
    try:
        assert partial.wait(5)
        (tmp_path / 'release').touch()
        assert suppressed.wait(5)
        assert not rich.is_set() and not outcome
        advance_clock.set()
        assert rich.wait(2), 'Received rich content stayed hidden while the stream was idle'
        assert not outcome and not (tmp_path / 'finish').exists()
        assert {'table', 'diagram'} <= {block['kind'] for block in snapshots[-1]['blocks']}
        (tmp_path / 'finish').touch(); thread.join(5)
        assert not thread.is_alive() and isinstance(outcome[0], tuple)
    finally:
        cancelled.set(); (tmp_path / 'release').touch(); (tmp_path / 'finish').touch(); thread.join(12)


def test_rpc_timeout_reaps_owned_child(tmp_path):
    marker = tmp_path / 'child-survived'
    child = f'import time,pathlib; time.sleep(1.5); pathlib.Path({str(marker)!r}).touch()'
    parent = f'import sys,subprocess,time; subprocess.Popen([sys.executable,"-c",{child!r}]); print("{{}}",flush=True); time.sleep(8)'
    with pytest.raises(TimeoutError):
        with ReportRpc([sys.executable,'-c',parent],environment=dict(os.environ),cwd=str(tmp_path),timeout=.5) as rpc:
            assert rpc.receive() == {}
            rpc.receive()
    time.sleep(1.7)
    assert not marker.exists()


def test_rpc_stalled_stdin_is_bounded(tmp_path):
    with pytest.raises(TimeoutError):
        with ReportRpc([sys.executable, '-c', 'import time; time.sleep(30)'], environment=dict(os.environ), cwd=str(tmp_path), timeout=.5) as rpc:
            rpc.send({'input':'x' * 100000})
    assert all(not thread.is_alive() for thread in rpc.readers)

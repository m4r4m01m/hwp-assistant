// Continuation of the SAME headed Playwright session. No launching, AI, approval clicks or approval writes.
import { expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import { isDeepStrictEqual } from 'node:util';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';

export function sameSelectionFence(live, expected, currentVersion, baseVersion) {
  return live.revision === expected.revision && live.state.documentSha256 === expected.state.documentSha256
    && live.state.documentEpoch === expected.state.documentEpoch && live.state.changeSeq === expected.state.changeSeq
    && live.evidence.textSha256 === expected.evidence.textSha256
    && live.evidence.formatSha256 === expected.evidence.formatSha256
    && live.evidence.adjacentContextSha256 === expected.evidence.adjacentContextSha256
    && isDeepStrictEqual(live.selection.target, expected.selection.target) && currentVersion === baseVersion;
}

export async function continueHumanVerification(h) {
  const { proof, proofPath, page, context, record, announce, get, snapshot, exported, inspect,
    preserved, focusEnd, out, hash, sleep, closed, isTarget } = h;
  const task = proof.task;
  const docPath = `/api/documents/${proof.document.id}`;
  const originalUrl = `${docPath}/versions/${task.base_version}/file`;
  const article = page.locator(`[data-task-id="${task.id}"]`);
async function untilSaved(task) {
  await announce('waiting_for_user_approval', '실제 후보를 비교한 뒤 원하는 후보의 승인·적용·저장 버튼을 직접 눌러 주세요. 창과 터미널을 유지하세요.');
  console.log(`작업 ${task.id}\n문서 ${proof.document.id}\n증거 ${proofPath}`);
  let connectionError = '';
  let approvalObservationStarted = 0;
  while (!closed()) {
    try {
      const rows = await get(`/api/documents/${proof.document.id}/applications`);
      const application = rows.find(a => a.task_id === task.id);
      if (application) {
        proof.application = application;
        const click = proof.ui_clicks.find(e => e.task_id === task.id && e.candidate_id === application.candidate_id && e.trusted);
        const approval = proof.network.find(e => e.kind === 'approval' && e.status === 200 && e.response.id === application.id);
        if (!click || !approval) {
          approvalObservationStarted ||= Date.now();
          if (Date.now() - approvalObservationStarted > 10000)
            throw Error('서버 승인과 이 창의 실제 후보 버튼 클릭을 연결할 수 없습니다. 자동으로 승인하지 않습니다.');
          await sleep(200); continue; // response/body observers may finish after the API poll
        }
        proof.stages.approval = 'passed';
        proof.approved_candidate_id = application.candidate_id;
        if (application.saved_version && application.status === 'saved') return application;
        const errors = await page.getByRole('alert').allTextContents();
        if (errors.length) throw Error(`제품 적용/저장 오류: ${errors.join(' | ')}`);
      } else {
        if (proof.ui_clicks.some(e => e.task_id === task.id)) {
          const errors = await page.getByRole('alert').allTextContents();
          if (errors.length) throw Error(`제품 승인 오류: ${errors.join(' | ')}`);
          await sleep(200); continue;
        }
        const live = await snapshot();
        const current = await get(`/api/documents/${proof.document.id}`);
        if (proof.ui_clicks.some(e => e.task_id === task.id)) continue;
        if (!sameSelectionFence(live, task.snapshot, current.current_version, task.base_version))
          throw Error('승인 전 편집 상태/선택 대상/기준 버전이 변경되었습니다. 강제 적용하지 않고 창과 후보를 보존합니다.');
        proof.live_buffer_check = { at: new Date().toISOString(), revision: live.revision, document_sha256: live.state.documentSha256,
          text_sha256: live.evidence.textSha256, base_version: current.current_version };
      }
      await record(); connectionError = '';
    } catch (error) {
      if (!String(error).includes('ECONNREFUSED') && !String(error).includes('ETIMEDOUT')) throw error;
      if (connectionError !== String(error)) console.error('연결 대기: 브라우저와 버퍼를 유지합니다.');
      connectionError = String(error);
    }
    await sleep(1500);
  }
  throw Error('사용자가 브라우저를 닫았습니다. 창 닫기는 승인이나 저장 성공이 아닙니다.');
}
  const application = await untilSaved(task);
  await expect(page.getByRole('status')).toHaveText('저장 완료', { timeout: 30000 });
  await expect(article.getByTestId('application-history')).toContainText(application.saved_version);
  await announce('verifying_saved_version', '서버 저장 성공을 확인했습니다. 새 버전과 원본 보존을 검사한 뒤 같은 창에서 다시 엽니다.');
  const candidate = task.candidates.find(c => c.id === application.candidate_id);
  expect(candidate).toBeTruthy();
  const receiptEvent = proof.network.find(e => e.kind === 'receipt' && e.status === 200 && e.application_id === application.id);
  const savedEvent = proof.network.find(e => e.kind === 'save' && e.status === 200 && e.response.id === application.saved_version);
  expect(receiptEvent).toBeTruthy(); expect(savedEvent).toBeTruthy();
  const receipt = receiptEvent.requested.engine_receipt;
  expect(receipt.commandId).toBe(application.id);
  expect(receipt.target).toEqual(task.snapshot.selection.target);
  expect(receipt.beforeTextSha256).toBe(task.snapshot.evidence.textSha256);
  expect(receipt.afterTextSha256).toBe(hash(candidate.replacement));
  expect(receipt.beforeChangeSeq).toBe(task.snapshot.state.changeSeq);
  expect(receipt.beforeDocumentSha256).toBe(task.snapshot.state.documentSha256);
  expect(application.base_version).toBe(task.base_version);
  proof.receipt = receipt; proof.stages.apply = 'passed';
  proof.after_apply_snapshot = await snapshot();
  expect(proof.after_apply_snapshot.evidence.text).toBe(candidate.replacement);
  const savedUrl = await page.getByRole('link', { name: '저장본 다운로드' }).getAttribute('href');
  expect(savedUrl).toContain(application.saved_version);
  const downloaded = await context.request.get(savedUrl); expect(downloaded.status()).toBe(200);
  const savedBytes = await downloaded.body();
  proof.saved_sha256 = hash(savedBytes);
  expect(proof.saved_sha256).toBe(receiptEvent.requested.export_sha256);
  expect(proof.saved_sha256).toBe(savedEvent.response.sha256);
  proof.saved_manifest = await inspect(savedBytes);
  preserved(proof.unsaved_manifest, proof.saved_manifest, receipt.target);
  expect(proof.saved_manifest.paragraphs.find(p => isTarget(p, receipt.target)).text).toBe(candidate.replacement);
  expect(proof.saved_manifest.paragraphs.some(p => p.text === task.candidates.find(c => c.id !== candidate.id).replacement)).toBe(false);
  const currentExport = await exported(); preserved(proof.unsaved_manifest, await inspect(currentExport), receipt.target);
  const versions = await get(docPath + '/versions'); expect(versions).toHaveLength(2);
  expect(versions.filter(v => v.id !== task.base_version).map(v => v.id)).toEqual([application.saved_version]);
  expect(hash(await (await context.request.get(originalUrl)).body())).toBe(proof.original_sha256);
  proof.versions = versions; proof.stages.save = 'passed';
  proof.checks.after_save = { non_target_paragraphs_and_resources_preserved: true, other_unsaved_edit_saved: true,
    original_unchanged: true, added_version_count: 1, unselected_candidate_not_applied: true, download_status: downloaded.status() };
  await writeFile(path.join(out, 'saved-synthetic.hwpx'), savedBytes); await record();
  // Reopen only after explicit product success, receipt, download, version and clean-buffer checks.
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  await page.getByRole('button', { name: '← 문서 목록', exact: true }).click();
  await page.getByRole('row').filter({ has: page.locator(`a[href="${savedUrl}"]`) }).locator('td').first().getByRole('button').click();
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  h.setFrame(page.frames().find(f => f.url().includes('/rhwp/')));
  await focusEnd(); proof.reopened_snapshot = await snapshot();
  expect(proof.reopened_snapshot.evidence.text).toBe(candidate.replacement);
  proof.reopened_manifest = await inspect(await exported());
  preserved(proof.unsaved_manifest, proof.reopened_manifest, receipt.target);
  expect((await get(docPath + '/versions')).length).toBe(2);
  proof.stages.reopen = 'passed'; proof.completed_at = new Date().toISOString();
  await announce('passed', '승인·적용·저장·웹 재열기 검증 통과. 한컴 왕복 검증은 아닙니다. 창은 직접 닫을 때까지 유지됩니다.');
  await page.screenshot({ path: path.join(out, 'reopened.png') });
  console.log(JSON.stringify({ task_id: task.id, candidate_id: candidate.id, application_id: application.id, previous_version: task.base_version, saved_version: application.saved_version, stages: proof.stages }, null, 2));
}

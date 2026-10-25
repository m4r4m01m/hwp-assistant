import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the actual downstream read-only lookup with synthetic cursor states.
// This protects target scope even when the public text-agent context is absent.
const bridge = readFileSync(new URL('../../scripts/assistant-bridge.ts.txt', import.meta.url), 'utf8');
const source = bridge.slice(bridge.indexOf('type AssistantStyleTarget ='), bridge.indexOf('// A read-only rendering receipt'));
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const body = { sectionIndex: 0, paragraphIndex: 3, charOffset: 2 };
const cell = { sectionIndex: 0, paragraphIndex: 0, charOffset: 2, parentParaIndex: 4, controlIndex: 2, cellIndex: 1, cellParaIndex: 0 };
function lookup({ position = body, selection = null, modes = {}, object = null, context, dimensions } = {}) {
  const inputHandler = {
    cursor: { isInHeaderFooter: () => !!modes.header, isInFootnote: () => !!modes.note },
    isFormMode: () => !!modes.form, isInField: () => !!modes.field, isInPictureObjectSelection: () => !!modes.picture,
    getCursorPosition: () => position, getSelection: () => selection,
    isInCellSelectionMode: () => !!modes.cells, hasMultiCellSelection: () => !!modes.multi, hasExcludedCellSelection: () => !!modes.excluded,
    isInTableObjectSelection: () => !!object, getSelectedTableRef: () => object,
    getCellTableContext: () => context ?? { sec: 0, ppi: 4, ci: 2 },
  };
  const wasm = { getSectionCount: () => 1, getParagraphCount: () => 10,
    getParagraphLength: () => 20, getCellParagraphCount: () => 2, getCellParagraphLength: () => 20,
    getTableDimensions: (_section, paragraph, control) => {
      assert.equal(paragraph, 4); assert.equal(control, 2);
      return dimensions ?? { rowCount: 2, colCount: 2, cellCount: 4 };
    } };
  const initial = JSON.stringify({ position, selection, object, context });
  const target = vm.runInNewContext(`${code}; assistantStyleTarget();`, { inputHandler, wasm, documentAgent: {} });
  assert.equal(JSON.stringify({ position, selection, object, context }), initial, 'lookup preserves live selection input');
  return JSON.parse(JSON.stringify(target));
}

test('body title target is the current paragraph including a partial same-paragraph selection', () => {
  const expected = { kind: 'paragraph', section: 0, paragraph: 3 };
  assert.deepEqual(lookup(), expected);
  assert.deepEqual(lookup({ selection: { start: { ...body, charOffset: 0 }, end: { ...body, charOffset: 5 } } }), expected);
});

test('cell and one-cell block select the entire exact native table control', () => {
  const expected = { kind: 'table', section: 0, paragraph: 4, control: 2 };
  assert.deepEqual(lookup({ position: cell }), expected);
  assert.deepEqual(lookup({ position: { ...cell, cellPath: [{ controlIndex: 2, cellIndex: 1, cellParaIndex: 0 }] }, modes: { cells: true } }), expected);
  assert.deepEqual(lookup({ object: { sec: 0, ppi: 4, ci: 2 } }), expected, 'object selection can differ from the text caret');
});

test('multiple paragraph, cell or table targets are refused', () => {
  for (const options of [
    { selection: { start: body, end: { ...body, paragraphIndex: 5 } } },
    { position: cell, selection: { start: cell, end: { ...cell, parentParaIndex: 5 } } },
    { position: cell, selection: { start: cell, end: { ...cell, cellIndex: 2 } } },
    { position: cell, selection: { start: cell, end: { ...cell, cellParaIndex: 1 } } },
    { position: cell, modes: { cells: true, multi: true } },
    { position: cell, modes: { cells: true, excluded: true } },
    { position: cell, modes: { cells: true }, context: { sec: 0, ppi: 5, ci: 2 } },
  ]) assert.throws(() => lookup(options));
});

test('headers, notes, forms, fields, shapes and nested tables never fall back to body coordinates', () => {
  for (const mode of ['header', 'note', 'form', 'field', 'picture']) assert.throws(() => lookup({ modes: { [mode]: true } }));
  assert.throws(() => lookup({ position: { ...cell, isTextBox: true } }));
  assert.throws(() => lookup({ position: { ...cell, cellPath: [{}, {}] } }));
  assert.throws(() => lookup({ object: { sec: 0, ppi: 4, ci: 2, cellPath: [{}, {}] } }));
});

test('stale or inconsistent positions are refused', () => {
  for (const position of [
    { ...body, paragraphIndex: -1 }, { ...body, paragraphIndex: 10 }, { ...body, sectionIndex: 1 },
    { ...body, charOffset: NaN }, { ...body, controlIndex: 2 },
    { ...body, charOffset: 21 }, { ...cell, cellParaIndex: 2 }, { ...cell, charOffset: 21 },
    { ...cell, controlIndex: undefined }, { ...cell, cellIndex: 4 },
    { ...cell, cellPath: [{ controlIndex: 1, cellIndex: 1, cellParaIndex: 0 }] },
  ]) assert.throws(() => lookup({ position }));
  assert.throws(() => lookup({ position: cell, dimensions: { rowCount: 0, colCount: 2, cellCount: 4 } }));
});

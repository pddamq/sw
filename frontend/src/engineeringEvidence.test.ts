// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from 'vitest';
import { createApp, h, nextTick, reactive } from 'vue';
import { renderToString } from 'vue/server-renderer';
import EngineeringEvidence from './EngineeringEvidence.vue';
const cleanups: (() => void)[] = [];
afterEach(() => cleanups.splice(0).forEach(fn => fn()));
const report = (disposition = 'checks_passed') => ({ disposition, production_released: false,
  created_at: '2026-11-06', repair_attempts: 1, plan_hash: 'abc', rules_hash: 'def',
  plan: { target_document: 'Fixture', process: 'concept', material: 'Steel', requirements: [] },
  rule_pack: { id: 'forma.concept', version: '1.0.0' },
  items: ['PASS', 'FAIL', 'UNKNOWN', 'NOT_APPLICABLE'].map(status => ({ id: status, description: 'Check ' + status,
    status, actual: 0, expected: 1, source: 'Fixture source', reason: 'Fixture evidence' })),
});
describe('engineering evidence', () => {
  it('shows confirmed project children and document-specific failures without mixing evidence', async () => {
    const child = { target_document: 'BasePlate', document_type: 'part', process: 'sheet_metal', material: 'Steel',
      requirements: [{ id: 'width', description: 'Plate width', expected: '20 mm', source: 'Project drawing',
        measurement: { name: 'D1@Sketch1', minimum: 19.9, maximum: 20.1, unit: 'mm' } }] };
    const value = { ...report('repair_required'), scope_hash: 'frozen-scope',
      scope_documents: [{ plan: child, rule_pack: { id: 'forma.sheet_metal', version: '1.1.0' } }],
      documents: [{ document: 'CaseAssembly', disposition: 'needs_review' }, { document: 'BasePlate', disposition: 'repair_required' }],
      items: [{ ...report().items[1], document: 'BasePlate', description: 'Plate width' }],
    };
    const html = await renderToString(createApp({ render: () => h(EngineeringEvidence, { report: value }) }));
    expect(html).toContain('项目文档范围');
    expect(html).toContain('frozen-scope');
    expect(html).toContain('BasePlate · part');
    expect(html).toContain('CaseAssembly：待人工核验');
    expect(html).toContain('BasePlate：发现缺陷，待修复');
    expect(html).toContain('D1@Sketch1 · [19.9, 20.1] mm');
    expect(html).toContain('Project drawing');
  });
  it('shows the exact named dimension, unit and confirmed bounds', async () => {
    const value = { ...report(), plan: { ...report().plan, requirements: [{ id: 'width',
      description: 'Width', expected: 'Confirmed range', source: 'Fixture source', checker: 'dimension_range',
      measurement: { name: 'D1@Sketch1', unit: 'mm', minimum: 19.9, maximum: 20.1 },
    }] } };
    const html = await renderToString(createApp({ render: () => h(EngineeringEvidence, { report: value }) }));
    expect(html).toContain('模型尺寸值');
    expect(html).toContain('D1@Sketch1');
    expect(html).toContain('[19.9, 20.1] mm');
    expect(html).toContain('闭区间');
  });
  it('renders no panel for legacy reports', async () => {
    expect(await renderToString(createApp({ render: () => h(EngineeringEvidence) }))).not.toContain('工程校验报告');
  });
  it.each(['checks_passed', 'repair_required', 'needs_review', 'stale', 'interrupted'])('keeps release limitation for %s', async disposition => {
    const html = await renderToString(createApp({ render: () => h(EngineeringEvidence, { report: report(disposition) }) }));
    expect(html).toContain('尚未生产放行');
    expect(html).toContain('非制造标准');
    expect(html).toContain('Fixture source');
    expect(html).toContain('forma.concept');
    for (const status of ['PASS', 'FAIL', 'UNKNOWN', 'NOT_APPLICABLE']) expect(html).toContain(`data-status="${status}"`);
    expect(html).toMatch(/<pre[^>]*>0<\/pre>/);
  });
  it('escapes model text and treats unknown statuses conservatively', async () => {
    const value = { ...report(), items: [{ id: 'x', description: '<img src=x onerror=alert(1)>', status: 'invented' }] };
    const html = await renderToString(createApp({ render: () => h(EngineeringEvidence, { report: value }) }));
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('<img');
    expect(html).toContain('data-status="UNKNOWN"');
    expect(html).toContain('尚无证据');
  });
  it('replaces old PASS evidence when a new stale report arrives', async () => {
    const props = reactive({ report: report() });
    const host = document.createElement('div'); document.body.append(host);
    const app = createApp({ render: () => h(EngineeringEvidence, props) }); app.mount(host);
    cleanups.push(() => { app.unmount(); host.remove(); });
    expect(host.textContent).toContain('已实现检查通过');
    props.report = { ...report('stale'), items: [] };
    await nextTick();
    expect(host.textContent).toContain('报告已失效');
    expect(host.querySelector('[data-status="PASS"]')).toBeNull();
    expect(host.textContent).toContain('尚无逐项检查证据');
  });
});

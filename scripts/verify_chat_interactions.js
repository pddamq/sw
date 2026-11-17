async (page) => {
  // All API calls are synthetic in this browser; no model or CAD request is made.
  const browserErrors = [];
  page.on('pageerror', error => browserErrors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') browserErrors.push(message.text()); });
  await page.addInitScript(() => {
    localStorage.setItem('forma-theme', 'dark');
    const stamp = '2026-11-04T08:00:00Z';
    const session = { id: 'fixture', title: 'Chat interaction fixture', updated_at: stamp };
    let messages = [
      { id: 'old-user', role: 'user', content: 'History fixture', created_at: stamp },
      { id: 'old-ai', role: 'assistant', content: Array.from({ length: 80 }, (_, i) => `History line ${i}.`).join('\n\n'), created_at: stamp },
    ];
    let tasks = [], events = [], current = null, output = '', streamController;
    let currentRound = 1;
    const roundText = {};
    const json = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
    const realFetch = window.fetch.bind(window);
    window.__emitFixture = event => {
      currentRound = event.round || currentRound;
      const e = { task_id: 'run-fixture', round: currentRound, ...event };
      if (e.type === 'token') {
        output += e.content;
        roundText[currentRound] = (roundText[currentRound] || '') + e.content;
      } else {
        if (e.type === 'answer') e.content = roundText[currentRound] || '';
        events.push({ event: e, created_at: stamp });
      }
      if (e.type === 'done') {
        current.status = 'completed';
        e.task = { ...current };
        messages.push({ id: 'new-ai', role: 'assistant', content: output, created_at: stamp });
      }
      streamController.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`));
      if (e.type === 'done') streamController.close();
    };
    window.fetch = async (input, init = {}) => {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      const path = url.pathname;
      if (!path.startsWith('/api/')) return realFetch(input, init);
      if (path === '/api/health') return json({ model_configured: true, task_running: current?.status === 'running', mcp: { running: false, tools: 0 }, solidworks: { connected: false }, active_task: current });
      if (path === '/api/model/profiles') return json({ profiles: [{ id: 'service', name: 'Fixture', models: ['fixture-model'] }], selected_id: 'service', selected_model: 'fixture-model' });
      if (path === '/api/sessions') return json([session]);
      if (path.endsWith('/messages')) return json(messages);
      if (path.endsWith('/tasks')) return json(tasks);
      if (path.endsWith('/attachments') || path === '/api/tools') return json([]);
      if (path.endsWith('/events')) return json(events);
      if (path.endsWith('/report')) return json({ task: current, report: { evidence: {}, operation_count: 0, returned_count: 0, error_count: 0, uncertain_count: 0 }, checkpoints: [] });
      if (path === '/api/chat') {
        window.__payloadFixture = JSON.parse(init.body);
        current = { id: 'run-fixture', session_id: 'fixture', status: 'running', created_at: stamp, message: window.__payloadFixture.message };
        tasks = [current];
        messages.push({ id: 'new-user', role: 'user', content: current.message, created_at: stamp });
        return new Response(new ReadableStream({ start(controller) {
          streamController = controller;
          window.__emitFixture({ type: 'task', task: current });
        } }), { headers: { 'Content-Type': 'text/event-stream' } });
      }
      return json({});
    };
  });
  await page.goto('http://127.0.0.1:52881/');
  await page.getByLabel('设计需求', { exact: true }).waitFor();
  await page.waitForFunction(() => {
    const feed = document.querySelector('.chat-feed');
    return document.querySelectorAll('.message').length === 2 && feed.scrollHeight > feed.clientHeight && feed.scrollHeight - feed.scrollTop - feed.clientHeight < 3;
  });
  if (await page.locator('.inspector').count()) throw new Error('Execution panel must start collapsed');
  await page.getByRole('button', { name: '显示执行面板', exact: true }).click();
  await page.locator('.inspector').waitFor();
  await page.getByRole('button', { name: '收起执行面板', exact: true }).click();
  await page.locator('.inspector').waitFor({ state: 'hidden' });
  const snapshot = await page.locator('body').ariaSnapshot();
  if (!snapshot.includes('思考强度')) throw new Error('Effort selector missing');
  await page.getByLabel('思考强度', { exact: true }).selectOption('high');
  await page.getByLabel('设计需求', { exact: true }).fill('Synthetic interaction verification');
  await page.getByTitle('发送任务', { exact: true }).click();
  await page.waitForFunction(() => window.__payloadFixture?.reasoning_effort === 'high');
  const emit = event => page.evaluate(e => window.__emitFixture(e), event);
  const usage = { system: 8700, tools: 15600, messages: 85500, total: 109800, source: 'estimated', images: 2, model: 'fixture-model', round: 1, limit: null };
  await emit({ type: 'context_usage', usage });
  await page.getByRole('button', { name: '查看上下文用量', exact: true }).click();
  const usagePanel = page.getByRole('region', { name: '上下文用量明细' });
  await usagePanel.waitFor();
  for (const text of ['系统提示词', '工具定义', '对话消息', '~8.7K', '~15.6K', '~85.5K', '上限未知', '2 张图片']) {
    if (!(await usagePanel.innerText()).includes(text)) throw new Error(`Missing usage detail: ${text}`);
  }
  if (await page.locator('.composer > .composer-toolbar .chat-model-picker').count() !== 1) throw new Error('Model selector must be inside composer toolbar');
  await page.screenshot({ path: 'output/playwright/composer-context-dark.png' });
  await page.keyboard.press('Escape');
  await usagePanel.waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: '查看上下文用量', exact: true }).click();
  await page.locator('.composer-caption').click();
  await usagePanel.waitFor({ state: 'hidden' });
  await emit({ type: 'reasoning_start', round: 1 });
  await emit({ type: 'reasoning_delta', round: 1, content: 'Synthetic reasoning text.' });
  await page.waitForFunction(() => document.querySelector('.reasoning')?.open === true);
  await emit({ type: 'reasoning_end', round: 1, content: 'Synthetic reasoning text.', status: 'completed' });
  await emit({ type: 'token', content: Array.from({ length: 40 }, (_, i) => `Answer line ${i}.`).join('\n\n') });
  await page.waitForFunction(() => document.querySelector('.reasoning')?.open === false);
  const feed = page.locator('.chat-feed');
  await page.waitForFunction(() => { const f = document.querySelector('.chat-feed'); return f.scrollHeight - f.scrollTop - f.clientHeight < 3; });
  const markers = page.getByRole('navigation', { name: '用户消息导航' }).getByRole('button');
  if (await markers.count() !== 2) throw new Error('Expected exactly one marker per user message');
  await markers.first().click();
  await page.waitForFunction(() => document.querySelector('.prompt-rail button:first-child')?.getAttribute('aria-current') === 'location');
  await page.screenshot({ path: 'output/playwright/chat-navigation-dark.png' });
  await page.getByRole('button', { name: '返回最底部' }).click();
  await page.waitForFunction(() => { const f = document.querySelector('.chat-feed'); return f.scrollHeight - f.scrollTop - f.clientHeight < 3 && !document.querySelector('.jump-bottom'); });
  await markers.nth(1).click();
  await page.waitForFunction(() => Math.abs(document.querySelectorAll('.message.user')[1].getBoundingClientRect().top - document.querySelector('.chat-feed').getBoundingClientRect().top - 12) < 2);
  await page.getByRole('button', { name: '返回最底部' }).click();
  const box = await feed.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -600);
  await page.waitForFunction(() => { const f = document.querySelector('.chat-feed'); return f.scrollHeight - f.scrollTop - f.clientHeight > 300; });
  const before = await feed.evaluate(f => f.scrollTop);
  await emit({ type: 'token', content: '\n\nMore answer text.\n'.repeat(20) });
  await page.waitForFunction(() => document.querySelector('.message.assistant:last-child')?.textContent.includes('More answer text.'));
  const after = await feed.evaluate(f => f.scrollTop);
  if (Math.abs(before - after) > 2) throw new Error(`Reading position moved: ${before} -> ${after}`);
  await page.mouse.wheel(0, 100000);
  await page.waitForFunction(() => { const f = document.querySelector('.chat-feed'); return f.scrollHeight - f.scrollTop - f.clientHeight < 3; });
  await emit({ type: 'token', content: '\n\nFollowing resumed.\n'.repeat(10) });
  await page.waitForFunction(() => { const f = document.querySelector('.chat-feed'); return f.scrollHeight - f.scrollTop - f.clientHeight < 3; });
  const refs = Array.from({ length: 5 }, (_, chunk_id) => ({ name: 'fixture.txt', document_id: 'document-fixture', chunk_id, text: `Source excerpt ${chunk_id}` }));
  await emit({ type: 'sources', sources: refs });
  await emit({ type: 'sources', sources: [refs[0]] });
  await page.waitForFunction(() => document.querySelectorAll('.sources > details').length === 1 && document.querySelectorAll('.sources > details p').length === 5);
  await emit({ type: 'tool_start', name: 'sw_status', purpose: 'Synthetic status check' });
  await page.waitForFunction(() => document.querySelector('.activity-chain')?.open === true);
  const chain = page.locator('.activity-chain');
  await chain.locator(':scope > summary').click();
  await emit({ type: 'tool_result', name: 'sw_status' });
  await page.waitForFunction(() => document.querySelector('.activity-chain')?.open === false);
  await chain.locator(':scope > summary').click();
  await page.waitForFunction(() => document.querySelector('.activity-chain')?.open === true);
  await emit({ type: 'answer', round: 1 });
  await emit({ type: 'reasoning_start', round: 2 });
  await emit({ type: 'reasoning_delta', round: 2, content: 'Second round reasoning.' });
  await page.waitForFunction(() => document.querySelector('.activity-chain')?.open === false);
  await emit({ type: 'reasoning_end', round: 2, content: 'Second round reasoning.', status: 'completed' });
  await emit({ type: 'token', round: 2, content: 'Second round answer.' });
  await emit({ type: 'answer', round: 2 });
  await emit({ type: 'done', status: 'completed' });
  await page.waitForFunction(() => document.querySelector('.activity-chain')?.open === false);
  await chain.locator(':scope > summary').click();
  await page.waitForFunction(() => document.querySelector('.activity-chain')?.open === true);
  await page.screenshot({ path: 'output/playwright/chat-interactions-dark.png' });
  await page.getByTitle('切换浅色').click();
  await page.screenshot({ path: 'output/playwright/chat-interactions-light.png' });
  await page.getByRole('button', { name: 'Chat interaction fixture', exact: true }).click();
  await page.waitForFunction(() => { const feed = document.querySelector('.chat-feed'); return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 3; });
  await page.waitForFunction(() => {
    const rounds = document.querySelectorAll('article.assistant:last-of-type .assistant-round');
    return rounds.length === 2 && rounds[0].querySelector('.prose')?.textContent.includes('Answer line 0') && rounds[1].querySelector('.prose')?.textContent.trim() === 'Second round answer.';
  });
  await page.screenshot({ path: 'output/playwright/chat-rounds-light.png' });
  await page.getByRole('button', { name: '查看上下文用量', exact: true }).click();
  await usagePanel.waitFor();
  if (!(await usagePanel.innerText()).includes('~109.8K')) throw new Error('Usage did not survive history reload');
  await page.screenshot({ path: 'output/playwright/composer-context-light.png' });
  await page.setViewportSize({ width: 760, height: 820 });
  await page.screenshot({ path: 'output/playwright/composer-context-narrow.png' });
  const bounds = await usagePanel.boundingBox();
  if (!bounds || bounds.x < 0 || bounds.x + bounds.width > 760) throw new Error('Usage popover overflows viewport');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await page.waitForFunction(() => document.querySelector('[aria-label="思考强度"]')?.value === 'high');
  if (browserErrors.length) throw new Error(browserErrors.join('\n'));
  console.log('PASS: composer layout, context details, dismissal/replay, narrow viewport, scroll navigation, round grouping, effort persistence, dark/light rendering');
}

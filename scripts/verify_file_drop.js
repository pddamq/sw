async (page) => {
  // Browser-local fixtures: no real model calls or changes to user conversations.
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.addInitScript(() => {
    const stamp = '2026-11-04T10:00:00Z';
    const session = { id: 'drop-fixture', title: 'Attachment fixture', updated_at: stamp };
    let pending = [], messages = [], tasks = [], events = [];
    window.__uploadCount = 0;
    const json = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
      if (!path.startsWith('/api/')) return realFetch(input, init);
      if (path === '/api/health') return json({ model_configured: true, vision_enabled: false, task_running: false, mcp: { running: true, tools: 0 }, solidworks: { connected: false } });
      if (path === '/api/model/profiles') return json({ profiles: [{ id: 'fixture', name: 'Fixture', models: ['fixture-model'] }], selected_id: 'fixture', selected_model: 'fixture-model' });
      if (path === '/api/sessions') return json([session]);
      if (path.endsWith('/attachments')) {
        if (init.method === 'POST') {
          window.__uploadCount++;
          const file = init.body.get('file');
          const item = { id: window.__uploadCount.toString(16).padStart(32, '0'), name: file.name, kind: 'document', size: file.size, width: 0, height: 0 };
          pending.push(item);
          return json(item);
        }
        return json(pending);
      }
      if (path.startsWith('/api/media/') && init.method === 'DELETE') {
        pending = pending.filter(item => !path.endsWith(item.id));
        return json({ deleted: true });
      }
      if (path.endsWith('/messages')) return json(messages);
      if (path.endsWith('/tasks')) return json(tasks);
      if (path.endsWith('/events')) return json(events);
      if (path.endsWith('/report')) return json({ task: tasks[0], report: { evidence: {}, operation_count: 0, returned_count: 0, error_count: 0, uncertain_count: 0 }, checkpoints: [] });
      if (path === '/api/chat') {
        const payload = JSON.parse(init.body);
        window.__chatPayload = payload;
        const task = { id: 'drop-task', session_id: session.id, status: 'completed', created_at: stamp, message: payload.message };
        tasks = [task];
        messages = [{ id: 'drop-user', role: 'user', content: payload.message, attachments: [...pending], task_id: task.id, created_at: stamp },
          { id: 'drop-assistant', role: 'assistant', content: 'Fixture file received.', task_id: task.id, created_at: stamp }];
        pending = [];
        const stream = [{ type: 'task', task: { ...task, status: 'running' } }, { type: 'token', content: 'Fixture file received.', round: 1 },
          { type: 'answer', content: 'Fixture file received.', round: 1 }, { type: 'done', task }].map(event => ({ ...event, task_id: task.id }));
        events = stream.filter(event => event.type !== 'token').map(event => ({ event, created_at: stamp }));
        return new Response(stream.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } });
      }
      return json([]);
    };
  });
  await page.goto('http://127.0.0.1:52881/');
  await page.getByLabel('设计需求', { exact: true }).waitFor();
  await page.waitForFunction(() => !document.querySelector('.chat-feed .loading'));
  await page.locator('body').ariaSnapshot();
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer();
    data.items.add(new File(['Diameter = 24 mm'], 'dimensions.txt', { type: 'text/plain' }));
    return data;
  });
  const composer = page.locator('.composer');
  await composer.dispatchEvent('dragenter', { dataTransfer: transfer });
  await composer.dispatchEvent('dragover', { dataTransfer: transfer });
  await page.getByText('松开即可添加附件', { exact: true }).waitFor();
  await page.screenshot({ path: 'output/playwright/file-drop-highlight.png' });
  await composer.dispatchEvent('drop', { dataTransfer: transfer });
  await page.locator('.pending-images .document-attachment').waitFor();
  if (await page.locator('.drop-overlay').count()) throw new Error('Drop overlay remained after drop');
  if (await page.getByRole('button', { name: '发送任务', exact: true }).isDisabled()) throw new Error('File-only message must be sendable');
  await page.getByRole('button', { name: '移除附件 dimensions.txt', exact: true }).click();
  await page.locator('.pending-images').waitFor({ state: 'hidden' });
  await composer.dispatchEvent('drop', { dataTransfer: transfer });
  await page.locator('.pending-images .document-attachment').waitFor();
  const invalid = await page.evaluateHandle(() => {
    const data = new DataTransfer(); data.items.add(new File(['binary'], 'unsupported.exe')); return data;
  });
  await composer.dispatchEvent('drop', { dataTransfer: invalid });
  await page.getByRole('alert').filter({ hasText: 'unsupported.exe' }).waitFor();
  if (await page.evaluate(() => window.__uploadCount) !== 2) throw new Error('Invalid file reached upload endpoint');
  await page.screenshot({ path: 'output/playwright/file-drop-attached.png' });
  await page.getByRole('button', { name: '发送任务', exact: true }).click();
  await page.waitForFunction(() => window.__chatPayload?.attachment_ids.length === 1);
  if (!await page.evaluate(() => window.__chatPayload.message.includes('附加的文件'))) throw new Error('File-only message omitted default prompt');
  await page.locator('.message.user .document-attachment').waitFor();
  await page.getByRole('button', { name: 'Attachment fixture', exact: true }).click();
  await page.locator('.message.user .document-attachment').waitFor();
  if (await page.locator('.pending-images').count()) throw new Error('Sent attachment remained pending');
  await transfer.dispose(); await invalid.dispose();
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: drag highlight, automatic attachment upload, remove, unsupported file rejection, file-only send without vision, history replay');
}

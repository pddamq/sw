async (page) => {
  // Synthetic provider endpoints only; never edits the user's saved configuration.
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.addInitScript(() => {
    localStorage.setItem('forma-theme', 'dark');
    const profile = { id: 'fixture', name: 'Fixture Provider', base_url: 'https://fixture.test/v1', full_endpoint: false,
      models: ['fixture-model'], has_api_key: true };
    let catalog = { profiles: [profile], selected_id: 'fixture', selected_model: 'fixture-model' };
    const json = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
      if (!path.startsWith('/api/')) return realFetch(input, init);
      if (path === '/api/health') return json({ model_configured: true, task_running: false, mcp: { running: true, tools: 0 }, solidworks: { connected: false } });
      if (path === '/api/model/profiles') {
        if (init.method === 'POST') {
          const saved = JSON.parse(init.body); window.__advancedSaved = saved;
          catalog = { ...catalog, profiles: [{ ...profile, ...saved, has_api_key: true }] };
        }
        return json(catalog);
      }
      if (path === '/api/settings') return json({ temperature: 0.2, mcp_args: [] });
      if (path === '/api/model/test') {
        window.__advancedTested = JSON.parse(init.body);
        return json({ connected: true, detail: 'Fixture connection passed', tokens_per_second: 24, first_token_ms: 10, elapsed_ms: 1000, output_tokens: 24, token_source: 'usage' });
      }
      if (path === '/api/model/models') { window.__advancedListed = JSON.parse(init.body); return json({ models: ['fixture-model'] }); }
      return json([]);
    };
  });
  await page.goto('http://127.0.0.1:52881/');
  await page.getByRole('button', { name: /工作台设置/ }).click();
  const panel = page.locator('.advanced-options');
  await panel.waitFor();
  if (await panel.getAttribute('open') !== null) throw new Error('Advanced settings must start collapsed');
  await panel.locator('summary').click();
  await page.getByRole('button', { name: '测试连接与速度', exact: true }).click();
  await page.waitForFunction(() => window.__advancedTested?.context_window === 200000 && window.__advancedTested?.max_output_tokens === 8192 && window.__advancedTested?.user_agent === '');
  await page.getByLabel('最大上下文窗口（tokens）', { exact: true }).fill('128000');
  await page.getByLabel('最大输出 token', { exact: true }).fill('4096');
  await page.getByLabel('自定义 User-Agent（UA）', { exact: true }).fill('FixtureBrowser/1.0');
  await page.getByRole('button', { name: '获取模型', exact: true }).click();
  await page.waitForFunction(() => window.__advancedListed?.user_agent === 'FixtureBrowser/1.0');
  await page.getByRole('button', { name: '测试连接与速度', exact: true }).click();
  await page.waitForFunction(() => window.__advancedTested?.max_output_tokens === 4096);
  await page.getByRole('button', { name: '保存此服务', exact: true }).click();
  await page.waitForFunction(() => window.__advancedSaved?.context_window === 128000 && window.__advancedSaved?.max_output_tokens === 4096 && window.__advancedSaved?.user_agent === 'FixtureBrowser/1.0');
  await page.getByLabel('最大上下文窗口（tokens）', { exact: true }).fill('');
  await page.getByLabel('最大输出 token', { exact: true }).fill('');
  await page.getByLabel('自定义 User-Agent（UA）', { exact: true }).fill('');
  await page.getByRole('button', { name: '保存此服务', exact: true }).click();
  await page.waitForFunction(() => window.__advancedSaved?.context_window === 200000 && window.__advancedSaved?.max_output_tokens === 8192 && window.__advancedSaved?.user_agent === '');
  await page.getByLabel('最大输出 token', { exact: true }).fill('200000');
  await page.getByRole('button', { name: '保存此服务', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '最大输出 token 应小于上下文窗口' }).waitFor();
  await page.getByLabel('最大输出 token', { exact: true }).fill('8192');
  await page.getByRole('button', { name: '保存此服务', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '模型服务已保存' }).waitFor();
  await page.screenshot({ path: 'output/playwright/model-advanced-dark.png' });
  await page.getByTitle('关闭设置', { exact: true }).click();
  await page.getByTitle('切换浅色', { exact: true }).click();
  await page.getByRole('button', { name: /工作台设置/ }).click();
  await page.locator('.advanced-options summary').click();
  if (await page.getByLabel('最大上下文窗口（tokens）', { exact: true }).inputValue() !== '200000') throw new Error('Saved context window did not restore');
  await page.screenshot({ path: 'output/playwright/model-advanced-light.png' });
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: advanced settings collapsed default, blank defaults, custom values, test/catalog/save payloads, persistence, validation, dark/light');
}

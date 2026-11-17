"""Protocol-aware endpoints; embedding calls continue to use their own API route."""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx


ROUTES = {'chat': '/chat/completions', 'responses': '/responses', 'claude': '/messages'}


def api_root(config) -> str:
    if not config.full_endpoint:
        return config.base_url.rstrip('/')
    url = urlsplit(config.base_url)
    path = url.path.rstrip('/')
    route = ROUTES[getattr(config, 'protocol', 'chat')]
    if path.endswith(route):
        path = path[:-len(route)]
    else:
        # A custom full route has no discoverable catalog URL; try its sibling.
        path = path.rsplit('/', 1)[0]
    return urlunsplit((url.scheme, url.netloc, path, '', '')).rstrip('/')


def protocol_root(config) -> str:
    root = api_root(config)
    if getattr(config, 'protocol', 'chat') == 'claude' and not config.full_endpoint:
        if not urlsplit(root).path.endswith('/v1'):
            root += '/v1'
    return root


def chat_endpoint(config) -> str:
    """Return the selected generation endpoint (legacy public helper name)."""
    return config.base_url if config.full_endpoint else protocol_root(config) + ROUTES[getattr(config, 'protocol', 'chat')]


def models_endpoint(config) -> str:
    return protocol_root(config) + '/models'


def auth_headers(config, key: str) -> dict:
    if getattr(config, 'protocol', 'chat') == 'claude':
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    else:
        headers = {'Authorization': 'Bearer ' + key}
    if config.user_agent:
        headers['User-Agent'] = config.user_agent
    return headers


def client_options(config, *, timeout: float | None = None) -> dict:
    """OpenAI SDK options for the unchanged Chat Completions adapter only."""
    timeout = timeout if timeout is not None else getattr(config, 'model_idle_timeout_seconds', 120)
    root = api_root(config)
    options = {'base_url': root, 'timeout': timeout, 'max_retries': 0}
    if config.user_agent:
        options['default_headers'] = {'User-Agent': config.user_agent}
    if config.full_endpoint:
        generated = httpx.URL(root + '/chat/completions')
        destination = httpx.URL(config.base_url)

        async def exact_chat_path(request: httpx.Request):
            if request.method == 'POST' and request.url == generated:
                request.url = destination

        options['http_client'] = httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, event_hooks={'request': [exact_chat_path]})
    return options

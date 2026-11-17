"""Local browser integration fixture. No external provider or CAD is contacted."""
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.post('/v1/chat/completions')
async def complete(request: Request):
    payload = await request.json()
    available = {t['function']['name'] for t in payload.get('tools', [])}
    last_role = payload['messages'][-1]['role']
    if last_role == 'user' and 'sw_status' in available:
        deltas = [{'tool_calls': [{'index': 0, 'id': 'fixture_status', 'type': 'function',
                   'function': {'name': 'sw_status', 'arguments': '{}'}}]}]
    elif 'fixture_image' in available and not any(m['role'] == 'tool' for m in payload['messages']):
        deltas = [{'tool_calls': [{'index': 0, 'id': 'fixture_image_call', 'type': 'function',
                   'function': {'name': 'fixture_image', 'arguments': '{}'}}]}]
    elif last_role == 'user' and 'connection_probe' in available:
        deltas = [{'tool_calls': [{'index': 0, 'id': 'fixture_probe', 'type': 'function',
                   'function': {'name': 'connection_probe', 'arguments': '{"value":"ok"}'}}]}]
    else:
        deltas = [{'content': text} for text in ['协议测试完成。', '此回复来自本地测试服务，', '没有操作真实 SOLIDWORKS，也没有创建 CAD 文件。']]
    async def stream():
        for delta in deltas:
            record = {'id': 'browser-protocol-fixture', 'object': 'chat.completion.chunk', 'created': 0,
                      'model': 'fixture-not-cad', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
            yield 'data: ' + json.dumps(record, ensure_ascii=False) + '\n\n'
        record['choices'] = [{'index': 0, 'delta': {},
                              'finish_reason': 'tool_calls' if 'tool_calls' in deltas[0] else 'stop'}]
        yield 'data: ' + json.dumps(record, ensure_ascii=False) + '\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(stream(), media_type='text/event-stream')

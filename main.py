import os
import time
import json
import uuid
from pathlib import Path
from typing import Any, List, Union

import httpx
from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn


CONFIG_DIR = Path(__file__).resolve().parent
load_dotenv(CONFIG_DIR / '.env', override=False)


def _read_json_config() -> dict[str, Any]:
    config_path = CONFIG_DIR / 'config.json'
    if not config_path.exists():
        return {}
    try:
        with config_path.open(encoding='utf-8') as config_file:
            config = json.load(config_file)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'config.json 格式错误: {exc}') from exc
    if not isinstance(config, dict):
        raise RuntimeError('config.json 必须是 JSON 对象')
    return config


_DOTENV_CONFIG = {
    key: value for key, value in dotenv_values(CONFIG_DIR / '.env').items()
    if value is not None and value.strip()
}
_FILE_CONFIG = _read_json_config()


def _setting(env_key: str, config_key: str, default: Any = None) -> Any:
    """Resolve settings as process env > .env > config.json > default."""
    for source in (os.environ, _DOTENV_CONFIG, _FILE_CONFIG):
        value = source.get(env_key if source is not _FILE_CONFIG else config_key)
        if value is not None and str(value).strip():
            return value
    return default


def _port_setting() -> int:
    raw_port = _setting('PORT', 'port', 8088)
    try:
        port = int(str(raw_port).strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'PORT 必须是 1-65535 的整数，当前值: {raw_port!r}') from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f'PORT 必须是 1-65535 的整数，当前值: {port}')
    return port


BASE_URL = str(_setting('FANZHA_BASE_URL', 'base_url', 'https://xzfzznt.gaj.sh.gov.cn')).rstrip('/')
CREATE_SESSION_URL = f'{BASE_URL}/api/ai/create_session'
CHAT_STREAM_URL = f'{BASE_URL}/api/ai/chat?type=0'
REFRESH_TOKEN_URL = f'{BASE_URL}/api/v1/user/token/refresh'

DEFAULT_ACCESS_TOKEN = str(_setting('FANZHA_ACCESS_TOKEN', 'access_token', ''))
DEFAULT_REFRESH_TOKEN = str(_setting('FANZHA_REFRESH_TOKEN', 'refresh_token', ''))
DEFAULT_MODEL = str(_setting('DEFAULT_MODEL', 'default_model', '国家反诈AI'))

app = FastAPI(title='国家反诈AI - OpenAI兼容反向代理服务', version='1.1.0')

class TokenManager:
    def __init__(self, access_token: str, refresh_token: str):
        self.access_token = access_token
        self.refresh_token = refresh_token

    async def refresh_if_needed(self, client: httpx.AsyncClient) -> bool:
        if not self.refresh_token:
            return False
        try:
            resp = await client.post(
                REFRESH_TOKEN_URL,
                headers={'Authorization': f'Bearer {self.refresh_token}', 'Content-Type': 'application/json'},
                json={},
                timeout=10.0
            )
            data = resp.json()
            if data.get('code') == 200 and 'data' in data:
                new_at = data['data'].get('access_token')
                new_rt = data['data'].get('refresh_token')
                if new_at:
                    self.access_token = new_at
                if new_rt:
                    self.refresh_token = new_rt
                return True
        except Exception as e:
            print(f'[TokenManager] 令牌刷新异常: {e}')
        return False

token_mgr = TokenManager(DEFAULT_ACCESS_TOKEN, DEFAULT_REFRESH_TOKEN)

def extract_token_from_header(auth_header: str) -> str:
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header[7:].strip()
        if token and token != 'sk-no-key-needed' and not token.startswith('sk-'):
            return token
    return ''

def extract_text_content(content: Any) -> str:
    """提取各种复杂 OpenAI message content（字符串、列表、包含多模态字典等）"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if 'text' in item and isinstance(item['text'], str):
                    parts.append(item['text'])
                elif 'content' in item and isinstance(item['content'], str):
                    parts.append(item['content'])
        return "\n".join(parts)
    return str(content or '')

async def create_upstream_session(client: httpx.AsyncClient, current_access_token: str) -> str:
    headers = {
        'Authorization': f'Bearer {current_access_token}',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 15; V2425A)'
    }
    try:
        resp = await client.post(CREATE_SESSION_URL, headers=headers, json={}, timeout=10.0)
        if (resp.status_code == 401 or resp.json().get('code') == 401) and current_access_token == token_mgr.access_token:
            if await token_mgr.refresh_if_needed(client):
                headers['Authorization'] = f'Bearer {token_mgr.access_token}'
                resp = await client.post(CREATE_SESSION_URL, headers=headers, json={}, timeout=10.0)
        data = resp.json()
        return data.get('data', {}).get('data', '')
    except Exception as e:
        print(f'[Session] 创建会话失败，使用降级UUID: {e}')
        return uuid.uuid4().hex

@app.get('/v1/models')
@app.get('/models')
async def list_models():
    return {
        'object': 'list',
        'data': [
            {'id': '国家反诈AI', 'object': 'model', 'created': 1788768000, 'owned_by': 'fanzha'},
            {'id': 'fanzha-ai', 'object': 'model', 'created': 1788768000, 'owned_by': 'fanzha'},
            {'id': 'gpt-4o-mini', 'object': 'model', 'created': 1788768000, 'owned_by': 'fanzha'}
        ]
    }

@app.get('/')
@app.get('/health')
async def health():
    return {'status': 'ok'}

@app.post('/v1/chat/completions')
@app.post('/chat/completions')
async def chat_completions(request: Request):
    auth_header = request.headers.get('Authorization', '')
    req_token = extract_token_from_header(auth_header)
    current_access_token = req_token if req_token else token_mgr.access_token

    if not current_access_token:
        raise HTTPException(
            status_code=401,
            detail="Missing access token. Please provide Authorization: Bearer <token> or set FANZHA_ACCESS_TOKEN environment variable."
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid JSON body')

    messages = body.get('messages', [])
    stream = body.get('stream', False)
    model = body.get('model', DEFAULT_MODEL)

    if not messages:
        raise HTTPException(status_code=400, detail='No messages provided')

    user_prompt = ''
    for msg in reversed(messages):
        if msg.get('role') == 'user':
            user_prompt = extract_text_content(msg.get('content'))
            break

    if not user_prompt:
        user_prompt = extract_text_content(messages[-1].get('content'))

    client = httpx.AsyncClient(timeout=60.0)
    session_id = await create_upstream_session(client, current_access_token)

    upstream_payload = {
        'conversation_id': session_id,
        'query': user_prompt
    }

    upstream_headers = {
        'Authorization': f'Bearer {current_access_token}',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 15; V2425A)'
    }

    chat_id = f'chatcmpl-{uuid.uuid4().hex}'
    created_time = int(time.time())

    try:
        if stream:
            async def sse_converter():
                has_yielded_any = False
                try:
                    start_chunk = {
                        'id': chat_id,
                        'object': 'chat.completion.chunk',
                        'created': created_time,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]
                    }
                    yield f'data: {json.dumps(start_chunk, ensure_ascii=False)}\n\n'

                    async with client.stream('POST', CHAT_STREAM_URL, headers=upstream_headers, json=upstream_payload) as r:
                        async for line in r.aiter_lines():
                            if not line:
                                continue
                            if line.startswith('data:'):
                                raw_json = line[5:].strip()
                                if raw_json == '[DONE]':
                                    continue
                                try:
                                    event = json.loads(raw_json)
                                    d = event.get('data', {})
                                    if isinstance(d, dict) and d.get('type') == 'answer' and d.get('answer'):
                                        chunk_text = d.get('answer')
                                        has_yielded_any = True
                                        chunk_resp = {
                                            'id': chat_id,
                                            'object': 'chat.completion.chunk',
                                            'created': created_time,
                                            'model': model,
                                            'choices': [{'index': 0, 'delta': {'content': chunk_text}, 'finish_reason': None}]
                                        }
                                        yield f'data: {json.dumps(chunk_resp, ensure_ascii=False)}\n\n'
                                except Exception:
                                    pass

                    if not has_yielded_any:
                        fallback_chunk = {
                            'id': chat_id,
                            'object': 'chat.completion.chunk',
                            'created': created_time,
                            'model': model,
                            'choices': [{'index': 0, 'delta': {'content': '您好！国家反诈AI智能助手已收到您的问题。请直接输入您想咨询的反诈问题或可疑情况。'}, 'finish_reason': None}]
                        }
                        yield f'data: {json.dumps(fallback_chunk, ensure_ascii=False)}\n\n'

                    end_chunk = {
                        'id': chat_id,
                        'object': 'chat.completion.chunk',
                        'created': created_time,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]
                    }
                    yield f'data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n'
                    yield 'data: [DONE]\n\n'
                finally:
                    await client.aclose()

            return StreamingResponse(sse_converter(), media_type='text/event-stream')

        else:
            full_answer = []
            try:
                async with client.stream('POST', CHAT_STREAM_URL, headers=upstream_headers, json=upstream_payload) as r:
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        if line.startswith('data:'):
                            raw_json = line[5:].strip()
                            if raw_json == '[DONE]':
                                continue
                            try:
                                event = json.loads(raw_json)
                                d = event.get('data', {})
                                if isinstance(d, dict) and d.get('type') == 'answer' and d.get('answer'):
                                    full_answer.append(d.get('answer'))
                            except Exception:
                                pass
            finally:
                await client.aclose()

            content = ''.join(full_answer) or '您好！国家反诈AI智能助手已收到您的问题。'
            return JSONResponse({
                'id': chat_id,
                'object': 'chat.completion',
                'created': created_time,
                'model': model,
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': len(user_prompt), 'completion_tokens': len(content), 'total_tokens': len(user_prompt) + len(content)}
            })

    except Exception as exc:
        await client.aclose()
        raise HTTPException(status_code=500, detail=str(exc))

if __name__ == '__main__':
    host = str(_setting('HOST', 'host', '127.0.0.1'))
    port = _port_setting()
    uvicorn.run(app, host=host, port=port)

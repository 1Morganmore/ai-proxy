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
                headers=build_upstream_headers(self.refresh_token),
                json={},
                timeout=10.0
            )
            data = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else None
            if not isinstance(data, dict):
                return False
            payload = data.get('data')
            if data.get('code') == 200 and isinstance(payload, dict):
                new_at = payload.get('access_token')
                new_rt = payload.get('refresh_token')
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
        if token.startswith('eyJ') and token.count('.') == 2:
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


ROLE_LABELS = {
    'system': '系统',
    'user': '用户',
    'assistant': '助手',
}
MAX_PROMPT_CHARS = 4000


def flatten_messages(messages: List[Any], max_chars: int = MAX_PROMPT_CHARS) -> str:
    turns: List[tuple[str, str]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        text = extract_text_content(msg.get('content')).strip()
        if not text:
            continue
        turns.append((msg.get('role') or 'user', text))
    if not turns:
        return ''

    def format_turn(role: str, text: str) -> str:
        if len(turns) == 1 and role == 'user':
            return text
        return f'{ROLE_LABELS.get(role, role)}: {text}'

    def clip(text: str, limit: int) -> str:
        if limit <= 0:
            return ''
        return text if len(text) <= limit else text[-limit:]

    formatted = [format_turn(role, text) for role, text in turns]
    joined = '\n'.join(formatted)
    if len(joined) <= max_chars:
        return joined

    last_idx = len(turns) - 1
    system_idx = next((i for i, (role, _) in enumerate(turns) if role == 'system'), None)
    keep_idx = []
    if system_idx is not None:
        keep_idx.append(system_idx)
    if last_idx not in keep_idx:
        keep_idx.append(last_idx)

    remaining = max_chars
    selected: dict[int, str] = {}
    for idx in keep_idx:
        if remaining <= 0:
            break
        selected[idx] = clip(formatted[idx], remaining)
        remaining -= len(selected[idx]) + (1 if selected else 0)

    for idx in range(last_idx - 1, -1, -1):
        if idx in selected or remaining <= 1:
            continue
        piece = clip(formatted[idx], remaining - 1)
        if not piece:
            continue
        selected[idx] = piece
        remaining -= len(piece) + 1

    return '\n'.join(selected[i] for i in sorted(selected))





UPSTREAM_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
)


def build_upstream_headers(access_token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'User-Agent': UPSTREAM_UA,
        'Accept': 'application/json, text/event-stream',
        'Referer': f'{BASE_URL}/',
        'Origin': BASE_URL,
        'channel': 'web',
    }


def build_upstream_payload(session_id: str, prompt: str, max_tokens: int = 2048) -> dict[str, Any]:
    return {
        'text': prompt,
        'version': 2,
        'stream': True,
        'max_tokens': max_tokens,
        'conversation_id': session_id,
        'using_context': False,
        'temperature': '0.1',
        'files': [],
        'model_name': '',
        'answer_mode': 'normal',
    }


def extract_answer_delta(event: Any) -> str:
    if not isinstance(event, dict):
        return ''
    data = event.get('data', {})
    if not isinstance(data, dict) or data.get('type') != 'answer':
        return ''
    answer = data.get('answer')
    return answer if isinstance(answer, str) else ''


def parse_sse_line(line: str) -> Any:
    if not line or not line.startswith('data:'):
        return None
    raw_json = line[5:].strip()
    if not raw_json or raw_json == '[DONE]':
        return None
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return None


async def iter_upstream_answers(client: httpx.AsyncClient, headers: dict[str, str], payload: dict[str, Any]):
    async with client.stream('POST', CHAT_STREAM_URL, headers=headers, json=payload) as response:
        if response.status_code >= 400:
            error_body = (await response.aread()).decode('utf-8', errors='replace')
            raise HTTPException(status_code=502, detail=f'上游聊天接口失败 ({response.status_code}): {error_body[:500]}')
        async for line in response.aiter_lines():
            event = parse_sse_line(line)
            if event is None:
                continue
            delta = extract_answer_delta(event)
            if delta:
                yield delta



def _json_or_none(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


def _session_id_from_body(data: Any) -> str:
    if not isinstance(data, dict):
        return ''
    payload = data.get('data')
    if not isinstance(payload, dict):
        return ''
    session_id = payload.get('data')
    return session_id if isinstance(session_id, str) and session_id.strip() else ''


def _is_unauthorized(resp: httpx.Response, data: Any) -> bool:
    if resp.status_code == 401:
        return True
    if not isinstance(data, dict):
        return False
    if data.get('code') == 401:
        return True
    payload = data.get('data')
    return isinstance(payload, dict) and payload.get('code') in (401, '401')


async def create_upstream_session(client: httpx.AsyncClient, current_access_token: str) -> tuple[str, str]:
    effective_token = current_access_token
    headers = build_upstream_headers(effective_token)
    try:
        resp = await client.post(CREATE_SESSION_URL, headers=headers, json={}, timeout=10.0)
        data = _json_or_none(resp)
        if _is_unauthorized(resp, data) and current_access_token == token_mgr.access_token:
            if await token_mgr.refresh_if_needed(client):
                effective_token = token_mgr.access_token
                headers = build_upstream_headers(effective_token)
                resp = await client.post(CREATE_SESSION_URL, headers=headers, json={}, timeout=10.0)
                data = _json_or_none(resp)
        session_id = _session_id_from_body(data)
        if resp.status_code >= 400 or not session_id:
            raise RuntimeError(f'HTTP {resp.status_code}: {resp.text[:500]}')
        return session_id, effective_token
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'创建上游会话失败: {exc}') from exc





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

    user_prompt = flatten_messages(messages)
    if not user_prompt:
        raise HTTPException(status_code=400, detail='No user content provided')

    max_tokens = body.get('max_tokens') or 2048
    try:
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        max_tokens = 2048

    chat_id = f'chatcmpl-{uuid.uuid4().hex}'
    created_time = int(time.time())
    client = httpx.AsyncClient(timeout=90.0)
    owns_client = True
    try:
        session_id, current_access_token = await create_upstream_session(client, current_access_token)
        payload = build_upstream_payload(session_id, user_prompt, max_tokens=max_tokens)
        headers = build_upstream_headers(current_access_token)

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
                    try:
                        async for chunk_text in iter_upstream_answers(client, headers, payload):
                            has_yielded_any = True
                            chunk_resp = {
                                'id': chat_id,
                                'object': 'chat.completion.chunk',
                                'created': created_time,
                                'model': model,
                                'choices': [{'index': 0, 'delta': {'content': chunk_text}, 'finish_reason': None}]
                            }
                            yield f'data: {json.dumps(chunk_resp, ensure_ascii=False)}\n\n'
                        if not has_yielded_any:
                            error_chunk = {
                                'id': chat_id,
                                'object': 'error',
                                'error': {'message': '上游未返回增量回答', 'type': 'upstream_error', 'code': 502},
                            }
                            yield f'data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n'
                            yield 'data: [DONE]\n\n'
                            return
                    except HTTPException as exc:
                        error_chunk = {
                            'id': chat_id,
                            'object': 'error',
                            'error': {'message': str(exc.detail), 'type': 'upstream_error', 'code': exc.status_code},
                        }
                        yield f'data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n'
                        yield 'data: [DONE]\n\n'
                        return
                    except Exception as copilot_exc:
                        error_chunk = {
                            'id': chat_id,
                            'object': 'error',
                            'error': {'message': str(copilot_exc), 'type': 'upstream_error', 'code': 502},
                        }
                        yield f'data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n'
                        yield 'data: [DONE]\n\n'
                        return
                    if not has_yielded_any:
                        return
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

            owns_client = False
            return StreamingResponse(sse_converter(), media_type='text/event-stream')

        full_answer = []
        async for chunk_text in iter_upstream_answers(client, headers, payload):
            full_answer.append(chunk_text)
        content = ''.join(full_answer)
        if not content:
            raise HTTPException(status_code=502, detail='上游未返回增量回答')
        return JSONResponse({
            'id': chat_id,
            'object': 'chat.completion',
            'created': created_time,
            'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': len(user_prompt), 'completion_tokens': len(content), 'total_tokens': len(user_prompt) + len(content)}
        })
    except HTTPException:
        raise
    except Exception as copilot_exc:
        raise HTTPException(status_code=500, detail=str(copilot_exc))
    finally:
        if owns_client:
            await client.aclose()


if __name__ == '__main__':
    host = str(_setting('HOST', 'host', '127.0.0.1'))
    port = _port_setting()
    uvicorn.run(app, host=host, port=port)

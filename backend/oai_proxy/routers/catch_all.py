from collections.abc import Iterable
from functools import lru_cache
from urllib.parse import quote
import json

import httpx
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from api.util.aes_gcm import decrypt_token, derive_key
from oai_proxy.core.config import settings

# Marker token that triggers use of the static key
STATIC_KEY_MARKER = 'STATIC'

# Parameters not supported by non-OpenAI models (Claude, Gemini, etc.)
UNSUPPORTED_BODY_PARAMS = {
    'prompt_cache_key',
    'cache_control',
}


HOP_BY_HOP_HEADERS = {
    'connection',
    'host',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailers',
    'transfer-encoding',
    'upgrade',
}

router = APIRouter()


@lru_cache(maxsize=1)
def _aesgcm_key() -> bytes:
    return derive_key(settings.OAI_PROXY_AES_KEY.get_secret_value())


def _get_static_key() -> str | None:
    if settings.OAI_PROXY_STATIC_KEY:
        return settings.OAI_PROXY_STATIC_KEY.get_secret_value()
    return None


def _decrypt_token(token: str) -> str:
    try:
        return decrypt_token(token, key=_aesgcm_key())
    except ValueError as err:
        raise HTTPException(status_code=401, detail='Invalid token') from err


def _resolve_openai_key_and_model(token: str) -> tuple[str, str | None]:
    """Resolve the actual OpenAI key and optional real model from the provided token.

    Token format: "TOKEN" or "TOKEN::real_model"
    If token starts with STATIC_KEY_MARKER, use the static key (if configured).
    Otherwise, decrypt the encrypted token.

    Returns: (openai_key, real_model or None)
    """
    real_model = None
    if '::' in token:
        token, real_model = token.split('::', 1)

    if token == STATIC_KEY_MARKER:
        static_key = _get_static_key()
        if not static_key:
            raise HTTPException(
                status_code=501,
                detail='Static key not configured on proxy',
            )
        return static_key, real_model
    return _decrypt_token(token), real_model


def _get_authorization_token(request: Request) -> str:
    auth_header = request.headers.get('authorization')
    if not auth_header:
        raise HTTPException(status_code=401, detail='Missing Authorization header')
    scheme, _, token = auth_header.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(status_code=401, detail='Invalid Authorization header')
    return token


def _filter_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in items:
        key_lower = key.lower()
        if key_lower in HOP_BY_HOP_HEADERS:
            continue
        if key_lower == 'content-length':
            continue
        headers[key_lower] = value
    return headers


# Models that need to use Chat Completions API instead of Responses API
# due to LiteLLM bugs with Responses API
CHAT_COMPLETIONS_MODELS = {
    'gemini-3-flash-preview',
    'gemini-2.5-flash-lite',
    'claude-opus-4-5',  # Bedrock has tool conversion issues with Responses API
}


def _convert_tool_responses_to_chat(tool: dict) -> dict:
    """Convert a single tool from Responses API format to Chat Completions format.

    Responses API: {"type": "function", "name": "...", "description": "...", "parameters": {...}}
    Chat Completions: {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    if 'function' in tool:
        # Already in Chat Completions format
        return tool
    # Convert from Responses API format
    return {
        'type': tool.get('type', 'function'),
        'function': {
            'name': tool.get('name', ''),
            'description': tool.get('description', ''),
            'parameters': tool.get('parameters', {}),
        }
    }


def _extract_text_content(content: list | str | None) -> str:
    """Extract text from Responses API content format."""
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                # Handle {"type": "input_text", "text": "..."} or {"type": "output_text", "text": "..."}
                if item.get('type') in ('input_text', 'output_text', 'text'):
                    texts.append(item.get('text', ''))
                elif 'text' in item:
                    texts.append(item.get('text', ''))
        return '\n'.join(texts)
    return str(content)


def _convert_messages_responses_to_chat(messages: list) -> list:
    """Convert messages from Responses API format to Chat Completions format.

    Handles:
    - message → user/system/assistant message
    - function_call → assistant message with tool_calls
    - function_call_output → tool message

    Groups consecutive function_calls into a single assistant message.
    """
    result = []
    pending_tool_calls = []

    role_map = {
        'developer': 'system',
        'system': 'system',
        'user': 'user',
        'assistant': 'assistant',
    }

    def flush_tool_calls():
        nonlocal pending_tool_calls
        if pending_tool_calls:
            result.append({
                'role': 'assistant',
                'content': None,
                'tool_calls': pending_tool_calls,
            })
            pending_tool_calls = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get('type')

        if msg_type == 'function_call':
            # Accumulate tool calls for assistant message
            pending_tool_calls.append({
                'id': msg.get('call_id', ''),
                'type': 'function',
                'function': {
                    'name': msg.get('name', ''),
                    'arguments': msg.get('arguments', '{}'),
                }
            })

        elif msg_type == 'function_call_output':
            # First flush any pending tool calls
            flush_tool_calls()
            # Add tool result message
            result.append({
                'role': 'tool',
                'tool_call_id': msg.get('call_id', ''),
                'content': msg.get('output', ''),
            })

        elif msg_type == 'message' or msg_type is None:
            # First flush any pending tool calls
            flush_tool_calls()
            # Regular message
            role = msg.get('role', 'user')
            chat_role = role_map.get(role, role)
            content = _extract_text_content(msg.get('content'))
            result.append({'role': chat_role, 'content': content})

        elif 'role' in msg and isinstance(msg.get('content'), str):
            # Already in Chat Completions format
            flush_tool_calls()
            role = msg.get('role', 'user')
            chat_role = role_map.get(role, role)
            result.append({'role': chat_role, 'content': msg['content']})

    # Flush any remaining tool calls
    flush_tool_calls()

    return result


def _convert_responses_to_chat(body: dict) -> dict:
    """Convert Responses API request format to Chat Completions API format."""
    chat_body = {}

    # Copy common fields (except tools which need special handling)
    for key in ['model', 'tool_choice', 'temperature', 'top_p', 'max_tokens']:
        if key in body:
            chat_body[key] = body[key]

    # Convert tools from Responses API format to Chat Completions format
    if 'tools' in body and isinstance(body['tools'], list):
        # Log problematic tools that don't have name
        for i, t in enumerate(body['tools']):
            if not t.get('name') and not t.get('function', {}).get('name'):
                logger.warning(f'Tool {i} missing name: {json.dumps(t)[:500]}')
        # Filter out tools without proper name
        valid_tools = [t for t in body['tools'] if t.get('name') or t.get('function', {}).get('name')]
        logger.info(f'Tools: {len(body["tools"])} original, {len(valid_tools)} valid')
        chat_body['tools'] = [_convert_tool_responses_to_chat(t) for t in valid_tools]

    # Convert 'input' to 'messages'
    if 'input' in body:
        input_data = body['input']
        if isinstance(input_data, str):
            chat_body['messages'] = [{'role': 'user', 'content': input_data}]
        elif isinstance(input_data, list):
            # Convert messages from Responses API format (handles function_call and function_call_output)
            chat_body['messages'] = _convert_messages_responses_to_chat(input_data)
        else:
            chat_body['messages'] = [{'role': 'user', 'content': str(input_data)}]
    elif 'messages' in body:
        chat_body['messages'] = body['messages']

    return chat_body


def _convert_chat_to_responses(chat_response: dict, original_request: dict | None = None) -> dict:
    """Convert Chat Completions API response to Responses API format.

    Native Responses API output format for tool calls:
    {"type": "function_call", "name": "...", "arguments": "...", "call_id": "...", "id": "...", "status": "completed"}

    Native Responses API output format for text:
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "..."}]}
    """
    import time
    import secrets
    created_at = chat_response.get('created', int(time.time()))

    # Generate a response ID similar to OpenAI's native format
    original_id = chat_response.get('id', '')
    # Use a long random string similar to OpenAI's format
    random_suffix = secrets.token_urlsafe(192)
    resp_id = f"resp_{random_suffix}"

    resp = {
        'id': resp_id,
        'created_at': created_at,
        'completed_at': created_at + 1,
        'error': None,
        'incomplete_details': None,
        'instructions': None,
        'metadata': {},
        'model': chat_response.get('model', ''),
        'object': 'response',
        'status': 'completed',
        'output': [],
        'parallel_tool_calls': True,
        'temperature': 1.0,
        'tool_choice': 'auto',
        'tools': original_request.get('tools', []) if original_request else [],
        'top_p': 1.0,
        'max_output_tokens': None,
        'previous_response_id': None,
        'reasoning': {'effort': 'none', 'summary': None},
        'text': {'format': {'type': 'text'}, 'verbosity': 'medium'},
        'truncation': 'disabled',
        'usage': chat_response.get('usage', {}),
        'user': None,
        'store': True,
        'background': False,
    }

    # Convert choices to output
    for choice in chat_response.get('choices', []):
        msg = choice.get('message', {})

        # Handle tool calls - convert to native Responses API function_call format
        if 'tool_calls' in msg and msg['tool_calls']:
            for tool_call in msg['tool_calls']:
                func = tool_call.get('function', {})
                output_item = {
                    'type': 'function_call',
                    'name': func.get('name', ''),
                    'arguments': func.get('arguments', '{}'),
                    'call_id': tool_call.get('id', ''),
                    'id': f"fc_{tool_call.get('id', '')}",
                    'status': 'completed',
                }
                resp['output'].append(output_item)
        else:
            # Handle text response
            content = msg.get('content', '')
            if content:
                output_item = {
                    'type': 'message',
                    'id': f"msg_{choice.get('index', 0)}",
                    'status': 'completed',
                    'role': msg.get('role', 'assistant'),
                    'content': [{'type': 'output_text', 'text': content, 'annotations': []}]
                }
                resp['output'].append(output_item)

    return resp


def _filter_body_params(body: dict, real_model: str | None = None) -> dict:
    """Remove unsupported parameters and optionally substitute model name."""
    filtered = {k: v for k, v in body.items() if k not in UNSUPPORTED_BODY_PARAMS}

    # Substitute model name if real_model is provided (for non-OpenAI models)
    if real_model and 'model' in filtered:
        filtered['model'] = real_model

    # LiteLLM should handle tool format conversion automatically
    # No manual conversion needed

    # Also filter nested structures like messages
    if 'messages' in filtered and isinstance(filtered['messages'], list):
        for msg in filtered['messages']:
            if isinstance(msg, dict):
                for param in UNSUPPORTED_BODY_PARAMS:
                    msg.pop(param, None)
    return filtered


async def _proxy_request(request: Request, path: str) -> StreamingResponse:
    token = _get_authorization_token(request)
    openai_key, real_model = _resolve_openai_key_and_model(token)
    forward_headers = _filter_headers(request.headers.items())
    forward_headers['authorization'] = f'Bearer {openai_key}'

    target_path = path.lstrip('/')
    base_url = settings.OAI_PROXY_OPENAI_BASE_URL.rstrip('/')

    params = dict(request.query_params)
    use_chat_api = False  # Flag to convert responses API to chat completions

    # Read and filter body for JSON requests
    content_type = forward_headers.get('content-type', '')
    if 'application/json' in content_type and request.method in ('POST', 'PUT', 'PATCH'):
        raw_body = await request.body()
        try:
            body_json = json.loads(raw_body)
            logger.info(f'Original model: {body_json.get("model")}, real_model: {real_model}, path: {target_path}')
            filtered_body = _filter_body_params(body_json, real_model=real_model)
            logger.info(f'Filtered model: {filtered_body.get("model")}')

            # Check if we need to convert Responses API to Chat Completions API
            # for models with LiteLLM Responses API bugs (only when tools are present)
            is_responses_api = target_path in ('responses', 'v1/responses')
            has_tools = bool(filtered_body.get('tools'))
            logger.debug(f'is_responses_api={is_responses_api}, has_tools={has_tools}, real_model={real_model}')
            if real_model in CHAT_COMPLETIONS_MODELS and is_responses_api and has_tools:
                logger.info(f'Converting Responses API to Chat Completions for {real_model}')
                logger.info(f'Original input type: {type(filtered_body.get("input"))}, preview: {str(filtered_body.get("input"))[:500]}')
                filtered_body = _convert_responses_to_chat(filtered_body)
                target_path = 'chat/completions'
                use_chat_api = True
                logger.info(f'Converted messages: {json.dumps(filtered_body.get("messages", []))[:1000]}')

            body_bytes = json.dumps(filtered_body).encode('utf-8')
        except (json.JSONDecodeError, TypeError):
            body_bytes = raw_body
    else:
        body_bytes = await request.body()

    encoded_path = quote(target_path, safe='/')
    target_url = f'{base_url}/{encoded_path}' if encoded_path else base_url

    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))
    upstream = await client.send(
        client.build_request(
            request.method,
            target_url,
            params=params,
            headers=forward_headers,
            content=body_bytes,
        ),
        stream=True,
    )

    response_headers = _filter_headers(upstream.headers.items())

    # Log non-200 responses for debugging
    if use_chat_api and upstream.status_code != 200:
        error_body = await upstream.aread()
        logger.error(f'Chat API error response ({upstream.status_code}): {error_body.decode()[:1000]}')
        await upstream.aclose()
        await client.aclose()
        return StreamingResponse(
            iter([error_body]),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    # If we converted to Chat API, we need to convert the response back
    if use_chat_api and upstream.status_code == 200:
        response_body = await upstream.aread()
        await upstream.aclose()
        await client.aclose()

        try:
            chat_response = json.loads(response_body)
            logger.debug(f'Chat API response: {json.dumps(chat_response)[:500]}')
            responses_response = _convert_chat_to_responses(chat_response, original_request=body_json)
            logger.debug(f'Converted to Responses API: {json.dumps(responses_response)[:500]}')
            return StreamingResponse(
                iter([json.dumps(responses_response).encode('utf-8')]),
                status_code=200,
                headers=response_headers,
            )
        except (json.JSONDecodeError, TypeError):
            return StreamingResponse(
                iter([response_body]),
                status_code=upstream.status_code,
                headers=response_headers,
            )

    async def _cleanup() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(_cleanup),
    )


@router.api_route('/', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
@router.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
async def proxy_all(request: Request, path: str = '') -> StreamingResponse:
    return await _proxy_request(request, path)

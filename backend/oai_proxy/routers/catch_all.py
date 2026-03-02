import asyncio
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

# 429 retry config
MAX_RETRIES = 5
DEFAULT_RETRY_WAIT = 10  # seconds when Retry-After header is missing

# Marker token that triggers use of the static key
STATIC_KEY_MARKER = 'STATIC'

# Parameters not supported by non-OpenAI models (Claude, Gemini, etc.)
UNSUPPORTED_BODY_PARAMS = {
    'prompt_cache_key',
    'cache_control',
    'store',  # Bedrock doesn't support this
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


def _filter_body_params(
    body: dict,
    real_model: str | None = None,
    *,
    skip_params: set[str] | None = None,
) -> dict:
    """Remove unsupported parameters and optionally substitute model name."""
    blocked = skip_params if skip_params is not None else UNSUPPORTED_BODY_PARAMS
    filtered = {k: v for k, v in body.items() if k not in blocked}

    # Substitute model name if real_model is provided (for non-OpenAI models)
    if real_model and 'model' in filtered:
        filtered['model'] = real_model

    # Also filter nested structures like messages
    if 'messages' in filtered and isinstance(filtered['messages'], list):
        for msg in filtered['messages']:
            if isinstance(msg, dict):
                for param in blocked:
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

    # Read and filter body for JSON requests
    content_type = forward_headers.get('content-type', '')
    if 'application/json' in content_type and request.method in ('POST', 'PUT', 'PATCH'):
        raw_body = await request.body()
        try:
            body_json = json.loads(raw_body)
            logger.info(f'Original model: {body_json.get("model")}, real_model: {real_model}, path: {target_path}')

            # Per-model route override — check before filtering so we know
            # which params to keep (direct OpenAI-compat endpoints need `store`).
            effective_model = real_model or body_json.get('model')
            route = settings.OAI_PROXY_MODEL_ROUTES.get(effective_model) if effective_model else None
            if route:
                base_url = route['base_url'].rstrip('/')
                if 'api_key' in route:
                    forward_headers['authorization'] = f'Bearer {route["api_key"]}'
                logger.info(f'Model route override: {effective_model} -> {base_url}')

            # For routed models (direct OpenAI-compat endpoints), keep `store`
            # but force it to true so server-side context is preserved for
            # previous_response_id chaining across turns.
            skip = UNSUPPORTED_BODY_PARAMS - {'store'} if route else UNSUPPORTED_BODY_PARAMS
            filtered_body = _filter_body_params(body_json, real_model=real_model, skip_params=skip)
            if route and 'store' in filtered_body:
                filtered_body['store'] = True
            logger.info(f'Filtered model: {filtered_body.get("model")}')

            # Debug: log key request params for routed models
            if route:
                tools = filtered_body.get('tools', [])
                tool_names = [t.get('name', t.get('function', {}).get('name', '?')) for t in tools] if isinstance(tools, list) else '?'
                input_val = filtered_body.get('input', '')
                input_len = len(json.dumps(input_val)) if input_val else 0
                input_count = len(input_val) if isinstance(input_val, list) else 0
                prev_id = filtered_body.get('previous_response_id')
                logger.info(
                    f'Route debug: store={filtered_body.get("store")}, '
                    f'prev_resp_id={prev_id}, '
                    f'tools={tool_names}, '
                    f'input_len={input_len}, input_msgs={input_count}, '
                    f'keys={list(filtered_body.keys())}'
                )

            body_bytes = json.dumps(filtered_body).encode('utf-8')
        except (json.JSONDecodeError, TypeError):
            body_bytes = raw_body
    else:
        body_bytes = await request.body()

    encoded_path = quote(target_path, safe='/')
    target_url = f'{base_url}/{encoded_path}' if encoded_path else base_url

    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))

    # Retry loop for 429 rate limiting
    for attempt in range(MAX_RETRIES + 1):
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

        if upstream.status_code != 429 or attempt == MAX_RETRIES:
            break

        # Read body so connection is released, then wait and retry
        await upstream.aread()
        await upstream.aclose()

        retry_after = upstream.headers.get('retry-after')
        try:
            wait = int(retry_after) if retry_after else DEFAULT_RETRY_WAIT
        except (ValueError, TypeError):
            wait = DEFAULT_RETRY_WAIT
        wait = min(wait, 60)  # cap at 60s

        logger.warning(
            f'429 rate limited (attempt {attempt + 1}/{MAX_RETRIES}), '
            f'waiting {wait}s before retry: {target_url}'
        )
        await asyncio.sleep(wait)

    response_headers = _filter_headers(upstream.headers.items())

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

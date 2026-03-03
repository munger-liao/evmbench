from api.core.config import settings

_BASE_MODELS = {
    # LiteLLM model names
    'gpt-5.2',
    'claude-opus-4-6',
    'gemini-3-flash-preview',
    # Direct Azure OpenAI
    'gpt-5.3-codex',
    # Direct Google Gemini
    'gemini-3-pro-preview',
}

ALLOWED_MODELS = _BASE_MODELS | set(settings.OAI_PROXY_MODEL_ROUTES.keys())

from fastapi import APIRouter

from api.core.config import settings
from api.core.impl import auth_backend
from api.schemas.integration import FrontendConfig, ModelOption


router = APIRouter(prefix='/integration', tags=['integration'])

_BASE_MODELS: list[ModelOption] = [
    ModelOption(value='claude-opus-4-6', label='Claude Opus 4.6'),
    ModelOption(value='gpt-5.2', label='GPT-5.2'),
    ModelOption(value='gpt-5.3-codex', label='GPT-5.3 Codex'),
    ModelOption(value='gemini-3-flash-preview', label='Gemini 3 Flash'),
    ModelOption(value='gemini-3-pro-preview', label='Gemini 3 Pro'),
]


def _build_model_list() -> list[ModelOption]:
    models = list(_BASE_MODELS)
    base_values = {m.value for m in _BASE_MODELS}
    for key, route in settings.OAI_PROXY_MODEL_ROUTES.items():
        if key not in base_values:
            label = route.get('label', key)
            models.append(ModelOption(value=key, label=label))
    return models


@router.get('/frontend')
async def frontend_config() -> FrontendConfig:
    return FrontendConfig(
        auth_enabled=bool(auth_backend),
        key_predefined=settings.BACKEND_STATIC_OAI_KEY is not None or settings.BACKEND_USE_PROXY_STATIC_KEY,
        models=_build_model_list(),
    )

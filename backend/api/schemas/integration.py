from pydantic import BaseModel


class ModelOption(BaseModel):
    value: str
    label: str


class FrontendConfig(BaseModel):
    auth_enabled: bool
    key_predefined: bool
    models: list[ModelOption]

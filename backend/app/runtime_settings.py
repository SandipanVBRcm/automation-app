"""Locally persisted settings that can change while the backend is running."""

import json
import os
from pydantic import BaseModel, Field
from app.config import DATA, settings


class RuntimeValues(BaseModel):
    max_steps: int = Field(ge=1, le=500)
    idle_timeout: int = Field(ge=60, le=86400, multiple_of=60)
    playwright_mcp_enabled: bool = True
    file_mcp_enabled: bool = True


class RuntimeSettings:
    def __init__(self, path=DATA / 'runtime-settings.json'):
        self.path = path
        self.values = RuntimeValues(max_steps=settings.max_agent_steps, idle_timeout=settings.session_idle_timeout)
        try:
            self.values = RuntimeValues.model_validate_json(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            # A damaged local settings file must not prevent the backend from starting.
            pass

    def update(self, values: RuntimeValues) -> RuntimeValues:
        temporary = self.path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(values.model_dump()), encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.values = values
        return values


runtime_settings = RuntimeSettings()

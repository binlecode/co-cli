import os
import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, model_validator

APP_NAME = "co-cli"

# XDG Paths - Explicit XDG resolution so ~/.config/ is used even on macOS
# (platformdirs would resolve to ~/Library/Application Support/ on macOS)
CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
DATA_DIR = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# Ensure directories exist
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

class Settings(BaseModel):
    # Core Tools
    obsidian_vault_path: Optional[str] = Field(default=None)
    slack_bot_token: Optional[str] = Field(default=None)
    google_credentials_path: Optional[str] = Field(default=None)
    
    # Behavior
    auto_confirm: bool = Field(default=False)
    docker_image: str = Field(default="co-cli-sandbox")
    theme: str = Field(default="light")
    tool_retries: int = Field(default=3)
    max_request_limit: int = Field(default=25)

    # LLM Settings (Gemini / Ollama)
    gemini_api_key: Optional[str] = Field(default=None)
    llm_provider: str = Field(default="gemini")
    ollama_host: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="glm-4.7-flash:q8_0")
    gemini_model: str = Field(default="gemini-2.0-flash")

    @model_validator(mode='before')
    @classmethod
    def fill_from_env(cls, data: dict) -> dict:
        """
        For each field, if it's not in 'data' or is empty/None, 
        try to get it from the environment.
        """
        env_map = {
            "obsidian_vault_path": "OBSIDIAN_VAULT_PATH",
            "slack_bot_token": "SLACK_BOT_TOKEN",
            "google_credentials_path": "GOOGLE_CREDENTIALS_PATH",
            "auto_confirm": "CO_CLI_AUTO_CONFIRM",
            "docker_image": "CO_CLI_DOCKER_IMAGE",
            "theme": "CO_CLI_THEME",
            "tool_retries": "CO_CLI_TOOL_RETRIES",
            "max_request_limit": "CO_CLI_MAX_REQUEST_LIMIT",
            "gemini_api_key": "GEMINI_API_KEY",
            "llm_provider": "LLM_PROVIDER",
            "ollama_host": "OLLAMA_HOST",
            "ollama_model": "OLLAMA_MODEL",
            "gemini_model": "GEMINI_MODEL",
        }
        
        for field, env_var in env_map.items():
            if field not in data or data[field] is None:
                val = os.getenv(env_var)
                if val:
                    data[field] = val
        return data

    def save(self):
        """Save current settings to settings.json"""
        with open(SETTINGS_FILE, "w") as f:
            f.write(self.model_dump_json(indent=2, exclude_none=True))

def load_config() -> Settings:
    data = {}
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r") as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"Error loading settings.json: {e}. Using environment variables.")
    
    # Pydantic will run the validator and fill in missing fields from env
    return Settings.model_validate(data)

# Global config instance
settings = load_config()

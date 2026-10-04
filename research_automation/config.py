from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    database_path: Path
    sec_user_agent: str
    ollama_model: str
    ollama_base_url: str
    dart_api_key: Optional[str]
    krx_auth_key: Optional[str]
    ecos_api_key: Optional[str]
    fred_api_key: Optional[str]
    meritz_app_key: Optional[str] = field(default=None, repr=False)
    meritz_app_secret: Optional[str] = field(default=None, repr=False)

    def key_status(self) -> Dict[str, bool]:
        """비밀값 자체를 노출하지 않고 설정 여부만 반환합니다."""
        return {
            "SEC_USER_AGENT": bool(
                self.sec_user_agent and "example.com" not in self.sec_user_agent
            ),
            "DART_API_KEY": bool(self.dart_api_key),
            "KRX_AUTH_KEY": bool(self.krx_auth_key),
            "ECOS_API_KEY": bool(self.ecos_api_key),
            "FRED_API_KEY": bool(self.fred_api_key),
            "MERITZ_APP_KEY": bool(self.meritz_app_key),
            "MERITZ_APP_SECRET": bool(self.meritz_app_secret),
        }


def _optional_env(name: str) -> Optional[str]:
    value = os.getenv(name, "").strip()
    return value or None


def load_settings(project_root: Optional[Path] = None) -> Settings:
    root = (project_root or Path.cwd()).resolve()
    load_dotenv(root / ".env", override=False)

    raw_db_path = Path(os.getenv("RESEARCH_DB_PATH", "data/research.db"))
    database_path = raw_db_path if raw_db_path.is_absolute() else root / raw_db_path

    return Settings(
        project_root=root,
        database_path=database_path,
        sec_user_agent=os.getenv("SEC_USER_AGENT", "").strip(),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3.5:9b").strip(),
        ollama_base_url=os.getenv(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        ).rstrip("/"),
        dart_api_key=_optional_env("DART_API_KEY"),
        krx_auth_key=_optional_env("KRX_AUTH_KEY"),
        ecos_api_key=_optional_env("ECOS_API_KEY"),
        fred_api_key=_optional_env("FRED_API_KEY"),
        meritz_app_key=_optional_env("MERITZ_APP_KEY"),
        meritz_app_secret=_optional_env("MERITZ_APP_SECRET"),
    )

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    database_path: Path
    sec_user_agent: str
    clova_api_key: Optional[str]
    clova_model: str
    clova_base_url: str
    dart_api_key: Optional[str]
    krx_auth_key: Optional[str]
    ecos_api_key: Optional[str]
    fred_api_key: Optional[str]

    def key_status(self) -> Dict[str, bool]:
        """비밀값 자체를 노출하지 않고 설정 여부만 반환합니다."""
        return {
            "SEC_USER_AGENT": bool(
                self.sec_user_agent and "example.com" not in self.sec_user_agent
            ),
            "CLOVA_API_KEY": bool(self.clova_api_key),
            "DART_API_KEY": bool(self.dart_api_key),
            "KRX_AUTH_KEY": bool(self.krx_auth_key),
            "ECOS_API_KEY": bool(self.ecos_api_key),
            "FRED_API_KEY": bool(self.fred_api_key),
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
        clova_api_key=_optional_env("CLOVA_API_KEY"),
        clova_model=os.getenv("CLOVA_MODEL", "HCX-007").strip(),
        clova_base_url=os.getenv(
            "CLOVA_BASE_URL", "https://clovastudio.stream.ntruss.com/v1/openai"
        ).rstrip("/"),
        dart_api_key=_optional_env("DART_API_KEY"),
        krx_auth_key=_optional_env("KRX_AUTH_KEY"),
        ecos_api_key=_optional_env("ECOS_API_KEY"),
        fred_api_key=_optional_env("FRED_API_KEY"),
    )

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    deriv_app_id: str = os.getenv("DERIV_APP_ID", "")
    deriv_api_token: str = os.getenv("DERIV_API_TOKEN", "")
    deriv_account_id: str = os.getenv("DERIV_ACCOUNT_ID", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_allowed_user_ids: list[int] = (
        [int(x) for x in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if x.strip()]
    )
    stake_pct: float = float(os.getenv("STAKE_PCT", "1.0"))

settings = Settings()

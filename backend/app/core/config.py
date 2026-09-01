from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev")
    app_name: str = Field(default="revenue-recovery-control-plane")
    log_level: str = Field(default="INFO")

    database_url: str = Field(
        default="postgresql+asyncpg://recovery:recovery@localhost:55432/recovery"
    )

    # Where promoted model artifacts and their cards live. Empty means the
    # checked-in `models/artifacts` directory. Tests and deployments override it
    # so nothing writes a promotion pointer into the repository.
    model_artifacts_dir: str = Field(default="")

    razorpay_key_id: str = Field(default="")
    razorpay_key_secret: str = Field(default="")
    razorpay_webhook_secret: str = Field(default="")
    razorpay_base_url: str = Field(default="https://api.razorpay.com")
    razorpay_mode: str = Field(default="test", pattern="^(test|live)$")
    shadow_mode: bool = Field(default=True)

    # Closes the demo loop without touching Razorpay.  When enabled, simulated
    # (is_synthetic) opportunities get an attributable *simulated* payment link
    # instead of being dropped as "shadowed", so the recovery, verification and
    # experiment stages have something to measure.  It never affects real
    # traffic and never makes a network call: real opportunities stay shadowed
    # while shadow_mode is on.
    simulate_interventions: bool = Field(default=False)

    # Control-plane mutations are deliberately separate from Razorpay webhook
    # authentication.  Local demos may omit these keys, but non-dev deployments
    # fail closed until an operator key is configured.
    control_plane_api_key: str = Field(default="")
    control_plane_admin_api_key: str = Field(default="")

    policy_kill_switch: bool = Field(default=False)
    policy_max_amount_minor: int = Field(default=2_500_000)
    policy_max_contact_attempts: int = Field(default=3)
    policy_cooldown_minutes: int = Field(default=60)
    policy_confidence_floor: float = Field(default=0.35)
    policy_min_ev_margin_minor: int = Field(default=500)

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def razorpay_source(self) -> str:
        return f"razorpay_{self.razorpay_mode}"

    @property
    def webhook_secret_configured(self) -> bool:
        return bool(self.razorpay_webhook_secret)

    @property
    def control_plane_auth_required(self) -> bool:
        return self.app_env.lower() not in {"dev", "test", "local"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

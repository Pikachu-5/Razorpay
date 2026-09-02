from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults to the safe value, not the convenient one. `dev`/`test`/`local`
    # skip control-plane authentication entirely, so a deployment that simply
    # forgets to set APP_ENV must not inherit that. The checked-in `.env` and
    # `.env.example` set `dev` explicitly for local work.
    app_env: str = Field(default="production")
    app_name: str = Field(default="revenue-recovery-control-plane")
    log_level: str = Field(default="INFO")

    database_url: str = Field(
        default="postgresql+asyncpg://recovery:recovery@localhost:55432/recovery"
    )

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, value: str) -> str:
        # Managed Postgres providers (Azure, Heroku, ...) hand out DATABASE_URL
        # with a bare postgres(ql):// scheme, which SQLAlchemy's async engine
        # can't use directly -- it needs the asyncpg driver named explicitly.
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

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

    # A public demonstration install that deliberately leaves operator actions
    # unauthenticated so a reviewer can drive the console without credentials.
    # This is an explicit, named posture rather than the accident of an unset
    # APP_ENV: it is refused outright unless RAZORPAY_MODE is `test`, it never
    # grants the admin role (so forced model promotion still needs a key), it
    # is announced in the startup log, and the console badges it on screen.
    control_plane_open_demo: bool = Field(default=False)

    # Bounds that apply only while `open_demo_active`, so a public visitor
    # cannot exhaust a shared install. They are set above what the console's
    # own presets ask for -- the largest is 90 payments over 60s -- so ordinary
    # use never reaches them and only abuse does.
    #
    # The cost driver is the decision audit, not the payment: a decision row
    # carries its diagnosis, every candidate's prediction and every policy rule
    # as JSON, so it is roughly ten times the size of the payment that produced
    # it. Bounding payments per run is really bounding audit volume.
    demo_max_payments_per_minute: int = Field(default=150)
    demo_max_duration_seconds: int = Field(default=120)
    # Refuse to start once this many synthetic payments already exist. Caps the
    # total, which per-run limits alone cannot: without it, back-to-back runs
    # accumulate without bound over an unattended weekend.
    demo_max_synthetic_payments: int = Field(default=50_000)
    # Paces runs globally rather than per IP. Only one simulation can run at a
    # time anyway (see `simulation.engine.start_simulation`), so the contended
    # resource is global -- and a global limit cannot be evaded by arriving from
    # another address, nor spoofed through a forwarded-for header.
    demo_simulation_cooldown_seconds: int = Field(default=30)

    # Comma-separated browser origins allowed to call this API cross-origin,
    # e.g. the GitHub Pages URL the frontend is deployed to.
    cors_allow_origins: str = Field(default="http://localhost:5173")

    @property
    def cors_allow_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

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
    def is_local_env(self) -> bool:
        return self.app_env.lower() in {"dev", "test", "local"}

    @property
    def open_demo_active(self) -> bool:
        """True only for an explicitly-declared open demo in Razorpay test mode.

        Tying it to `razorpay_mode` means the flag cannot be carried into a live
        deployment by copying an environment file: the moment the mode changes,
        the control plane goes back to demanding a key.
        """
        return self.control_plane_open_demo and self.razorpay_mode == "test"

    @property
    def control_plane_auth_required(self) -> bool:
        return not self.is_local_env and not self.open_demo_active


@lru_cache
def get_settings() -> Settings:
    return Settings()

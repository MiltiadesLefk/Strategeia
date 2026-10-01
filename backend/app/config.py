from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

# What SEC EDGAR sees when no contact is configured. Made up on purpose:
# plan.md D13 (answered 2026-09-28) keeps it while the SEC traffic is two test
# tickers, and a separate contact address gets set before the full S&P 500
# backtest download. `.example` is a reserved domain (RFC 2606), so this can
# never be anybody's real inbox.
SEC_EDGAR_PLACEHOLDER_USER_AGENT = "Strategeia/1.0 (personal paper-trading research; contact@strategeia.example)"

# A web address in the user-agent is what turns EDGAR's 200 into a 403 (see
# InfraSettings.sec_edgar_user_agent). Email addresses are cut out before the
# check: "you@example.com" is exactly the contact the SEC asks for, and its
# domain half must not read as a link. Each alternative matches the whole
# fragment so the warning can quote it: a scheme (http://, https://), a www.
# name, or a bare domain with or without a path (github.com/someone/repo).
_EMAIL_ADDRESS_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_WEB_ADDRESS_RE = re.compile(
    r"\S*://\S*|\bwww\.\S*|\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b(?:/\S*)?",
    re.IGNORECASE,
)


class InfraSettings(BaseSettings):
    """Static, process-lifetime config from .env — never editable at runtime."""

    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173"
    # Auth gate in front of every API route (see api/deps.py's
    # require_shared_secret) — a matching `X-API-Key` header is required on
    # every request once this is non-empty. Leave EMPTY here: an empty
    # field is not "no auth", it means "let get_infra_settings() decide" —
    # see _load_or_create_shared_secret below, which auto-generates and
    # persists a real secret into runtime/ the first time this is empty AND
    # allow_unauthenticated_api is false. Set this explicitly (env var or
    # .env) to pin a specific value instead — e.g. to share one secret
    # between the backend and a build-time frontend config, which a
    # freshly-generated one can't do without a manual copy step. This is
    # not a substitute for the reverse-proxy/TLS setup TODO.md's "Before
    # running this on a public VPS 24/7" section calls for — it's what
    # stops casual unauthenticated abuse (draining a metered LLM/Finnhub
    # key via /api/trade-plans/generate or /api/scan/auto-trade,
    # overwriting Settings to redirect Telegram notifications, wiping the
    # portfolio via /api/portfolio/reset) once traffic reaches the port.
    api_shared_secret: str = ""
    # The explicit opt-out TODO.md called for: "keep an explicit opt-out
    # for anyone who genuinely wants an open port on localhost." Set to
    # true (backend/.env.example does this for local dev) to keep every
    # endpoint open with zero friction, exactly like before this existed —
    # NEVER set this for anything reachable beyond localhost/a private
    # network. Distinguishing "deliberately open" from "just never
    # configured" needs its own flag: api_shared_secret alone can't tell
    # the two apart once it's a string defaulting to "".
    allow_unauthenticated_api: bool = False
    # SEC EDGAR's fair-access policy requires a User-Agent that identifies the
    # requester with a contact address, and it rejects strings containing a URL
    # (verified: any UA with "github.com" in it returns 403 where the same
    # request with an email-shaped contact returns 200). The default is a
    # neutral placeholder deliberately (SEC_EDGAR_PLACEHOLDER_USER_AGENT
    # above). To send your own contact, set SEC_EDGAR_USER_AGENT in
    # backend/.env for a local run, or in the gitignored .env next to
    # docker-compose.yml, which passes it into the container (plan.md F-7).
    # Form: "Name/1.0 (purpose; you@example.com)" — plain ASCII, no link.
    sec_edgar_user_agent: str = SEC_EDGAR_PLACEHOLDER_USER_AGENT

    @field_validator("sec_edgar_user_agent")
    @classmethod
    def _sec_user_agent_or_placeholder(cls, value: str) -> str:
        """docker-compose.yml passes SEC_EDGAR_USER_AGENT=${SEC_EDGAR_USER_AGENT:-},
        so a deployment that never set it still delivers the variable, as "".
        pydantic-settings reads a variable set to "" as the value "", not as
        "absent, use the default" (the same trap notes/Decisions.md records
        for AUTH_USERNAME), and EDGAR refuses requests that don't say who sent
        them — insider scoring would quietly drop to zero. Blank therefore
        means the placeholder.

        Runs of whitespace, line breaks included, collapse to one space, and a
        value that still can't travel as an HTTP header falls back to the
        placeholder, loudly: http.client raises ValueError on a line break and
        UnicodeEncodeError on anything outside Latin-1 (a name in Greek
        letters, say). sec_edgar_provider._fetch converts network errors only,
        so either would escape the provider chain and fail every trade-plan
        evaluation of a US stock, not just cost the insider signal. Printable
        ASCII is the bar (RFC 9110 has senders keep header values to ASCII).

        A web address is only warned about, never replaced: the operator's own
        contact is still the right thing to send, and the check is a pattern
        that can misfire. The placeholder gets no warning at all — keeping it
        for now is a deliberate choice (plan.md D13), and a warning on every
        start would teach whoever reads the logs to skip warnings."""
        value = " ".join(value.split())
        if not value:
            return SEC_EDGAR_PLACEHOLDER_USER_AGENT
        if not (value.isascii() and value.isprintable()):
            # The value itself isn't logged: it's someone's name and address.
            logger.warning(
                "SEC_EDGAR_USER_AGENT has characters an HTTP header can't carry (only "
                "plain printable ASCII is safe; Greek or accented letters are not), so "
                "every SEC EDGAR request would fail. Using the built-in placeholder "
                "instead. Rewrite it in plain ASCII, as "
                "'Name/1.0 (purpose; you@example.com)'."
            )
            return SEC_EDGAR_PLACEHOLDER_USER_AGENT
        web_address = _WEB_ADDRESS_RE.search(_EMAIL_ADDRESS_RE.sub(" ", value))
        if web_address:
            logger.warning(
                "SEC_EDGAR_USER_AGENT contains what looks like a web address (%r). SEC "
                "EDGAR answers 403 to any user-agent with a link in it, which switches "
                "insider-trade scoring off. Use the form "
                "'Name/1.0 (purpose; you@example.com)': an email address is fine, a "
                "link is not.",
                web_address.group(0).strip("()[]<>{};,+\"'"),
            )
        return value

    # Deliberately separate from data/ (which holds the bundled, read-only
    # sp500.csv baked into the Docker image) so a single volume mount at
    # runtime/ can persist the db + settings without hiding sp500.csv.
    db_path: str = "runtime/strategeia.db"
    settings_path: str = "runtime/settings.json"
    # Keep fetched market data on disk (runtime/cache.db) so a restart or
    # redeploy doesn't empty the provider cache and refetch everything from
    # Yahoo (see data_providers/cache_store.py). Env: PERSIST_CACHE_DB. Set it
    # false for a memory-only cache. The price-history store's file
    # (runtime/history.db, see data_providers/history_store.py) sits next to
    # it and is not affected by this switch: it is data you asked to keep.
    persist_cache_db: bool = True

    # The ONE account that logs into this dashboard (api/routers/auth.py) —
    # no signup, no other users, by explicit design: "for now, create ONLY
    # ONE user, mine ... so no sign up, so no one can use it." Unlike
    # auth_password below, a username isn't a secret (it's not auto-generated
    # or logged specially, and it isn't what a stolen .env would actually
    # need to guard) — "admin" is a fine default until you set your own via
    # AUTH_USERNAME. Still checked with secrets.compare_digest alongside the
    # password purely for consistency, not because it needs to be.
    auth_username: str = "admin"
    # Set your own via AUTH_PASSWORD; left unset, one is auto-generated and
    # logged loudly at startup the same way api_shared_secret is, so a
    # forgotten password still leaves the dashboard reachable rather than
    # permanently locking you out — but the expected flow is you set this
    # yourself. Compared with secrets.compare_digest, same as
    # api_shared_secret, and for the same reason (constant-time).
    auth_password: str = ""
    # HMAC key that signs the session cookie a successful login issues (see
    # auth.py's create_session_token/verify_session_token). Auto-generated
    # and persisted to runtime/ exactly like api_shared_secret, but never
    # logged loudly — unlike the API secret and the password, nobody ever
    # needs to type this anywhere; it's pure internal plumbing, and a fresh
    # one on first boot just means any session issued before that boot
    # stops verifying (nobody is silently locked into a broken state, they
    # just see the login screen again).
    session_secret: str = ""
    # "also make maximum logins tries 3": after this many wrong passwords
    # from one client, further attempts are refused for
    # login_lockout_minutes regardless of whether the next guess would have
    # been correct — see auth.py's _LoginAttempts. Per-client (best-effort
    # by IP, see auth.py), not global, so one attacker hammering the login
    # can't lock the real operator out of their own dashboard.
    max_login_attempts: int = 3
    login_lockout_minutes: int = 15
    # How long a successful login stays valid before the browser has to log
    # in again. A personal dashboard checked periodically, not a banking
    # app — long enough to not be annoying, short enough that a device left
    # logged in somewhere doesn't stay valid indefinitely.
    session_lifetime_days: int = 7

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_file(self) -> Path:
        return BASE_DIR / self.db_path

    @property
    def settings_file(self) -> Path:
        return BASE_DIR / self.settings_path

    @property
    def cache_db_file(self) -> Path:
        """Beside settings.json, so it lives in the same volume as the db."""
        return self.settings_file.parent / "cache.db"

    @property
    def history_db_file(self) -> Path:
        return self.settings_file.parent / "history.db"

    @property
    def universe_file(self) -> Path:
        """The watchlist saved from Settings: beside settings.json, in the same
        volume, so it survives a Docker rebuild (data_providers/universe_store.py)."""
        return self.settings_file.parent / "universe.json"

    @property
    def generated_secret_file(self) -> Path:
        return self.settings_file.parent / "api_key.txt"

    @property
    def generated_session_secret_file(self) -> Path:
        return self.settings_file.parent / "session_secret.txt"

    @property
    def generated_password_file(self) -> Path:
        return self.settings_file.parent / "auth_password.txt"


# Idea credited to OpenTerminal (github.com/ErTasselli/OpenTerminal), reviewed
# 2026-09-12 — see TODO.md/notes/Decisions.md. The problem it fixes: a guard
# that only activates once you know to turn it on makes the INSECURE state
# the default one. This makes the SECURE state the default instead, at zero
# cost to anyone who hasn't touched auth config yet — the first request
# after a fresh install still succeeds, it just needs whatever this
# generates and persists into runtime/.
def _load_or_create_secret(secret_file: Path) -> str:
    """Shared plumbing behind all three auto-generated secrets
    (api_shared_secret, auth_password, session_secret) — read an existing
    value back if one is already on disk (so a restart doesn't invalidate
    a secret something else was already built/logged in against), else
    generate, persist, and best-effort-chmod a fresh one. Callers own
    deciding WHETHER to call this (only when nothing was explicitly
    configured) and what, if anything, to log about it — this function
    itself never logs, since the right message differs a lot between "an
    operator needs to copy this somewhere" (the API secret, the password)
    and "pure internal plumbing, silence is correct" (the session
    secret)."""
    if secret_file.exists():
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
        # An empty file (a previous run failed mid-write, or someone
        # truncated it by hand) is treated the same as "absent" below —
        # fall through and regenerate rather than authenticating against
        # a value nothing could ever match.

    secret = secrets.token_urlsafe(32)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(secret, encoding="utf-8")
    try:
        # Best-effort: POSIX only (a real VPS), silently skipped on
        # Windows dev machines where chmod is a no-op anyway.
        secret_file.chmod(0o600)
    except OSError:
        pass
    return secret


# logger.warning is what makes a message land in `docker logs`/console with
# zero logging setup required — Python's logging module ships a "handler of
# last resort" that prints WARNING+ to stderr when nothing else is
# configured (confirmed: this codebase has no logging.basicConfig anywhere
# and scheduler.py already relies on the same mechanism for its bare
# logger.exception calls). logger.info would NOT reliably show up the same
# way, so both loud messages below use WARNING even though neither is
# really a warning.


def _load_or_create_shared_secret(infra: InfraSettings) -> str:
    """Only ever called when api_shared_secret is empty AND
    allow_unauthenticated_api is false — an explicit value in .env/the
    environment always wins and this is never reached."""
    secret_file = infra.generated_secret_file
    was_missing = not (secret_file.exists() and secret_file.read_text(encoding="utf-8").strip())
    secret = _load_or_create_secret(secret_file)
    if was_missing:
        logger.warning(
            "\n"
            + "=" * 78
            + "\nNo API_SHARED_SECRET configured — generated one and saved it to:\n"
            + f"  {secret_file}\n"
            + "\nEvery API request now requires this as an X-API-Key header. The bundled\n"
            + "frontend needs the SAME value baked in at build time as VITE_API_SHARED_SECRET\n"
            + "(see README) — a value generated here after the frontend was already built\n"
            + "will lock it out with 401s until it's rebuilt with this value.\n"
            + "\nTo pin a specific value instead: set API_SHARED_SECRET yourself before\n"
            + "starting the backend. To disable this guard entirely for local-only use, set\n"
            + "ALLOW_UNAUTHENTICATED_API=true (see backend/.env.example) — never do that for\n"
            + "anything reachable beyond localhost.\n"
            + "=" * 78
        )
    return secret


def _load_or_create_auth_password(infra: InfraSettings) -> str:
    """Only ever called when auth_password is empty — an explicit value in
    .env/the environment always wins. Unlike the API secret, there is no
    opt-out for this one: "for now, create ONLY ONE user, mine ... so no
    sign up, so no one can use it" means a login screen always exists, so
    a password always has to exist for it to check against."""
    secret_file = infra.generated_password_file
    was_missing = not (secret_file.exists() and secret_file.read_text(encoding="utf-8").strip())
    password = _load_or_create_secret(secret_file)
    if was_missing:
        logger.warning(
            "\n"
            + "=" * 78
            + "\nNo AUTH_PASSWORD configured — generated one and saved it to:\n"
            + f"  {secret_file}\n"
            + "\nThis is the password for the dashboard's login screen (POST /api/auth/login),\n"
            + "not the API key above — they're two separate secrets. Read the file to log in,\n"
            + "or set AUTH_PASSWORD yourself in .env to choose your own instead.\n"
            + "=" * 78
        )
    return password


def _load_or_create_session_secret(infra: InfraSettings) -> str:
    """Only ever called when session_secret is empty. Deliberately silent
    (see the module comment above) — nobody ever needs to type this
    anywhere, it only signs the session cookie a login issues."""
    return _load_or_create_secret(infra.generated_session_secret_file)


LlmProviderName = Literal["none", "claude_code_cli", "openrouter", "orcarouter", "openai", "gemini"]

# The model the Claude Code CLI provider asks for with `--model`. The value
# ends up in an argv list (never a shell), but it is validated strictly anyway:
# it must start with a letter or digit (so it can never be read as another
# flag) and may then use only the characters real model names use: letters,
# digits and . _ - : @ [ ] (aliases like "sonnet", full ids like
# "claude-opus-4-1-20250805", and context-size suffixes like "sonnet[1m]").
# Blank is allowed and means "do not pass --model; use whatever the CLI is
# set to", which is the unpinned behaviour.
CLAUDE_CLI_MODEL_MAX_LENGTH = 64
CLAUDE_CLI_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@\[\]-]*$")


def normalize_claude_cli_model(value: str) -> str:
    """Trim and validate a Claude CLI model name; "" is a valid value (unpinned).
    Raises ValueError for anything that is not a plain model name."""
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) > CLAUDE_CLI_MODEL_MAX_LENGTH:
        raise ValueError(f"Claude model name must be at most {CLAUDE_CLI_MODEL_MAX_LENGTH} characters")
    if not CLAUDE_CLI_MODEL_PATTERN.fullmatch(cleaned):
        raise ValueError(
            "Claude model name may only use letters, digits and . _ - : @ [ ] "
            "and must start with a letter or digit (e.g. sonnet, opus, claude-sonnet-5-5)"
        )
    return cleaned


# Model ids for the HTTP providers (OpenRouter, OrcaRouter, OpenAI, Gemini), used
# for every decision_model field and the request-time check of the routine
# ones. A model id goes into a JSON body (OpenAI-style APIs) or a URL path
# (Gemini), so the same strict start-with-a-letter-or-digit rule applies;
# gateways also use "vendor/model" ids ("anthropic/claude-3.5-haiku",
# "orcarouter/auto"), so "/" is allowed except for Gemini, whose id is
# interpolated into a URL path and so can never contain one.
API_MODEL_MAX_LENGTH = 96
API_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@\[\]/-]*$")
API_MODEL_NO_SLASH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@\[\]-]*$")


def normalize_api_model(value: str, *, allow_slash: bool = True) -> str:
    """Trim and validate an HTTP provider's model id; "" is a valid value for a
    decision_model field (it means "use the routine model"). Raises
    ValueError for anything that is not a plain model id."""
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) > API_MODEL_MAX_LENGTH:
        raise ValueError(f"Model name must be at most {API_MODEL_MAX_LENGTH} characters")
    pattern = API_MODEL_PATTERN if allow_slash else API_MODEL_NO_SLASH_PATTERN
    if not pattern.fullmatch(cleaned):
        slash = "/ " if allow_slash else ""
        raise ValueError(
            f"Model name may only use letters, digits and . _ - : @ [ ] {slash}"
            "and must start with a letter or digit (e.g. gpt-4o-mini, anthropic/claude-3.5-haiku)"
        )
    return cleaned

# What happens when the AI Trading Overlay says it would not take the trade.
# One choice, not a set of flags: "cancel" and "hold" fire on the identical
# trigger and cancel always wins (a cancelled evaluation never reaches the
# auto-execute step), so as two independent booleans one combination was
# always dead and the UI could show a setting as ON that could never once
# fire. See notes/Decisions.md.
AiOverlayObjectionAction = Literal["cancel", "hold", "none"]

# Whether research-purpose AI calls may search the web (research_mode setting).
ResearchMode = Literal["our_data_only", "allow_web_search"]

# What a watcher event does once it is recorded: nothing more ("record"), send a
# Telegram alert ("alert"), or alert and also run a full evaluation of the symbol
# ("alert_and_reevaluate").
WatchersAction = Literal["record", "alert", "alert_and_reevaluate"]


MASK_BULLET_COUNT = 16


def _mask_secret(value: str) -> str:
    """"" when unset; otherwise a masked hint that reveals at most the last
    4 characters — enough to recognize "yes, that's the right key", never
    enough to reconstruct it. Always the same number of bullets regardless of
    the real key's length, so the UI can render a stable-looking masked
    field without also leaking how long the stored secret is."""
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * MASK_BULLET_COUNT
    return f"{'•' * MASK_BULLET_COUNT}{value[-4:]}"


class AppSettings(BaseModel):
    """Runtime-editable config — provider choice, API keys, paper account.

    Persisted to runtime/settings.json (gitignored). Contains secrets: never
    log this model's contents, never echo api keys back in API responses.
    """

    llm_provider: LlmProviderName = "none"
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-3.5-haiku"
    orcarouter_api_key: str = ""
    orcarouter_model: str = "orcarouter/auto"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"

    # Model the Claude Code CLI provider is pinned to (`claude -p --model X`).
    # Without a pin the CLI answers with whichever model was last picked in an
    # interactive session (or the account default), so narratives and the AI
    # overlay's objections would silently change model between runs, an
    # invisible variable in a system whose decisions are meant to be measured.
    # "sonnet" is the CLI's own alias for the latest Sonnet: stable across
    # releases (no dated id to go stale) and the cost/quality middle ground.
    # It still moves when a newer Sonnet ships; use a full id for an exact
    # version. Blank means "do not pin" (whatever the CLI is set to).
    claude_cli_model: str = "sonnet"

    @field_validator("claude_cli_model", mode="before")
    @classmethod
    def _validate_claude_cli_model(cls, value):
        return normalize_claude_cli_model(value) if isinstance(value, str) else value

    # Two model tiers per provider. The routine model (the *_model fields above)
    # writes the narratives: chart insight, research summary, the trade-plan
    # take. The decision model answers the one call whose output can stop a
    # trade, the AI Trading Overlay's verdict, so a stronger model can be spent
    # there without paying for it on every paragraph of prose. BLANK means "use
    # the routine model", and blank is the default for every provider, so an
    # existing install behaves exactly as before until the user opts in (a
    # non-blank default would also change the overlay's verdicts, and with them
    # the strategy version, on upgrade). Which model answers is the only thing
    # this changes: the overlay still can only stop a trade, never start one.
    claude_cli_decision_model: str = ""
    openrouter_decision_model: str = ""
    orcarouter_decision_model: str = ""
    openai_decision_model: str = ""
    gemini_decision_model: str = ""

    @field_validator("claude_cli_decision_model", mode="before")
    @classmethod
    def _validate_claude_cli_decision_model(cls, value):
        return normalize_claude_cli_model(value) if isinstance(value, str) else value

    @field_validator("openrouter_decision_model", "orcarouter_decision_model", "openai_decision_model", mode="before")
    @classmethod
    def _validate_api_decision_model(cls, value):
        return normalize_api_model(value) if isinstance(value, str) else value

    @field_validator("gemini_decision_model", mode="before")
    @classmethod
    def _validate_gemini_decision_model(cls, value):
        return normalize_api_model(value, allow_slash=False) if isinstance(value, str) else value

    def effective_decision_model(self) -> str:
        """The model that answers the AI overlay's verdict for the selected
        provider: its decision_model if one is set, else its routine model.
        "" for the 'none' provider, and for the Claude CLI when neither is set
        (the CLI then uses its own default)."""
        routine, decision = {
            "claude_code_cli": (self.claude_cli_model, self.claude_cli_decision_model),
            "openrouter": (self.openrouter_model, self.openrouter_decision_model),
            "orcarouter": (self.orcarouter_model, self.orcarouter_decision_model),
            "openai": (self.openai_model, self.openai_decision_model),
            "gemini": (self.gemini_model, self.gemini_decision_model),
        }.get(self.llm_provider, ("", ""))
        return decision or routine

    finnhub_enabled: bool = False
    finnhub_api_key: str = ""

    # Master switch, opt-in and off by default: when on, generate_trade_plan
    # asks the LLM for its own independent read of ALL the same raw data
    # (technicals, fundamentals, news, earnings) — stored as ai_opinion_* on
    # the trade plan. Costs one extra LLM call per symbol evaluated,
    # including ones the rule-based engine rejects. With this off, neither
    # ai_overlay_* setting below does anything. See notes/Decisions.md.
    ai_trading_overlay_enabled: bool = False

    # Two independent questions about how much the overlay's opinion counts,
    # both meaningful only when ai_trading_overlay_enabled is also on — off,
    # neither changes anything, so a default install behaves exactly as it
    # always has. Both default to their active setting because turning the
    # overlay on at all is itself opt-in: the reasonable assumption is you
    # wanted the second opinion to matter. Neither lets the overlay
    # ORIGINATE a trade — direction still comes from the rule-based trend and
    # nowhere else, and the score contribution is one-directional (a penalty,
    # never a bonus). See analysis/ai_overlay_scoring.py and
    # notes/Decisions.md.

    # (1) Does an objection cost confidence points? The overlay becomes a
    # scored dimension like every other confluence check (0 to -3, scaled by
    # the model's own stated conviction) instead of sitting in a box next to
    # a number it couldn't touch. A marginal plan can fall under
    # min_confidence_for_trade this way, so this alone can reach the
    # trade/no-trade decision — independently of the action below.
    ai_overlay_scores_confidence: bool = True

    # What an objection actually DOES, as a single three-way choice:
    #   "cancel" — the evaluation becomes an explicit no_trade decision with
    #              the overlay named as the reason, instead of a plan.
    #   "hold"   — the plan is written normally, but auto-execute leaves it
    #              "pending" for manual review instead of opening the
    #              position. The one moment a second opinion is worth having
    #              is the moment before capital commits.
    #   "none"   — the objection is recorded and scored but stops nothing.
    #              Useful for measuring whether the overlay's objections
    #              actually correlate with losing trades before giving it
    #              stopping power.
    # These were two booleans (ai_overlay_vetoes_trade /
    # ai_overlay_blocks_auto_execute) and should not have been: they fire on
    # the same trigger and cancel strictly wins, so "both on" was
    # indistinguishable from "cancel" and the UI showed a live-looking
    # toggle that could never fire. Old settings.json files are migrated in
    # _migrate_overlay_objection_action below.
    ai_overlay_objection_action: AiOverlayObjectionAction = "cancel"

    # The confidence bar a setup must clear to become a tradeable plan at
    # all; below it (or on a Neutral trend) the evaluation is persisted as an
    # explicit no_trade decision instead. Was a hardcoded constant in
    # trade_plan_service.py.
    #
    # The default is 30, not the 40 that constant held, and the change is a
    # rescale rather than a loosening. Confidence used to be squeezed onto a
    # 20-90 range; it is now the honest 0-100 percentage of achievable
    # evidence points a setup actually earned. 40 on the old range and 30 on
    # the new one are the same cutoff: 5 of the 16 achievable points. Keeping
    # the literal 40 would have silently tightened the bar to 7 points and
    # rejected setups the app has been trading all along.
    min_confidence_for_trade: int = 30

    # Whether AI calls made for RESEARCH (background pages a person reads) may
    # use the provider's web search. "our_data_only" (default) keeps every call
    # to the data the app already holds. The AI overlay and the narration never
    # use the web in either mode, and this setting is not part of the strategy
    # version: research never decides a trade.
    research_mode: ResearchMode = "our_data_only"

    @model_validator(mode="before")
    @classmethod
    def _migrate_overlay_objection_action(cls, data):
        """Carry a settings.json written before ai_overlay_objection_action
        existed onto the new field. Without this, pydantic silently drops
        the two retired booleans (BaseModel ignores extras) and anyone who
        had deliberately turned the veto off would find it back on after an
        upgrade — a real behaviour change, applied invisibly."""
        if not isinstance(data, dict) or data.get("ai_overlay_objection_action") is not None:
            return data
        vetoes = data.get("ai_overlay_vetoes_trade")
        holds = data.get("ai_overlay_blocks_auto_execute")
        if vetoes is None and holds is None:
            return data
        data = dict(data)
        data["ai_overlay_objection_action"] = "cancel" if vetoes else "hold" if holds else "none"
        return data

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    scan_universe_size: int = 50

    # News cards: an AI (the configured provider's routine model) labels each
    # archived headline once with an event type, sentiment and materiality. Off
    # by default because it spends AI calls. The labels are shown on the News tab
    # and recorded as a silent signal on plans; they never change a score today.
    # news_card_batch_limit is the most headlines labelled per run (a run is one
    # button press); AI calls per run are separately capped at three.
    news_cards_enabled: bool = False
    news_card_batch_limit: int = 20
    paper_starting_cash: float = 100_000.0
    default_risk_pct: float = 1.0
    mark_to_market_interval_minutes: int = 15

    # Execution realism. A paper fill that always lands exactly on the stop
    # is the single easiest way to make a strategy look better than it is:
    # real stops are market orders that fill at whatever the tape offers
    # after the trigger, which on a gap is materially worse. The engine
    # models the gap itself unconditionally (see PaperTradingEngine._fill_price);
    # these two knobs cover the rest of the cost of doing business.
    # slippage_bps applies only to MARKET fills (entries and stop exits),
    # never to take-profit limit fills. 5bps is a modest, defensible default
    # for liquid large-caps — raise it if you trade thinner names.
    slippage_bps: float = 5.0
    commission_per_trade: float = 0.0
    auto_execute_trade_plans: bool = True

    # Unattended scan -> generate -> execute loop. Off by default — unlike
    # auto-execute (which only acts on a plan you already asked for), this
    # decides *which* symbols to trade with no human in the loop at all, so
    # it opts in rather than opting out. Runs 3x/day at fixed session-open
    # times (see scheduler.py's AUTO_SCAN_SESSION_TIMES_UTC), not on an
    # interval — no `_interval_minutes` setting to configure here.
    auto_scan_enabled: bool = False
    max_concurrent_positions: int = 5

    # Watchers: small background pollers (a new filing, a headline) that report
    # events. Master switch off by default: no real watcher ships yet, and a
    # watcher that alerts or re-evaluates on its own is something to opt into.
    # An event is always recorded first; watchers_action says what happens next.
    # The default also runs a full evaluation of the symbol, which goes through the
    # same gates as any other (and is queued for the next open when the market is
    # closed). watchers_poll_minutes is how often the scheduler wakes to see which
    # watchers are due; each watcher has its own, slower, interval too.
    watchers_enabled: bool = False
    watchers_action: WatchersAction = "alert_and_reevaluate"
    watchers_poll_minutes: int = 5

    # Portfolio-level risk, as opposed to the per-trade risk default_risk_pct
    # already covers. Five 1%-risk positions is only "5% at risk" if the five
    # are independent — five semis on the same tape is one 5% bet. max_positions
    # _per_sector bounds that; max_position_pct_of_adv bounds the other
    # direction (a size you could not actually fill without moving the book).
    max_positions_per_sector: int = 2
    max_position_pct_of_adv: float = 1.0

    # A third way out after the stop and TP1: a position that has been open this
    # many TRADING days (bars, so weekends and holidays don't count) without
    # touching either is closed at that day's close. Plans are labelled "1-4
    # weeks", so 20 trading days (four weeks) is the top of the plan's own
    # horizon: a trade still unresolved after it has outlived the thesis it was
    # sized for and is only holding a slot and a sector cap hostage. 0 turns the
    # limit off (positions then close only at the stop, TP1 or by hand). The
    # upper bound (SettingsUpdateRequest) keeps the limit inside the 3-month
    # window the exit scan reads, since the scan must see the position's entry
    # bar to count days.
    max_holding_days: int = 20

    def redacted(self) -> dict:
        """Copy safe to return over the API — secrets collapsed to a masked
        hint (e.g. "••••ab12") so the UI can show *that* a key is set and
        confirm it's the right one, without ever echoing the real value."""
        data = self.model_dump()
        for key in (
            "openrouter_api_key",
            "orcarouter_api_key",
            "openai_api_key",
            "gemini_api_key",
            "finnhub_api_key",
            "telegram_bot_token",
        ):
            data[key] = _mask_secret(data[key])
        return data


_lock = threading.Lock()
_settings_cache: AppSettings | None = None


@lru_cache
def get_infra_settings() -> InfraSettings:
    """@lru_cache makes this a process-lifetime singleton, which is exactly
    what the three _load_or_create_* calls below need: each runs (and, on a
    fresh install, writes to disk and maybe logs) at most once per process,
    the first time anything asks for InfraSettings — never on every
    request, since every caller (api/deps.py, api/routers/auth.py,
    database.py, main.py, sec_edgar_provider.py) goes through this same
    cached function."""
    infra = InfraSettings()
    # allow_unauthenticated_api skips ALL of this, not just the API key:
    # require_auth returns immediately in that mode without checking a
    # password or a session cookie either, so generating and loudly
    # logging either one would just be noise about a credential nothing
    # will ever check.
    if not infra.allow_unauthenticated_api:
        if not infra.api_shared_secret:
            infra.api_shared_secret = _load_or_create_shared_secret(infra)
        if not infra.auth_password:
            infra.auth_password = _load_or_create_auth_password(infra)
        if not infra.session_secret:
            infra.session_secret = _load_or_create_session_secret(infra)
    return infra


def _settings_file() -> Path:
    return get_infra_settings().settings_file


def load_app_settings() -> AppSettings:
    global _settings_cache
    with _lock:
        if _settings_cache is not None:
            return _settings_cache
        path = _settings_file()
        if path.exists():
            _settings_cache = AppSettings.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            _settings_cache = AppSettings()
        return _settings_cache


def save_app_settings(settings: AppSettings) -> None:
    global _settings_cache
    with _lock:
        path = _settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings.model_dump(), indent=2), encoding="utf-8")
        _settings_cache = settings


def update_app_settings(**changes) -> AppSettings:
    current = load_app_settings()
    updated = current.model_copy(update=changes)
    save_app_settings(updated)
    return updated

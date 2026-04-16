from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError
from mcp.server.auth.provider import AccessToken, TokenVerifier

from bridge_server.config import BridgeSettings


logger = logging.getLogger(__name__)


def _normalize_issuer_url(issuer_url: str) -> str:
    return issuer_url.rstrip("/") + "/"


def _jwks_url_from_issuer(issuer_url: str) -> str:
    normalized = _normalize_issuer_url(issuer_url)
    return f"{normalized}.well-known/jwks.json"


def _extract_scopes(payload: dict[str, Any]) -> list[str]:
    scopes: set[str] = set()

    scope_claim = payload.get("scope")
    if isinstance(scope_claim, str):
        scopes.update(part for part in scope_claim.split() if part)

    permissions_claim = payload.get("permissions")
    if isinstance(permissions_claim, list):
        scopes.update(item for item in permissions_claim if isinstance(item, str) and item)

    return sorted(scopes)


def _extract_client_id(payload: dict[str, Any]) -> str:
    for key in ("azp", "client_id", "sub"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown-client"


@dataclass(slots=True)
class Auth0TokenVerifier(TokenVerifier):
    issuer_url: str
    audience: str
    algorithms: tuple[str, ...] = ("RS256",)
    _jwks_client: PyJWKClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        normalized_issuer = _normalize_issuer_url(self.issuer_url)
        object.__setattr__(self, "issuer_url", normalized_issuer)
        object.__setattr__(self, "_jwks_client", PyJWKClient(_jwks_url_from_issuer(normalized_issuer)))

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer_url,
            )
        except InvalidTokenError:
            logger.warning("mcp auth token verification failed", exc_info=True)
            return None
        except Exception:
            logger.exception("mcp auth verifier failed unexpectedly")
            return None

        expires_at = payload.get("exp")
        if expires_at is not None:
            try:
                expires_at = int(expires_at)
            except (TypeError, ValueError):
                expires_at = None

        audience_claim = payload.get("aud")
        resource: str | None = None
        if isinstance(audience_claim, str):
            resource = audience_claim
        elif isinstance(audience_claim, list):
            for item in audience_claim:
                if isinstance(item, str) and item:
                    resource = item
                    break

        return AccessToken(
            token=token,
            client_id=_extract_client_id(payload),
            scopes=_extract_scopes(payload),
            expires_at=expires_at,
            resource=resource,
        )


def build_mcp_auth_components(settings: BridgeSettings) -> tuple[Any, TokenVerifier] | tuple[None, None]:
    if not settings.mcp_auth_enabled:
        return None, None

    from mcp.server.auth.settings import AuthSettings

    auth_settings = AuthSettings(
        issuer_url=settings.mcp_auth_issuer_url,
        resource_server_url=settings.mcp_resource_server_url,
        required_scopes=list(settings.mcp_auth_required_scopes),
    )
    verifier = Auth0TokenVerifier(
        issuer_url=settings.mcp_auth_issuer_url or "",
        audience=settings.mcp_auth_audience or "",
    )
    return auth_settings, verifier

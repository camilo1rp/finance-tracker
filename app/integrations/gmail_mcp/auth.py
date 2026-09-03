from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from app.domain.email_source import EmailSourceUnavailable

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class TokenProvider(ABC):
    @abstractmethod
    def access_token(self) -> str:
        raise NotImplementedError


class StaticTokenProvider(TokenProvider):
    def __init__(self, token: str) -> None:
        self._token = token

    def access_token(self) -> str:
        return self._token


class RefreshTokenProvider(TokenProvider):
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        clock=None,
        post=None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._clock = clock if clock is not None else __import__("time").time
        self._post = post
        self._token: str | None = None
        self._expiry: float = 0.0

    def access_token(self) -> str:
        now = float(self._clock())
        if self._token is not None and now < self._expiry - 60:
            return self._token
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        try:
            if self._post is not None:
                response = self._post(GOOGLE_TOKEN_URL, data=data)
            else:
                response = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=20.0)
            response.raise_for_status()
            payload = response.json()
            token = payload["access_token"]
            expires_in = int(payload.get("expires_in") or 3600)
        except Exception as exc:
            raise EmailSourceUnavailable("auth") from exc
        self._token = str(token)
        self._expiry = now + expires_in
        return self._token

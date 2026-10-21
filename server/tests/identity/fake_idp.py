"""最小的假 OIDC 提供方：发现文档、授权（直接同意）、换令牌（校验 PKCE 与客户端凭证）、JWKS。

`overrides` 用来篡改签发的 ID Token 声明，`sign_with` 用来换签名方式，以覆盖各种失败路径。
"""

import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlencode

from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

ISSUER = "https://idp.test"
CLIENT_ID = "knowwork"
CLIENT_SECRET = "s3cret-client"
REDIRECT_URI = "https://kw.test/api/auth/oidc/callback"


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class FakeIdp:
    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
        self.subject = "sub-alice"
        self.email = "alice@example.com"
        self.claims_extra: dict = {"email_verified": True, "name": "Alice From IdP"}
        self.overrides: dict = {}
        self.sign_with: str = "RS256"
        self.codes: dict[str, dict] = {}

    def app(self) -> Starlette:
        return Starlette(routes=[
            Route("/.well-known/openid-configuration", self._discovery),
            Route("/authorize", self._authorize),
            Route("/token", self._token, methods=["POST"]),
            Route("/jwks", self._jwks),
        ])

    async def _discovery(self, _: Request):
        return JSONResponse({"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                             "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"})

    async def _jwks(self, _: Request):
        return JSONResponse({"keys": [self.key.as_dict(private=False)]})

    async def _authorize(self, request: Request):
        params = request.query_params
        assert params["client_id"] == CLIENT_ID and params["response_type"] == "code"
        assert params["code_challenge_method"] == "S256" and "openid" in params["scope"].split()
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"challenge": params["code_challenge"], "nonce": params["nonce"],
                            "redirect_uri": params["redirect_uri"]}
        return RedirectResponse(f"{params['redirect_uri']}?{urlencode({'code': code, 'state': params['state']})}")

    async def _token(self, request: Request):
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        grant = self.codes.pop(form.get("code", ""), None)
        if (request.headers.get("authorization") != expected or grant is None
                or _s256(form.get("code_verifier", "")) != grant["challenge"]
                or form.get("redirect_uri") != grant["redirect_uri"]):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse({"access_token": "at", "token_type": "Bearer", "id_token": self._id_token(grant)})

    def _id_token(self, grant: dict) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "sub": self.subject, "aud": CLIENT_ID, "iat": now, "exp": now + 300,
                  "nonce": grant["nonce"], "email": self.email, **self.claims_extra, **self.overrides}
        if self.sign_with == "HS256":
            return jwt.encode({"alg": "HS256", "kid": "k1"}, claims, OctKey.import_key(b"x" * 32))
        if self.sign_with == "rogue":
            return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, RSAKey.generate_key(2048))
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, self.key)

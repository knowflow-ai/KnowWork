"""原生应用登录的接口（挂在 /api/auth 下）：授权确认、换令牌、吊销、已登录的设备。

- 确认页是前端页面：它先 GET /oauth/authorize 核对请求（总是 200：客户端名字，或 error 与能不能带回应用），
  用户点允许或拒绝后 POST 同一地址，拿到要回到的应用地址自己跳过去。批准必须是浏览器里登录着的本人（Cookie 会话），令牌、API Key 都不行。
- /oauth/token、/oauth/revoke 给应用直接调：表单提交，出错照 RFC 6749 5.2 返回 {error, error_description}，
  带 Cache-Control: no-store。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..audit.context import annotate
from ..auth.principal import Principal
from .container import Identity
from .oauth import OAuthError
from .oauth_exchange import ACCESS_TOKEN_TYPE, TOKEN_EXCHANGE

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
BROWSER_ONLY = "请在浏览器里登录后再确认"


class AuthorizeBody(BaseModel):
    response_type: str = Field(default="", max_length=32)
    client_id: str = Field(default="", max_length=64)
    redirect_uri: str = Field(default="", max_length=300)
    code_challenge: str = Field(default="", max_length=128)
    code_challenge_method: str = Field(default="", max_length=16)
    state: str = Field(default="", max_length=500)
    device_name: str = Field(default="", max_length=200)
    device_id: str = Field(default="", max_length=64)
    approve: bool


def _check_failure(error: OAuthError) -> dict:
    """核对结果里的错误：redirectTo 有值时确认页把错误带回应用，没有时只在页面上说明。"""
    failure = {"code": error.error, "message": error.description}
    if error.redirect_to:
        failure["redirectTo"] = error.redirect_to
    return {"error": failure}


def _token_error(error: OAuthError) -> JSONResponse:
    return JSONResponse({"error": error.error, "error_description": error.description}, status_code=400,
                        headers=NO_STORE)


def _device_view(device) -> dict:
    return {"id": device.id, "clientId": device.client_id, "clientName": device.client_name,
            "deviceName": device.device_name, "deviceId": device.device_id, "createdAt": device.created_at.isoformat(),
            "lastUsedAt": device.last_used_at.isoformat()}


def add_oauth_routes(router: APIRouter, identity: Identity) -> None:
    oauth = identity.oauth
    Who = Annotated[Principal, Depends(identity.get_principal)]

    @router.get("/oauth/authorize")
    async def check_authorization(request: Request):
        """核对应用的登录请求（确认页用）"""
        try:
            checked = oauth.check(dict(request.query_params))
        except OAuthError as error:
            return _check_failure(error)
        return {"client": {"id": checked.client.client_id, "name": checked.client.name},
                "deviceName": checked.device_name}

    @router.post("/oauth/authorize")
    async def decide_authorization(body: AuthorizeBody, who: Who):
        """允许或拒绝应用登录"""
        if who.session_id is None:
            raise HTTPException(status_code=403, detail=BROWSER_ONLY)
        try:
            checked = oauth.check(body.model_dump(exclude={"approve"}))
        except OAuthError as error:
            raise HTTPException(status_code=400, detail={"code": error.error, "message": error.description}) from error
        annotate(actor_id=who.user_id, resource_type="oauth_client", resource_id=checked.client.client_id,
                 approved=body.approve)
        if not body.approve:
            return {"redirectTo": oauth.deny(checked)}
        return {"redirectTo": await oauth.approve(who.user_id, checked)}

    @router.post("/oauth/token")
    async def issue_token(request: Request):
        """应用换取或刷新令牌"""
        form = await request.form()
        field = lambda name: str(form.get(name) or "")
        try:
            grant_type = field("grant_type")
            if grant_type == "authorization_code":
                tokens = await oauth.exchange_code(field("client_id"), field("code"), field("redirect_uri"),
                                                   field("code_verifier"))
            elif grant_type == "refresh_token":
                tokens = await oauth.refresh(field("client_id"), field("refresh_token"))
            elif grant_type == TOKEN_EXCHANGE:
                return await exchange_token({key: str(value) for key, value in form.items()})
            else:
                raise OAuthError("unsupported_grant_type", "只支持授权码、刷新令牌与令牌交换")
        except OAuthError as error:
            return _token_error(error)
        return JSONResponse({"access_token": tokens.access_token, "token_type": "Bearer",
                             "expires_in": tokens.expires_in, "refresh_token": tokens.refresh_token},
                            headers=NO_STORE)

    async def exchange_token(params: dict[str, str]) -> JSONResponse:
        """RFC 8693：用这个应用的访问令牌换某个受众的短期令牌（受众由业务服务、扩展登记）。"""
        client_id = params.get("client_id", "")
        if identity.oauth_clients.get(client_id) is None:
            raise OAuthError("invalid_client", "未登记的客户端")
        if params.get("subject_token_type") != ACCESS_TOKEN_TYPE:
            raise OAuthError("invalid_request", "只能用访问令牌交换")
        principal = await oauth.subject(client_id, params.get("subject_token", ""))
        exchanger = identity.oauth_exchangers.get(params.get("audience", ""))
        if exchanger is None:
            raise OAuthError("invalid_target", "不认识的受众")
        issued = await exchanger(principal, params)
        annotate(actor_id=principal.user_id, resource_type="oauth_audience", resource_id=params.get("audience", ""))
        return JSONResponse({"access_token": issued.token, "issued_token_type": ACCESS_TOKEN_TYPE,
                             "token_type": "Bearer", "expires_in": issued.expires_in}, headers=NO_STORE)

    @router.post("/oauth/revoke")
    async def revoke_token(request: Request):
        """应用退出登录（吊销令牌）"""
        form = await request.form()
        client_id = str(form.get("client_id") or "")
        if identity.oauth_clients.get(client_id) is None:
            return _token_error(OAuthError("invalid_client", "未登记的客户端"))
        await oauth.revoke_token(client_id, str(form.get("token") or ""))
        return Response(status_code=200, headers=NO_STORE)

    @router.get("/devices")
    async def list_devices(who: Who):
        """我已登录的设备"""
        return [_device_view(device) for device in await oauth.list_devices(who.user_id)]

    @router.delete("/devices/{grant_id}", status_code=204)
    async def sign_out_device(grant_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")], who: Who):
        """让某台设备退出登录"""
        if not await oauth.revoke_device(who.user_id, grant_id):
            raise HTTPException(status_code=404, detail="没有这台设备的登录")
        annotate(actor_id=who.user_id, resource_type="oauth_grant", resource_id=grant_id)

"""IM 机器人认人：按组织来源的身份域，把平台给的用户 ID 对到本地用户。

先拿每个 ID 对主 ID，再按同名的键对别名（飞书 open_id、union_id 这类）；身份不在职、账号停用的算停用；
登记点由扩展登记来源，按平台列、按 id 取，提供方出错不拖垮别的。"""

import pytest

from knowwork_server.identity.im_identities import (
    INACTIVE,
    UNKNOWN,
    ImIdentities,
    ImIdentitySource,
    resolve,
)
from knowwork_server.identity.models import ExternalIdentity, User

pytestmark = pytest.mark.anyio
WECOM = "wecom:ww-corp"
FEISHU = "feishu:tenant-1"


async def _person(sessionmaker, user_id: str, *, status: str = "active") -> None:
    async with sessionmaker() as session, session.begin():
        session.add(User(id=user_id, email=f"{user_id}@example.com", name=user_id, status=status))


async def _identity(sessionmaker, user_id: str, realm: str, subject: str, *, aliases=None, active=True) -> None:
    async with sessionmaker() as session, session.begin():
        session.add(ExternalIdentity(realm=realm, subject=subject, user_id=user_id, aliases=aliases or {},
                                     active=active))


async def test_matches_the_main_id_within_the_realm(sessionmaker):
    await _person(sessionmaker, "u-zhang")
    await _identity(sessionmaker, "u-zhang", WECOM, "zhangsan")
    assert (await resolve(sessionmaker, WECOM, {"userid": "zhangsan"})).user_id == "u-zhang"
    # 同一个 userid 在别的企业（身份域）里不是他
    missed = await resolve(sessionmaker, "wecom:other-corp", {"userid": "zhangsan"})
    assert (missed.user_id, missed.reason) == (None, UNKNOWN)


async def test_falls_back_to_aliases_with_the_same_key(sessionmaker):
    # 飞书：机器人和组织同步不是同一个应用时 open_id 对不上，靠 union_id 对上
    await _person(sessionmaker, "u-li")
    await _identity(sessionmaker, "u-li", FEISHU, "user-li", aliases={"open_id": "ou_sync", "union_id": "on_li"})
    found = await resolve(sessionmaker, FEISHU, {"user_id": "", "open_id": "ou_bot", "union_id": "on_li"})
    assert found.user_id == "u-li"
    # 键名不同的别名不算：open_id 的值碰巧等于别人的 union_id 也不能认成他
    missed = await resolve(sessionmaker, FEISHU, {"open_id": "on_li"})
    assert missed.user_id is None


async def test_left_or_disabled_people_are_reported_inactive(sessionmaker):
    await _person(sessionmaker, "u-left")
    await _identity(sessionmaker, "u-left", WECOM, "left", active=False)  # 组织同步判了离职
    await _person(sessionmaker, "u-off", status="disabled")
    await _identity(sessionmaker, "u-off", WECOM, "off")
    for userid in ("left", "off"):
        found = await resolve(sessionmaker, WECOM, {"userid": userid})
        assert (found.user_id, found.reason) == (None, INACTIVE)


async def test_nothing_to_match_is_unknown(sessionmaker):
    found = await resolve(sessionmaker, WECOM, {"userid": ""})
    assert (found.user_id, found.reason) == (None, UNKNOWN)


async def test_sources_are_listed_by_platform_and_looked_up_by_id():
    identities = ImIdentities()
    wecom = ImIdentitySource(id="src-wecom", platform="wecom", name="示例科技", realm=WECOM,
                             login_url="/api/auth/sso/src-wecom/login")
    feishu = ImIdentitySource(id="src-feishu", platform="feishu", name="飞书", realm=FEISHU)

    async def provider():
        return [wecom, feishu]

    def broken():
        raise RuntimeError("组织来源读不出来")

    identities.add(broken)  # 一个提供方出错，别的照常列出
    identities.add(provider)
    assert await identities.list("wecom") == [wecom]
    assert await identities.list() == [wecom, feishu]
    assert await identities.get("src-feishu") == feishu
    assert await identities.get("missing") is None

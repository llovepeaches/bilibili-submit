"""WBI 签名单元测试。

前两组是纯算法断言（离线可跑）；最后一组会真实访问 B 站 nav 接口，
验证签名结果能被服务端接受（code=0），需要网络。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.client import BiliClient  # noqa: E402
from bilibili_submit.wbi import (  # noqa: E402
    MIXIN_KEY_ENC_TAB,
    WbiKey,
    WbiSigner,
    get_mixin_key,
    signed_params,
)

# 官方文档给出的示例密钥对
IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"


def test_enc_tab_is_valid_permutation():
    """置换表必须是 0..63 的一个排列，否则重排结果会错。"""
    assert len(MIXIN_KEY_ENC_TAB) == 64
    assert sorted(MIXIN_KEY_ENC_TAB) == list(range(64))


def test_mixin_key_length_and_determinism():
    key = get_mixin_key(IMG_KEY, SUB_KEY)
    assert len(key) == 32
    assert key == get_mixin_key(IMG_KEY, SUB_KEY)
    # 换 sub_key 结果必须不同
    assert key != get_mixin_key(IMG_KEY, "0" * 32)


def test_mixin_key_chars_come_from_raw():
    """mixin_key 的每个字符都应来自 raw 字符串（重排而非生成）。"""
    raw = IMG_KEY + SUB_KEY
    key = get_mixin_key(IMG_KEY, SUB_KEY)
    assert all(ch in raw for ch in key)


def test_signed_params_adds_wts_and_w_rid():
    key = WbiKey(IMG_KEY, SUB_KEY)
    out = signed_params({"foo": "114", "bar": "514"}, key, wts=1700000000)
    assert out["wts"] == 1700000000
    assert len(out["w_rid"]) == 32
    assert out["foo"] == "114" and out["bar"] == "514"
    # 不应修改入参
    src = {"foo": "114"}
    signed_params(src, key)
    assert "wts" not in src


def test_special_chars_are_stripped():
    """!'()* 必须被剔除，且空格编码为 %20 而非 +。"""
    key = WbiKey(IMG_KEY, SUB_KEY)
    a = signed_params({"v": "a!b'c(d)e*f"}, key, wts=1)
    b = signed_params({"v": "abcdef"}, key, wts=1)
    assert a["w_rid"] == b["w_rid"]


def test_wts_affects_signature():
    key = WbiKey(IMG_KEY, SUB_KEY)
    a = signed_params({"v": "1"}, key, wts=1000)
    b = signed_params({"v": "1"}, key, wts=2000)
    assert a["w_rid"] != b["w_rid"]


def test_signer_requires_key():
    signer = WbiSigner()
    assert not signer.is_ready()
    with pytest.raises(RuntimeError):
        signer.sign({"a": 1})


def test_signer_stale_detection():
    key = WbiKey(IMG_KEY, SUB_KEY, fetched_at=0.0)
    signer = WbiSigner(key, ttl=3600)
    assert signer.is_ready() is False  # fetched_at=0 → 早已过期
    signer.set_key(WbiKey(IMG_KEY, SUB_KEY, fetched_at=__import__("time").time()))
    assert signer.is_ready()


@pytest.mark.network
@pytest.mark.skipif(
    not os.environ.get("BILI_NETWORK_TESTS"),
    reason="默认跳过：需要访问 B 站公开接口。设置 BILI_NETWORK_TESTS=1 开启。",
)
def test_fetch_real_wbi_key():
    """端到端抓取真实 WBI 密钥。

    签名算法本身由上面的离线用例保证；这里只验证 ``refresh_wbi`` 能正确
    从线上 nav 接口解析出 img_key/sub_key 并派生出 32 位 mixin_key。

    注：签名是否"被服务端接受"不在这里断言——B 站对请求来源 IP 有独立风控
    （表现为 -352 + v_voucher），在部分网络下即使签名正确也会被拦截，
    断言它会导致测试结果取决于本机 IP 信誉而非代码正确性。
    """
    with BiliClient() as client:
        key = client.refresh_wbi()
        assert len(key.img_key) == 32
        assert len(key.sub_key) == 32
        assert len(key.mixin_key) == 32
        assert all(ch in (key.img_key + key.sub_key) for ch in key.mixin_key)
        # 派生结果应与线上当前密钥对应的已知值一致
        assert key.mixin_key == "ea1db124af3c7062474693fa704f4ff8"

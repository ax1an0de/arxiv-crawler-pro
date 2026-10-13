"""
app_license.py — 离线激活 / 设备绑定 / 一次一密（v0.14.0）

设计要点（v0.14.0 升级）：
  * 非对称加密：本模块只内嵌**公钥**用于验证；**私钥**由授权方（你）单独持有，
    因此"只有你能生成激活码"，应用本身无法伪造。
  * 设备指纹：采集稳定的硬件标识（MachineGuid / 主板序列 / BIOS 序列 / CPU ID / MAC）
    → SHA-256 → 32 位 device_id。同一台机器每次计算结果一致。
  * **一次一密（v0.14.0 新增）**：每张激活码 payload 内含一个全局唯一 serial
    （由生成器签发时生成，UUID4）。应用端在 %LOCALAPPDATA%/ArxivCrawler/used_serials.json
    中持久化已成功激活过的 serial 列表。**同一张激活码在任意机器上只能被激活一次**；
    **同一台设备多次签发的不同码，第一次成功后，再换码/重装软件激活同一设备也要
    消耗一张新码**——防一机多用户/重装蹭码。
  * 激活码 = base64(payload.json) + "." + base64(RSA 签名)，可带连字符分组展示。
    payload 内含 device_id + serial，因此激活码与设备绑定 + 一次性。
  * 授权状态持久化到 %LOCALAPPDATA%/ArxivCrawler/license.dat，
    每次启动重新校验（签名 + 设备绑定 + serial 未被使用过），不依赖网络。

注意：本文件名为 app_license.py（而非 license.py），以避免与 Python 标准库 license 模块冲突。
"""

import os
import re
import sys
import json
import base64
import hashlib
import subprocess

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# ---- 内嵌公钥（对应 C:/workbuddy_tools/ArxivCrawler_license_keys/ArxivCrawler_private_key.pem，NOT 私钥；密钥已迁出项目树）----
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAiUvUFRG1dO1lf02EX6So
H4pZnYe6XOzj7kx1mSdTrsbaq7a5IwoN/Tmst4f59ZcohBZY/NRVXkP64MC1HE5d
13SU4PyikqLf/9cbEcbZ4F995jbCI0o06NrJ1BJd1glK6rsFwn4l8/XjVvxRR2co
JDCRKaSphqsg8Dmr+ycHpyNWGHcmiKdXY1RbDlwwy7YW8rzV366ZzNjyc4bp37wx
NgfVyKevLQ6jMJyxx9ZACHZDBrVoRvJhl3mFZZOS3z0aL0j6WWsVqjK6/4DxAbWm
8l5YR+Xz/wByRLZqH4jo/+gvMHNrtd/+3z0V0aZ5c9nq8QbV7T8LqsMhym2eYR56
iwIDAQAB
-----END PUBLIC KEY-----
"""

# v0.13.0 = 设备绑定永久；v0.14.0 = 设备绑定 + 一次一密（serial）
CODE_VERSION = 2

APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ArxivCrawler"
)
LICENSE_PATH = os.path.join(APP_DIR, "license.dat")
USED_PATH = os.path.join(APP_DIR, "used_serials.json")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _b64u(b: bytes) -> str:
    # 用标准 base64（字母表 A-Za-z0-9+/，不含 '-'），便于用 '-' 分组且剥离时不冲突。
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def _run(cmd):
    """运行命令返回 stdout 文本；失败返回空串。"""
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, timeout=8, shell=False
        )
        return out.decode("utf-8", "ignore")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# 设备指纹
# --------------------------------------------------------------------------
def get_device_id() -> str:
    """采集稳定硬件标识 → SHA-256 → 32 位十六进制 device_id。"""
    parts = []

    # 1) MachineGuid（系统级、重装前稳定）
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        ) as k:
            parts.append(str(winreg.QueryValueEx(k, "MachineGuid")[0]))
    except Exception:
        pass

    # 2) 主板 / BIOS 序列号（优先 wmic，回退 PowerShell CIM）
    for label, cmds in (
        ("baseboard", [
            ["wmic", "baseboard", "get", "serialnumber"],
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_BaseBoard).SerialNumber"],
        ]),
        ("bios", [
            ["wmic", "bios", "get", "serialnumber"],
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_BIOS).SerialNumber"],
        ]),
        ("cpu", [
            ["wmic", "cpu", "get", "processorid"],
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).ProcessorId"],
        ]),
    ):
        for cmd in cmds:
            out = _run(cmd)
            lines = [ln.strip() for ln in out.splitlines()
                     if ln.strip() and label not in ln.lower()
                     and "serialnumber" not in ln.lower()]
            if lines:
                parts.append(lines[0])
                break

    # 3) 第一个 MAC 地址
    try:
        import uuid
        mac = ":".join(re.findall("..", "%012x" % uuid.getnode()))
        if mac != "00:00:00:00:00:00":
            parts.append(mac)
    except Exception:
        pass

    raw = "|".join(p for p in parts if p)
    if not raw:
        # 极端回退：仅用机器名（不推荐，但不至于崩）
        raw = "fallback:" + os.environ.get("COMPUTERNAME", "unknown")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_request_code() -> str:
    """本机设备请求码：device_id 的 base64 分组（保持原大小写，仅加连字符便于抄写），
    供授权方签名生成激活码。注意：base64 大小写敏感，不要对请求码改变大小写。"""
    code = _b64u(get_device_id().encode("ascii"))
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


# --------------------------------------------------------------------------
# 已用 serial 黑名单（v0.14.0 一次一密）
# --------------------------------------------------------------------------
def _load_used() -> list:
    try:
        with open(USED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("serials", []))
    except (OSError, ValueError):
        return []


def _save_used(serials: list) -> None:
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = USED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"serials": sorted(set(serials))}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USED_PATH)


def _mark_used(serial: str) -> None:
    serials = _load_used()
    if serial not in serials:
        serials.append(serial)
        _save_used(serials)


# --------------------------------------------------------------------------
# 校验 / 持久化
# --------------------------------------------------------------------------
def _load_pub():
    return load_pem_public_key(PUBLIC_KEY_PEM.encode("ascii"))


def _decode_payload(code: str):
    """提取 (payload dict, sig bytes, payload_b64 str)。失败抛 ValueError。"""
    code = code.strip().replace("-", "").replace(" ", "")
    if "." not in code:
        raise ValueError("激活码格式错误（缺少分隔符 '.'）")
    payload_b64, sig_b64 = code.split(".", 1)
    sig = _b64d(sig_b64)
    payload = json.loads(_b64d(payload_b64))
    return payload, sig, payload_b64


def verify_activation(code: str) -> bool:
    """校验激活码**合法性**：签名有效 + device_id 匹配本机。

    注：此函数**不**检查 serial 是否已被本机用过——那是"激活前"的检查。
    每次启动 `is_activated` 也用此函数来"重验 license.dat 是否仍合法"。
    """
    try:
        payload, sig, payload_b64 = _decode_payload(code)
        if payload.get("v") != CODE_VERSION:
            return False
        if payload.get("dev") != get_device_id():
            return False
        if not payload.get("s"):
            return False
        _load_pub().verify(
            sig, payload_b64.encode("ascii"),
            padding.PKCS1v15(), hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def _check_serial_unused(serial: str) -> bool:
    """serial 不在本机 used_serials.json 中（v0.14.0 一次一密核心）"""
    return bool(serial) and serial not in _load_used()


def is_activated() -> bool:
    """已激活 = license.dat 存在 且 仍能通过 verify_activation（签名 + 设备绑定）"""
    if not os.path.exists(LICENSE_PATH):
        return False
    try:
        with open(LICENSE_PATH, "r", encoding="utf-8") as f:
            stored = f.read().strip()
        return verify_activation(stored)
    except Exception:
        return False


def activate(code: str) -> bool:
    """激活：签名合法 + device_id 匹配 + serial 未被本机用过 → 写 license.dat + used_serials。"""
    try:
        payload, sig, payload_b64 = _decode_payload(code)
        if payload.get("v") != CODE_VERSION:
            return False
        if payload.get("dev") != get_device_id():
            return False
        serial = payload.get("s", "")
        if not serial or not _check_serial_unused(serial):
            return False
        _load_pub().verify(
            sig, payload_b64.encode("ascii"),
            padding.PKCS1v15(), hashes.SHA256(),
        )
    except Exception:
        return False

    os.makedirs(APP_DIR, exist_ok=True)
    # 先写 used_serials 再写 license.dat（避免激活成功却没记入黑名单）
    _mark_used(serial)
    tmp = LICENSE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(code.strip())
    os.replace(tmp, LICENSE_PATH)
    return True


def deactivate() -> None:
    try:
        os.remove(LICENSE_PATH)
    except OSError:
        pass


def get_activation_info() -> dict:
    """当前激活信息（设备 id / serial / 首次激活时间等）— 供设置/帮助面板展示。"""
    if not is_activated():
        return {"activated": False, "device_id": get_device_id()[:16] + "…"}
    try:
        with open(LICENSE_PATH, "r", encoding="utf-8") as f:
            code = f.read().strip()
        payload, _, _ = _decode_payload(code)
        return {
            "activated": True,
            "device_id": payload.get("dev", "")[:16] + "…",
            "serial": payload.get("s", ""),
            "issued_at": payload.get("iat", "—"),
        }
    except Exception:
        return {"activated": False, "device_id": "?"}


# 供桌面软件导入时方便调用
__all__ = [
    "get_device_id", "get_request_code", "verify_activation",
    "is_activated", "activate", "deactivate", "get_activation_info",
    "CODE_VERSION", "PUBLIC_KEY_PEM",
]

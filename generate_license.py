"""
generate_license.py — 激活码生成器（仅授权方持有私钥，不随安装包分发）

v0.14.0 升级：**一次一密** + **防重发**
  * payload = {v:2, dev:device_id, s:UUID4 序列号, iat:签发时间}
  * issued.json 记录 request_code→{serial, issued_at} 映射，
    同一请求码第二次签发会被拒（或提示"已签发过"）。

用法：
    python generate_license.py <设备请求码或device_id>
    python generate_license.py <请求码1> <请求码2> ...   # 批量

  * 请求码 = 目标机器上软件"激活"对话框里显示的"本机设备请求码"
    （即 device_id 的标准 base64 分组，含连字符，例如 XXXX-XXXX-XXXX-XXXX）。
  * 也可直接粘贴 32 位 device_id 十六进制串。
  * 输出即激活码（含连字符，粘贴时有无连字符均可）。
  * 每个请求码默认只能签发一次（防授权方手抖重复发码造成一码多用）；
    如需重签，请先 `python generate_license.py --reset <请求码>` 清掉历史记录。

原理：用私钥对 payload（含 device_id + serial）签名；应用用内嵌公钥验签 + 比对
本机 device_id + 检查 serial 未被使用过。没有私钥无法伪造有效激活码。
"""

import os
import sys
import json
import base64
import uuid
import datetime

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

CODE_VERSION = 2  # 必须与 app_license.CODE_VERSION 一致

ROOT = os.path.dirname(os.path.abspath(__file__))

# 默认存放位置（v0.20.1 起）：项目树外的专用目录，目录与文件名均带项目前缀，
# 便于多项目并存时不混淆。可用环境变量 ARXIV_LICENSE_KEYS_DIR 覆盖。
DEFAULT_KEYS_DIR = "C:/workbuddy_tools/ArxivCrawler_license_keys"
PRIV_FNAME = "ArxivCrawler_private_key.pem"
ISSUED_FNAME = "ArxivCrawler_issued.json"
LEGACY_PRIV_FNAME = "private_key.pem"
LEGACY_ISSUED_FNAME = "issued.json"


def _resolve_keys():
    """定位 (目录, 私钥文件名, 签发记录文件名)。

    v0.20.1 起**已迁出项目树**，默认放在 C:/workbuddy_tools/ArxivCrawler_license_keys
    （授权方本机，绝不会随项目源码 / 安装包分发）。解析顺序：
      1) 环境变量 ARXIV_LICENSE_KEYS_DIR（强制覆盖，指向任意安全目录，文件名用项目前缀）
      2) 默认 C:/workbuddy_tools/ArxivCrawler_license_keys（带项目前缀的文件名）
      3) 兼容旧位置 <脚本目录>/license_keys（仅当默认不存在时回退，文件名用旧无前缀名）
    """
    env = os.environ.get("ARXIV_LICENSE_KEYS_DIR")
    if env:
        return env, PRIV_FNAME, ISSUED_FNAME
    if os.path.isdir(DEFAULT_KEYS_DIR):
        return DEFAULT_KEYS_DIR, PRIV_FNAME, ISSUED_FNAME
    legacy = os.path.join(ROOT, "license_keys")
    if os.path.isdir(legacy):
        return legacy, LEGACY_PRIV_FNAME, LEGACY_ISSUED_FNAME
    return DEFAULT_KEYS_DIR, PRIV_FNAME, ISSUED_FNAME


KEYS_DIR, PRIV_NAME, ISSUED_NAME = _resolve_keys()
PRIVATE_KEY_PATH = os.path.join(KEYS_DIR, PRIV_NAME)
ISSUED_PATH = os.path.join(KEYS_DIR, ISSUED_NAME)


def _b64u(b: bytes) -> str:
    # 标准 base64（不含 '-'），与 app_license 保持一致
    return base64.b64encode(b).decode("ascii")


def _dev_from_request(req: str) -> str:
    """请求码(标准 base64 of device_id) → device_id；若已是 device_id 则原样返回。

    注意：base64 大小写敏感，绝不可对请求码整体 lower；仅当输入被判为
    纯 32 位十六进制 device_id 时才 lower（hex 大小写无关）。
    """
    req = req.strip().replace("-", "").replace(" ", "")
    # 先按标准 base64 解码（保持大小写），成功且得到 32 位 hex 即命中
    try:
        s = req + "=" * (-len(req) % 4)
        dev = base64.b64decode(s).decode("ascii")
        if len(dev) == 32 and all(c in "0123456789abcdef" for c in dev):
            return dev
    except Exception:
        pass
    # 当作原始 device_id 处理（hex 大小写无关，统一 lower）
    req_l = req.lower()
    if len(req_l) == 32 and all(c in "0123456789abcdef" for c in req_l):
        return req_l
    raise SystemExit("无法从输入解析出有效的 device_id（请求码或 32 位十六进制串）")


def _req_from_dev(dev: str) -> str:
    """反向：device_id → 请求码（标准 base64 分组），用于 issued.json 索引。"""
    code = _b64u(dev.encode("ascii"))
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def _load_issued() -> dict:
    try:
        with open(ISSUED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_issued(issued: dict) -> None:
    os.makedirs(os.path.dirname(ISSUED_PATH), exist_ok=True)
    tmp = ISSUED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(issued, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ISSUED_PATH)


def _new_serial() -> str:
    """生成全局唯一序列号（UUID4 短码）。"""
    return uuid.uuid4().hex[:16]


def sign_one(req: str, allow_resign: bool = False) -> str:
    """为单个请求码签发激活码。

    成功返回激活码字符串；失败抛 SystemExit。
    若同一请求码已签发过，第二次默认拒绝（除非 allow_resign=True）。
    """
    if not os.path.exists(PRIVATE_KEY_PATH):
        raise SystemExit(f"找不到私钥：{PRIVATE_KEY_PATH}")

    dev = _dev_from_request(req)
    req_canon = _req_from_dev(dev)  # 规范化请求码（用于查重）
    issued = _load_issued()

    if req_canon in issued and not allow_resign:
        old = issued[req_canon]
        raise SystemExit(
            f"该请求码已经签发过激活码（serial={old.get('serial', '?')}，"
            f"签发时间={old.get('iat', '?')}）。如确认要重发（将产生新码，旧码仍可被目标设备使用），\n"
            f"请用：python generate_license.py --reset {req_canon}   # 清掉历史后再签"
        )

    serial = _new_serial()
    iat = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {"v": CODE_VERSION, "dev": dev, "s": serial, "iat": iat}
    payload_b64 = _b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    with open(PRIVATE_KEY_PATH, "rb") as f:
        priv = load_pem_private_key(f.read(), password=None)
    sig = priv.sign(payload_b64.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = _b64u(sig)
    code = f"{payload_b64}.{sig_b64}"
    grouped = "-".join(code[i:i + 4] for i in range(0, len(code), 4))

    # 记入 issued.json（防重发）
    issued[req_canon] = {"serial": serial, "iat": iat, "device_id": dev}
    _save_issued(issued)
    return grouped, dev, serial, iat


def reset_one(req: str) -> None:
    """清掉某请求码的历史签发记录（允许重新签发）。"""
    dev = _dev_from_request(req)
    req_canon = _req_from_dev(dev)
    issued = _load_issued()
    if req_canon in issued:
        del issued[req_canon]
        _save_issued(issued)
        print(f"已清除 {req_canon} 的签发记录。")
    else:
        print(f"{req_canon} 尚无签发记录，无需清。")


def list_issued() -> None:
    issued = _load_issued()
    if not issued:
        print("(空) 尚未签发任何激活码。")
        return
    print(f"已签发 {len(issued)} 张激活码：")
    for req, info in issued.items():
        print(f"  {req}  →  serial={info.get('serial','?')[:8]}…  "
              f"iat={info.get('iat','?')}  dev={info.get('device_id','?')[:12]}…")


def main():
    args = sys.argv[1:]
    if not args:
        print("用法:")
        print("  python generate_license.py <请求码1> [请求码2 ...]    # 批量签发")
        print("  python generate_license.py --list                    # 看已签发清单")
        print("  python generate_license.py --reset <请求码>          # 允许对某请求码重新签发")
        print()
        print("  请求码 = 目标设备上软件激活窗口里显示的「本机设备请求码」(base64 分组)。")
        sys.exit(1)

    if args[0] == "--list":
        list_issued()
        return
    if args[0] == "--reset" and len(args) >= 2:
        reset_one(args[1])
        return

    if args[0] == "--force-resign":
        allow = True
        args = args[1:]
    else:
        allow = False

    n_ok = n_skip = 0
    for req in args:
        try:
            grouped, dev, serial, iat = sign_one(req, allow_resign=allow)
            print(f"\n=== 设备 {dev[:12]}… ===")
            print(f"  serial:  {serial}")
            print(f"  签发时间: {iat}")
            print(f"  激活码:  {grouped}")
            print()
            n_ok += 1
        except SystemExit as e:
            print(f"[跳过] {req}: {e}", file=sys.stderr)
            n_skip += 1
    print(f"--- 签发完成：成功 {n_ok} 张，跳过 {n_skip} 张 ---")
    if n_ok:
        print("请将激活码分别发给对应用户；用户在自己机器的【激活】对话框里粘贴即可。")


if __name__ == "__main__":
    main()

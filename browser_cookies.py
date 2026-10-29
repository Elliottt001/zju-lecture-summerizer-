# -*- coding: utf-8 -*-
"""
从本机 Microsoft Edge 的 Cookie 数据库里读取指定域名的登录态。
纯标准库：sqlite3 读库 + 内置 AES-128-CBC 解密（macOS 上 Edge 用 Keychain
里的 "Microsoft Edge Safe Storage" 密钥加密 Cookie 值）。
第一次运行 macOS 会弹 Keychain 授权框，点"允许"即可。
"""
import glob
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

IS_WIN = sys.platform == "win32"
EDGE_ROOT_MAC = os.path.expanduser(
    "~/Library/Application Support/Microsoft Edge")
EDGE_ROOT_WIN = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             "Microsoft", "Edge", "User Data")
KEYCHAIN_SERVICE = "Microsoft Edge Safe Storage"
KEYCHAIN_ACCOUNT = "Microsoft Edge"


# --------------------------------------------------------------- AES-128
# 只需要解密（CBC），实现逆密码。按 FIPS-197 测试向量验证过。

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
_INV_SBOX = bytes(_SBOX.index(i) for i in range(256))
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36)


def _xtime(a):
    a <<= 1
    return a ^ 0x1b if a & 0x100 else a


def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        b >>= 1
        a = _xtime(a)
    return p & 0xff


def _key_expansion(key):
    """返回 11 个轮密钥，每个 = 4 个字（每字 4 字节），对应 state 的 4 列。"""
    w = [list(key[4 * i:4 * i + 4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [w[4 * r:4 * r + 4] for r in range(11)]


def _add_round_key(state, rk):
    for c in range(4):
        for r in range(4):
            state[r][c] ^= rk[c][r]


def _inv_shift_rows(s):
    s[1] = s[1][3:] + s[1][:3]
    s[2] = s[2][2:] + s[2][:2]
    s[3] = s[3][1:] + s[3][:1]


def _inv_mix_columns(s):
    for c in range(4):
        a = s[0][c], s[1][c], s[2][c], s[3][c]
        s[0][c] = _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
        s[1][c] = _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
        s[2][c] = _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
        s[3][c] = _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)


def _aes128_decrypt_block(block, round_keys):
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    _add_round_key(s, round_keys[10])
    for rnd in range(9, 0, -1):
        _inv_shift_rows(s)
        s = [[_INV_SBOX[b] for b in row] for row in s]
        _add_round_key(s, round_keys[rnd])
        _inv_mix_columns(s)
    _inv_shift_rows(s)
    s = [[_INV_SBOX[b] for b in row] for row in s]
    _add_round_key(s, round_keys[0])
    return bytes(s[r][c] for c in range(4) for r in range(4))


def aes128_cbc_decrypt(key, iv, data):
    rks = _key_expansion(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        blk = _aes128_decrypt_block(data[i:i + 16], rks)
        out += bytes(b ^ p for b, p in zip(blk, prev))
        prev = data[i:i + 16]
    pad = out[-1]
    if 1 <= pad <= 16 and out[-pad:] == bytes([pad]) * pad:
        out = out[:-pad]
    return bytes(out)


# --------------------------------------------------------------- Edge 读取

def _keychain_password():
    """从 macOS Keychain 取 Edge 的 Cookie 加密主密码（首次会弹授权框）。"""
    r = subprocess.run(
        ["security", "find-generic-password", "-w",
         "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(
            "无法从钥匙串读取 Edge 密钥（可能点了拒绝）。"
            "请重试并在弹窗中选择『允许』；或改用手动粘贴 Cookie。")
    return r.stdout.rstrip("\n")


def _dpapi_decrypt(data):
    """Windows：用系统 DPAPI 解密（ctypes 调 crypt32，无需第三方库）。"""
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(inp), None, None, None, None, 0,
            ctypes.byref(out)):
        raise RuntimeError("DPAPI 解密失败（需在登录本机的 Windows 账户下运行）")
    res = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return res


def _decrypt_value(raw, key):
    if raw[:3] in (b"v10", b"v20"):
        if IS_WIN:                            # Windows：DPAPI
            return _dpapi_decrypt(raw).decode("utf-8", "replace")
        # macOS：Keychain 主密码 + PBKDF2 + AES-128-CBC
        dk = hashlib.pbkdf2_hmac("sha1", key.encode("utf-8"),
                                 b"saltysalt", 1003, 16)
        return aes128_cbc_decrypt(dk, b" " * 16, raw[3:]).decode("utf-8",
                                                                 "replace")
    return raw.decode("utf-8", "replace")     # 未加密（少见）


def read_edge_cookies(domain="zju.edu.cn"):
    """读取 Edge 里属于 domain（含子域）的所有 Cookie，拼成请求头字符串。"""
    if IS_WIN:
        # 新版 Edge 的 Cookie 库在 Network 子目录，旧版直接在配置目录下
        dbs = (glob.glob(os.path.join(EDGE_ROOT_WIN, "*", "Network", "Cookies"))
               + glob.glob(os.path.join(EDGE_ROOT_WIN, "*", "Cookies")))
        if not dbs:
            raise RuntimeError("没找到 Edge 的 Cookie 数据库，请确认已安装 Edge")
        key = None                            # Windows 不需要 Keychain
    else:
        dbs = glob.glob(os.path.join(EDGE_ROOT_MAC, "*", "Cookies"))
        if not dbs:
            raise RuntimeError(
                "没找到 Edge 的 Cookie 数据库。若提示权限不足，请到 "
                "系统设置 → 隐私与安全性 → 完全磁盘访问权限，把终端（或 python）加进去；"
                "或改用手动粘贴 Cookie。")
        key = _keychain_password()
    pairs = []
    seen = set()
    for db in dbs:
        tmp = tempfile.mktemp(suffix=".cookies")
        try:
            shutil.copy2(db, tmp)               # Edge 运行中数据库被锁，先拷出来
            con = sqlite3.connect(tmp)
            rows = con.execute(
                "SELECT host_key, name, encrypted_value, value FROM cookies"
                " WHERE host_key LIKE ?", ("%" + domain,)).fetchall()
            con.close()
        except sqlite3.Error as e:
            raise RuntimeError(f"读取 Cookie 数据库失败：{e}")
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        for host, name, enc, plain in rows:
            val = plain or ""
            if enc:
                try:
                    val = _decrypt_value(enc, key)
                except Exception:
                    continue
            if (host, name) in seen or not val:
                continue
            seen.add((host, name))
            pairs.append(f"{name}={val}")
    if not pairs:
        raise RuntimeError(
            f"Edge 里没有 {domain} 的 Cookie——请先在 Edge 里登录智云课堂")
    return "; ".join(pairs)


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "zju.edu.cn"
    c = read_edge_cookies(d)
    print(f"共 {c.count('=')} 个 Cookie，总长 {len(c)} 字符")
    print(c[:120] + "…")

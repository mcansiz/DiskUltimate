"""İşletim sistemi (giriş) parolasını sıfırlama — saf Python (ADR 0100).

Unutulmuş parola **geri getirilmez** (hash'li saklanır). Bu modül, diske
fiziksel erişimi olan kullanıcının kendi makinesinde parolayı **sıfırlar /
değiştirir / kaldırır** — bir kurtarma işlemidir, kırma değil.

Bu dilim **Linux** (`/etc/shadow`): kullanıcıları listeler, parolayı yeni bir
değere ($6$ sha512crypt) ayarlar, boşaltır (parolasız), kilitler/açar.
Windows (SAM hive) sonraki dilimde (`core/winreg.py`).

Çapraz platform: çekirdek platforma bağlı `crypt` modülünü **import etmez**;
`sha512crypt` saf Python'dur (ana makinedeki stdlib `crypt` ile KAT doğrulanır,
`tests/ospass_check`). Bir `FileSystemAccess` (bölümün açık dosya sistemi)
üzerinde çalışır; birim yazılabilir açık olmalıdır.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import List, Optional

from ..i18n import tr


class OsPassError(Exception):
    """OS parola sıfırlama hatası."""


# ======================================================================
# sha512crypt ($6$) — saf Python (glibc ile birebir)
# ======================================================================
_B64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

# glibc sha512 çıktısının son yeniden sıralaması (21 üçlü + son bayt).
_PERM = (
    (0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4),
    (47, 5, 26), (6, 27, 48), (28, 49, 7), (50, 8, 29), (9, 30, 51),
    (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13), (56, 14, 35),
    (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19),
    (62, 20, 41),
)


def _b64_24(b2: int, b1: int, b0: int, n: int) -> str:
    w = (b2 << 16) | (b1 << 8) | b0
    out = []
    for _ in range(n):
        out.append(_B64[w & 0x3F])
        w >>= 6
    return "".join(out)


def sha512crypt(password: bytes, salt: str, rounds: int = 5000) -> str:
    """glibc $6$ sha512crypt. `salt` en fazla 16 karakter."""
    salt_b = salt.encode("utf-8")[:16]
    salt = salt_b.decode("utf-8", "ignore")
    pw = password
    plen = len(pw)

    b = hashlib.sha512(pw + salt_b + pw).digest()
    a = hashlib.sha512()
    a.update(pw + salt_b)
    a.update((b * (plen // 64)) + b[:plen % 64])
    i = plen
    while i:
        a.update(b if (i & 1) else pw)
        i >>= 1
    a = a.digest()

    dp = hashlib.sha512(pw * plen).digest()
    p = (dp * (plen // 64)) + dp[:plen % 64]
    ds = hashlib.sha512(salt_b * (16 + a[0])).digest()
    s = (ds * (len(salt_b) // 64)) + ds[:len(salt_b) % 64]

    c = a
    for i in range(rounds):
        h = hashlib.sha512()
        h.update(p if (i & 1) else c)
        if i % 3:
            h.update(s)
        if i % 7:
            h.update(p)
        h.update(c if (i & 1) else p)
        c = h.digest()

    out = [_b64_24(c[x], c[y], c[z], 4) for (x, y, z) in _PERM]
    out.append(_b64_24(0, 0, c[63], 2))
    prefix = "$6$" if rounds == 5000 else "$6$rounds=%d$" % rounds
    return prefix + salt + "$" + "".join(out)


def _random_salt(n: int = 16) -> str:
    import os
    raw = os.urandom(n)
    return "".join(_B64[b & 0x3F] for b in raw)


# ======================================================================
# Linux: /etc/passwd + /etc/shadow
# ======================================================================
@dataclass
class OsUser:
    system: str                 # 'linux' | 'windows'
    name: str
    uid: int = -1               # Linux UID / Windows RID
    status: str = ""            # '', 'yok', 'kilitli', 'sistem'
    disabled: bool = False
    note: str = ""              # görünen ipucu (ör. "parola yok")
    is_system: bool = False     # servis/sistem hesabı (arayüzde varsayılan gizli)


# Giriş kabuğu olmayan hesaplar (sistem/servis). Düşük/yüksek UID ile birlikte
# "insan giriş hesabı değil" ayrımında kullanılır.
_NOLOGIN = ("nologin", "/false", "/bin/false", "/bin/sync", "/dev/null")
_UID_MIN, _UID_MAX = 1000, 60000    # Ubuntu/Debian normal kullanıcı aralığı


# Misafir birim içindeki yollar (ana makine değil — ADR 0100).
SHADOW_PATH = "/etc/shadow"
PASSWD_PATH = "/etc/passwd"


def backup_target(fs, system: str) -> str:
    """İşlemden önce hedef dosyanın yedeğini aynı birime (.dubak) alır."""
    path = SAM_PATH if system == "windows" else SHADOW_PATH
    fs.write_file(path + ".dubak", fs.read(path))
    return path + ".dubak"


def _read_text(fs, path: str) -> str:
    try:
        return fs.read(path).decode("utf-8", "replace")
    except Exception as exc:                       # noqa: BLE001
        raise OsPassError(tr("{} okunamadı: {}", path, exc))


def linux_users(fs) -> List[OsUser]:
    """`/etc/passwd` + `/etc/shadow`'dan kullanıcıları listeler."""
    passwd = _read_text(fs, "/etc/passwd")
    shadow = _read_text(fs, "/etc/shadow")
    sh = {}
    for line in shadow.splitlines():
        if ":" in line:
            name, _, rest = line.partition(":")
            sh[name] = rest.split(":")[0]
    users: List[OsUser] = []
    for line in passwd.splitlines():
        parts = line.split(":")
        if len(parts) < 7:
            continue
        name, _, uid = parts[0], parts[1], parts[2]
        try:
            uidn = int(uid)
        except ValueError:
            continue
        shell = parts[6].strip()
        pw = sh.get(name, parts[1])
        u = OsUser(system="linux", name=name, uid=uidn)
        # Sistem/servis hesabı: root dışında, normal kullanıcı UID aralığının
        # dışında ya da giriş kabuğu olmayan. (Arayüz bunları varsayılan gizler;
        # root ve gerçek kullanıcılar kalır.)
        nologin = (not shell) or any(shell.endswith(s) for s in _NOLOGIN)
        if uidn != 0 and (uidn < _UID_MIN or uidn > _UID_MAX or nologin):
            u.is_system = True
            u.status = "sistem"
        if pw == "" :
            u.note = tr("parola yok")
        elif pw.startswith("!") or pw.startswith("*"):
            u.disabled = True
            u.status = u.status or "kilitli"
            u.note = tr("kilitli")
        elif pw.startswith("$"):
            u.note = tr("parola var")
        users.append(u)
    return users


def _require_writable(fs):
    if not getattr(fs, "writable", False):
        reason = getattr(fs, "write_reason", "") or tr("birim salt okunur açık")
        raise OsPassError(tr("Bu birime yazılamıyor: {}", reason))


def read_backup(fs, path: str) -> bytes:
    """İşlemden önce yedek alınması için dosyanın ham içeriği."""
    return fs.read(path)


def linux_set_password(fs, user: str, new_password: Optional[str] = None,
                       blank: bool = False, lock: bool = False,
                       unlock: bool = False) -> None:
    """`/etc/shadow`'da `user`'ın parola alanını düzenler.

    * `new_password` — yeni parola ($6$ sha512crypt ile hash'lenir)
    * `blank`        — alanı boşaltır (parolasız; PAM `nullok` gerekebilir)
    * `lock`/`unlock`— `!` ön ekini ekler/kaldırır
    """
    _require_writable(fs)
    text = _read_text(fs, "/etc/shadow")
    lines = text.split("\n")
    found = False
    for idx, line in enumerate(lines):
        if not line or ":" not in line:
            continue
        fields = line.split(":")
        if fields[0] != user:
            continue
        found = True
        cur = fields[1]
        if new_password is not None:
            fields[1] = sha512crypt(new_password.encode("utf-8"), _random_salt())
        elif blank:
            fields[1] = ""
        elif lock:
            fields[1] = cur if cur.startswith("!") else "!" + cur
        elif unlock:
            fields[1] = cur[1:] if cur.startswith("!") else cur
        else:
            raise OsPassError(tr("Bir işlem seçilmedi."))
        lines[idx] = ":".join(fields)
        break
    if not found:
        raise OsPassError(tr("Kullanıcı bulunamadı: {}", user))
    data = "\n".join(lines).encode("utf-8")
    fs.write_file("/etc/shadow", data)
    fs.flush()


# ======================================================================
# Windows: SAM hive (yerel hesaplar)
# ======================================================================
_SAM_USERS = "SAM\\Domains\\Account\\Users"
# SAM dosyasının birim içindeki yolu (misafir Windows yolu).
SAM_PATH = "/Windows/System32/config/SAM"

# V yapısı alan ofsetleri (0xCC tabanlı; chntpw/creddump ile aynı)
_V_NAME_OFF, _V_NAME_LEN = 0x0C, 0x10
_V_LM_LEN, _V_NT_LEN = 0xA0, 0xB0
_V_BASE = 0xCC
# F yapısı: hesap denetim bitleri (ACB)
_F_ACB = 0x38
_ACB_DISABLED = 0x0001


def _parse_v(data: bytes):
    """V değerinden (kullanıcı adı, NT hash var mı) döndürür."""
    name = ""
    if len(data) >= _V_NAME_LEN + 4:
        off = struct.unpack_from("<I", data, _V_NAME_OFF)[0] + _V_BASE
        ln = struct.unpack_from("<I", data, _V_NAME_LEN)[0]
        name = data[off:off + ln].decode("utf-16-le", "replace")
    nt_len = (struct.unpack_from("<I", data, _V_NT_LEN)[0]
              if len(data) >= _V_NT_LEN + 4 else 0)
    return name, nt_len > 0


def windows_users(sam_bytes: bytes) -> List[OsUser]:
    """SAM kütüğünden yerel hesapları listeler (ad, RID, parola/kilit)."""
    from . import winreg
    h = winreg.Hive(sam_bytes)
    users_key = h.find(_SAM_USERS)
    if users_key is None:
        raise OsPassError(tr("SAM kütüğünde kullanıcı listesi bulunamadı."))
    out: List[OsUser] = []
    for child, _ in h.subkeys(users_key):
        rid_name = h.nk_name(child)
        if rid_name.lower() == "names":
            continue
        try:
            rid = int(rid_name, 16)
        except ValueError:
            continue
        v = h.value(child, "V")
        if v is None:
            continue
        name, has_pw = _parse_v(v[0])
        u = OsUser(system="windows", name=name or rid_name, uid=rid)
        u.note = tr("parola var") if has_pw else tr("parola yok")
        f = h.value(child, "F")
        if f is not None and len(f[0]) >= _F_ACB + 2:
            acb = struct.unpack_from("<H", f[0], _F_ACB)[0]
            if acb & _ACB_DISABLED:
                u.disabled = True
                u.status = "kilitli"
                u.note = tr("kilitli")
        out.append(u)
    return out


def _user_key(h, rid: int):
    users_key = h.find(_SAM_USERS)
    if users_key is None:
        raise OsPassError(tr("SAM kütüğünde kullanıcı listesi bulunamadı."))
    key = h.subkey(users_key, "%08X" % rid)
    if key is None:
        raise OsPassError(tr("Kullanıcı bulunamadı (RID {}).", rid))
    return key


def windows_blank_password(sam_bytes: bytes, rid: int) -> bytes:
    """V'deki LM ve NT hash uzunluklarını 0 yapar → parola kaldırılır.

    (chntpw yöntemi: hash kurulmaz, kaldırılır; bootkey gerekmez.)"""
    from . import winreg
    h = winreg.Hive(sam_bytes)
    key = _user_key(h, rid)
    v = h.value(key, "V")
    if v is None:
        raise OsPassError(tr("V değeri bulunamadı."))
    _, off, size = v
    if size < _V_NT_LEN + 4:
        raise OsPassError(tr("V yapısı beklenenden kısa."))
    h.patch(off + _V_LM_LEN, struct.pack("<I", 0))
    h.patch(off + _V_NT_LEN, struct.pack("<I", 0))
    return h.to_bytes()


def windows_enable_account(sam_bytes: bytes, rid: int) -> bytes:
    """F'deki ACB_DISABLED bitini temizler → hesap etkinleşir."""
    from . import winreg
    h = winreg.Hive(sam_bytes)
    key = _user_key(h, rid)
    f = h.value(key, "F")
    if f is None or f[2] < _F_ACB + 2:
        raise OsPassError(tr("F değeri bulunamadı."))
    _, off, _size = f
    acb = struct.unpack_from("<H", h.data, off + _F_ACB)[0]
    h.patch(off + _F_ACB, struct.pack("<H", acb & ~_ACB_DISABLED))
    return h.to_bytes()


def windows_users_fs(fs) -> List[OsUser]:
    return windows_users(fs.read(SAM_PATH))


def windows_reset_fs(fs, rid: int, blank: bool = True,
                     enable: bool = True) -> None:
    """SAM'ı okur, parolayı boşaltır ve/veya hesabı etkinleştirir, yazar."""
    _require_writable(fs)
    out = fs.read(SAM_PATH)
    if blank:
        out = windows_blank_password(out, rid)
    if enable:
        out = windows_enable_account(out, rid)
    fs.write_file(SAM_PATH, out)
    fs.flush()

"""Kullanici ayarlarinin kalici saklanmasi (kucuk bir JSON dosyasi).

Ayarlar **proje dizinine degil**, isletim sisteminin kullanici ayar klasorune
yazilir (`platform.config_dir()`): uygulama salt okunur bir klasorden
calistirilabilir ve ayarlar makinedeki kullaniciya aittir.

Dosya bozuksa veya yazilamiyorsa uygulama **calismaya devam eder**; ayar
kaybolur ama hicbir islem durmaz. Bir disk aracinin bir tercih dosyasi
yuzunden acilmamasi kabul edilemez.

QSettings kullanilmaz: `core/` PyQt import etmez (CLAUDE.md katman kurali) ve
ayarlar cekirdek testlerinden de okunabilmelidir.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .platform import config_dir, restore_owner

FILE_NAME = "settings.json"

_cache: Optional[Dict[str, Any]] = None


def path() -> str:
    """Ayar dosyasinin tam yolu."""
    return os.path.join(config_dir(), FILE_NAME)


def load() -> Dict[str, Any]:
    """Ayarlarin tamamini dondurur (ilk cagrida dosyadan okur)."""
    global _cache
    if _cache is None:
        try:
            with open(path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def get(key: str, default: Any = None) -> Any:
    """Tek bir ayari okur."""
    return load().get(key, default)


def set_value(key: str, value: Any) -> bool:
    """Tek bir ayari yazar ve dosyayi gunceller. Basarisizsa False doner."""
    data = load()
    data[key] = value
    return save()


def save() -> bool:
    """Ayarlari diske yazar. Yazilamazsa False doner (olumcul degil)."""
    data = load()
    target = path()
    try:
        # Once gecici dosyaya, sonra yerine: yarim yazilmis bir ayar dosyasi
        # sonraki acilista sessizce silinirdi.
        temporary = target + ".new"
        with open(temporary, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, target)
        restore_owner(target)            # yetkili kopyada root'a ait kalmasin
        return True
    except OSError:
        return False


def reset_cache() -> None:
    """Onbellegi bosaltir (testler icin)."""
    global _cache
    _cache = None

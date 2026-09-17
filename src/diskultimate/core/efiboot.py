"""UEFI onyukleme degiskenlerinin yapisi — saf Python, isletim sisteminden bagimsiz.

## Ne yapar

UEFI bellenimi onyukleme duzenini **degisken** olarak tutar: `BootOrder` sirayi,
`Boot0000`, `Boot0001`... her bir girisi tarif eder. Bu modul o ikili yapilari
**cozer ve yeniden uretir**; degiskenleri nereden okudugunu bilmez.

Okuma/yazma `core/platform.py` icindedir (Linux `efivarfs`, Windows bellenim
API'si). Boylece yapi cozumlemesi her platformda ayni kodla yapilir ve
testlerde aygit olmadan dogrulanabilir — bu ayrim olmasaydi UEFI cozumleyicisi
yalnizca UEFI ile acilmis bir makinede test edilebilirdi.

## Kaynak

UEFI Specification 2.10, bolum 3.1.3 (`EFI_LOAD_OPTION`), 3.1.6
(`EFI_KEY_OPTION`) ve 10.3-10.6 (aygit yolu dugumleri ve metin gosterimi).
Metin gosterimi olculudur: `PciRoot(0x0)/Pci(0x1D,0x0)/HD(1,GPT,...)/File(...)`
biciminde, bellenim ureticilerinin ve `efibootmgr`in urettigi yaziyla ayni.

Gerekce: `.claude/decisions/0029-uefi-onyukleme-duzenleyici.md`
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..i18n import mark, tr

# EFI_GLOBAL_VARIABLE — onyukleme degiskenlerinin tamami bu ad alanindadir.
GLOBAL_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"

# Degisken oznitelikleri (UEFI 8.2). Yazarken **aynen geri verilmelidir**:
# okunan bir degiskeni farkli oznitelikle yazmak bellenimde reddedilir.
NON_VOLATILE = 0x00000001
BOOTSERVICE_ACCESS = 0x00000002
RUNTIME_ACCESS = 0x00000004
HARDWARE_ERROR_RECORD = 0x00000008
AUTHENTICATED_WRITE_ACCESS = 0x00000010
TIME_BASED_AUTHENTICATED_WRITE_ACCESS = 0x00000020
APPEND_WRITE = 0x00000040

# Onyukleme girisleri icin olagan oznitelik ucusu.
BOOT_ENTRY_ATTRIBUTES = NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS

# EFI_LOAD_OPTION oznitelikleri (UEFI 3.1.3)
LOAD_OPTION_ACTIVE = 0x00000001
LOAD_OPTION_FORCE_RECONNECT = 0x00000002
LOAD_OPTION_HIDDEN = 0x00000008
LOAD_OPTION_CATEGORY = 0x00001F00
LOAD_OPTION_CATEGORY_BOOT = 0x00000000
LOAD_OPTION_CATEGORY_APP = 0x00000100


class EfiBootError(Exception):
    """Bozuk ya da desteklenmeyen UEFI verisi."""


# ==========================================================================
# GUID
# ==========================================================================
def guid_to_bytes(text: str) -> bytes:
    """Metin GUID'i 16 baytlik EFI gosterimine cevirir.

    EFI GUID'i **karisik siralidir**: ilk uc alan kucuk endian, son ikisi
    oldugu gibi. Duz `bytes.fromhex` kullanmak baytlari ters cevirirdi.
    """
    clean = text.strip().strip("{}").replace("-", "")
    if len(clean) != 32:
        raise EfiBootError(tr("Gecersiz GUID: {}", text))
    try:
        raw = bytes.fromhex(clean)
    except ValueError:
        raise EfiBootError(tr("Gecersiz GUID: {}", text))
    return raw[0:4][::-1] + raw[4:6][::-1] + raw[6:8][::-1] + raw[8:16]


def guid_from_bytes(raw: bytes) -> str:
    """16 baytlik EFI GUID'ini olagan metin gosterimine cevirir."""
    if len(raw) < 16:
        raise EfiBootError(tr("GUID icin 16 bayt gerekiyor, {} bayt var", len(raw)))
    ordered = raw[0:4][::-1] + raw[4:6][::-1] + raw[6:8][::-1] + raw[8:16]
    text = ordered.hex()
    return f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"


# ==========================================================================
# Aygit yolu dugumleri
# ==========================================================================
# Dugum turleri (UEFI 10.3.1)
TYPE_HARDWARE = 0x01
TYPE_ACPI = 0x02
TYPE_MESSAGING = 0x03
TYPE_MEDIA = 0x04
TYPE_BIOS = 0x05
TYPE_END = 0x7F

END_ENTIRE = 0xFF
END_INSTANCE = 0x01

# Ortam (Media) alt turleri
MEDIA_HARD_DRIVE = 0x01
MEDIA_CDROM = 0x02
MEDIA_VENDOR = 0x03
MEDIA_FILE_PATH = 0x04
MEDIA_PROTOCOL = 0x05
MEDIA_FV_FILE = 0x06
MEDIA_FV = 0x07
MEDIA_RELATIVE_OFFSET = 0x08
MEDIA_RAM_DISK = 0x09

# HD() dugumundeki bolum tablosu bicimi
PARTITION_MBR = 0x01
PARTITION_GPT = 0x02

# HD() imza turu
SIGNATURE_NONE = 0x00
SIGNATURE_MBR = 0x01
SIGNATURE_GUID = 0x02


@dataclass
class DevicePathNode:
    """Tek bir aygit yolu dugumu.

    `data` dugumun basligi disindaki ham govdesidir. Cozulemeyen bir dugum
    **atilmaz**: ham haliyle saklanir ve yazarken oldugu gibi geri konur.
    Bilinmeyen bir dugumu dusurmek, tanimadigimiz bir bellenimin onyukleme
    girisini sessizce bozmak olurdu.
    """

    type_id: int
    subtype: int
    data: bytes = b""

    @property
    def length(self) -> int:
        return 4 + len(self.data)

    def to_bytes(self) -> bytes:
        return struct.pack("<BBH", self.type_id, self.subtype,
                           self.length) + self.data

    @property
    def is_end(self) -> bool:
        return self.type_id == TYPE_END

    # -- metin gosterimi --------------------------------------------------
    def text(self) -> str:
        """Dugumun UEFI olcusundeki metin gosterimi.

        Ceviriye girmez: bu bir **bicim**dir, cumle degil. Her dilde ayni
        yazilir; cevirmek `efibootmgr` ciktisiyla karsilastirmayi imkansiz
        kilardi.
        """
        render = _NODE_TEXT.get((self.type_id, self.subtype))
        if render is not None:
            try:
                return render(self.data)
            except (struct.error, ValueError, IndexError):
                pass
        return f"Path({self.type_id:#04x},{self.subtype:#04x},{self.data.hex()})"

    def describe(self) -> str:
        """Kullaniciya gosterilecek **aciklama** (bu cevrilir)."""
        name = _NODE_NAMES.get((self.type_id, self.subtype))
        return tr(name) if name else tr("Bilinmeyen dugum")


# -- tek tek dugum metinleri ----------------------------------------------
def _text_pci(data: bytes) -> str:
    function, device = struct.unpack("<BB", data[:2])
    return f"Pci({device:#04x},{function:#04x})"


def _text_acpi(data: bytes) -> str:
    hid, uid = struct.unpack("<II", data[:8])
    if (hid & 0xFFFF) == 0x41D0:        # PNP kimligi: PciRoot/PcieRoot kisayolu
        eisa = hid >> 16
        if eisa == 0x0A03:
            return f"PciRoot({uid:#x})"
        if eisa == 0x0A08:
            return f"PcieRoot({uid:#x})"
        return f"Acpi(PNP{eisa:04X},{uid:#x})"
    return f"Acpi({hid:#010x},{uid:#x})"


def _text_atapi(data: bytes) -> str:
    primary, master, lun = struct.unpack("<BBH", data[:4])
    return f"Ata({primary},{master},{lun:#x})"


def _text_scsi(data: bytes) -> str:
    target, lun = struct.unpack("<HH", data[:4])
    return f"Scsi({target:#x},{lun:#x})"


def _text_usb(data: bytes) -> str:
    port, interface = struct.unpack("<BB", data[:2])
    return f"USB({port:#x},{interface:#x})"


def _text_mac(data: bytes) -> str:
    address = data[:32]
    if_type = data[32] if len(data) > 32 else 0
    # Ethernet icin adres 6 bayttir; gerisi dolgudur ve gosterilmez.
    size = 6 if if_type in (0, 1) else 32
    return f"MAC({address[:size].hex()},{if_type:#x})"


def _text_ipv4(data: bytes) -> str:
    local = ".".join(str(b) for b in data[0:4])
    remote = ".".join(str(b) for b in data[4:8])
    return f"IPv4({remote},{local})"


def _hextets(raw: bytes) -> str:
    return ":".join(f"{(raw[i] << 8) | raw[i + 1]:X}" for i in range(0, 16, 2))


def _text_ipv6(data: bytes) -> str:
    return f"IPv6({_hextets(data[16:32])},{_hextets(data[0:16])})"


def _text_sata(data: bytes) -> str:
    port, multiplier, lun = struct.unpack("<HHH", data[:6])
    return f"Sata({port:#x},{multiplier:#x},{lun:#x})"


def _text_nvme(data: bytes) -> str:
    namespace, = struct.unpack("<I", data[:4])
    eui = data[4:12][::-1].hex().upper()
    return f"NVMe({namespace:#x},{eui})"


def _text_usb_class(data: bytes) -> str:
    vendor, product, dev_class, sub, protocol = struct.unpack("<HHBBB", data[:7])
    return f"UsbClass({vendor:#x},{product:#x},{dev_class:#x},{sub:#x},{protocol:#x})"


def _text_usb_wwid(data: bytes) -> str:
    interface, vendor, product = struct.unpack("<HHH", data[:6])
    return f"UsbWwid({vendor:#x},{product:#x},{interface:#x},{_utf16(data[6:])})"


def _text_vendor(data: bytes) -> str:
    tail = f",{data[16:].hex()}" if len(data) > 16 else ""
    return f"VenHw({guid_from_bytes(data[:16])}{tail})"


def _text_vendor_media(data: bytes) -> str:
    tail = f",{data[16:].hex()}" if len(data) > 16 else ""
    return f"VenMedia({guid_from_bytes(data[:16])}{tail})"


def _text_vendor_messaging(data: bytes) -> str:
    tail = f",{data[16:].hex()}" if len(data) > 16 else ""
    return f"VenMsg({guid_from_bytes(data[:16])}{tail})"


def _text_hard_drive(data: bytes) -> str:
    number, start, size = struct.unpack("<IQQ", data[:20])
    signature = data[20:36]
    fmt, sig_type = struct.unpack("<BB", data[36:38])
    scheme = "GPT" if fmt == PARTITION_GPT else "MBR"
    if sig_type == SIGNATURE_GUID:
        text = guid_from_bytes(signature)
    elif sig_type == SIGNATURE_MBR:
        text = f"{struct.unpack('<I', signature[:4])[0]:#010x}"
    else:
        text = "0"
    return f"HD({number},{scheme},{text},{start:#x},{size:#x})"


def _text_cdrom(data: bytes) -> str:
    entry, start, size = struct.unpack("<IQQ", data[:20])
    return f"CDROM({entry:#x},{start:#x},{size:#x})"


def _text_file(data: bytes) -> str:
    return f"File({_utf16(data)})"


def _text_fv_file(data: bytes) -> str:
    return f"FvFile({guid_from_bytes(data[:16])})"


def _text_fv(data: bytes) -> str:
    return f"Fv({guid_from_bytes(data[:16])})"


def _text_bios(data: bytes) -> str:
    device_type, status = struct.unpack("<HH", data[:4])
    name = data[4:].split(b"\x00")[0].decode("ascii", "replace")
    return f"BBS({device_type:#x},{name},{status:#x})"


def _text_uri(data: bytes) -> str:
    return f"Uri({data.decode('utf-8', 'replace')})"


def _text_end(data: bytes) -> str:
    return ""


_NODE_TEXT = {
    (TYPE_HARDWARE, 0x01): _text_pci,
    (TYPE_HARDWARE, 0x04): _text_vendor,
    (TYPE_ACPI, 0x01): _text_acpi,
    (TYPE_MESSAGING, 0x01): _text_atapi,
    (TYPE_MESSAGING, 0x02): _text_scsi,
    (TYPE_MESSAGING, 0x05): _text_usb,
    (TYPE_MESSAGING, 0x0A): _text_vendor_messaging,
    (TYPE_MESSAGING, 0x0B): _text_mac,
    (TYPE_MESSAGING, 0x0C): _text_ipv4,
    (TYPE_MESSAGING, 0x0D): _text_ipv6,
    (TYPE_MESSAGING, 0x0F): _text_usb_class,
    (TYPE_MESSAGING, 0x10): _text_usb_wwid,
    (TYPE_MESSAGING, 0x12): _text_sata,
    (TYPE_MESSAGING, 0x17): _text_nvme,
    (TYPE_MESSAGING, 0x18): _text_uri,
    (TYPE_MEDIA, MEDIA_HARD_DRIVE): _text_hard_drive,
    (TYPE_MEDIA, MEDIA_CDROM): _text_cdrom,
    (TYPE_MEDIA, MEDIA_VENDOR): _text_vendor_media,
    (TYPE_MEDIA, MEDIA_FILE_PATH): _text_file,
    (TYPE_MEDIA, MEDIA_FV_FILE): _text_fv_file,
    (TYPE_MEDIA, MEDIA_FV): _text_fv,
    (TYPE_BIOS, 0x01): _text_bios,
    (TYPE_END, END_ENTIRE): _text_end,
    (TYPE_END, END_INSTANCE): _text_end,
}

# Kullaniciya gosterilecek dugum adlari (cevrilir).
_NODE_NAMES = {
    (TYPE_HARDWARE, 0x01): mark("PCI aygiti"),
    (TYPE_HARDWARE, 0x04): mark("Uretici aygiti"),
    (TYPE_ACPI, 0x01): mark("ACPI aygiti"),
    (TYPE_MESSAGING, 0x01): mark("ATA/ATAPI diski"),
    (TYPE_MESSAGING, 0x02): mark("SCSI aygiti"),
    (TYPE_MESSAGING, 0x05): mark("USB aygiti"),
    (TYPE_MESSAGING, 0x0B): mark("Ag arabirimi (MAC)"),
    (TYPE_MESSAGING, 0x0C): mark("IPv4 ag onyuklemesi"),
    (TYPE_MESSAGING, 0x0D): mark("IPv6 ag onyuklemesi"),
    (TYPE_MESSAGING, 0x12): mark("SATA diski"),
    (TYPE_MESSAGING, 0x17): mark("NVMe diski"),
    (TYPE_MESSAGING, 0x18): mark("URI adresi"),
    (TYPE_MEDIA, MEDIA_HARD_DRIVE): mark("Disk bolumu"),
    (TYPE_MEDIA, MEDIA_CDROM): mark("CD/DVD"),
    (TYPE_MEDIA, MEDIA_FILE_PATH): mark("Dosya yolu"),
    (TYPE_MEDIA, MEDIA_FV_FILE): mark("Bellenim birimi dosyasi"),
    (TYPE_BIOS, 0x01): mark("BIOS onyukleme aygiti"),
}


def _utf16(raw: bytes) -> str:
    """UTF-16LE metni cozer; ilk bos karakterde biter."""
    if len(raw) % 2:
        raw = raw[:-1]
    text = raw.decode("utf-16-le", "replace")
    return text.split("\x00", 1)[0]


def _utf16_bytes(text: str) -> bytes:
    """Metni bos karakterle biten UTF-16LE'ye cevirir."""
    return (text + "\x00").encode("utf-16-le")


# ==========================================================================
# Aygit yolu
# ==========================================================================
def parse_device_path(raw: bytes) -> List[DevicePathNode]:
    """Ham aygit yolunu dugumlere ayirir.

    Bozuk uzunluk alanlarinda **durur**: hatali bir dugumden sonrasini
    okumaya calismak rastgele bayt yorumlamak olur. O ana kadar cozulenler
    dondurulur.
    """
    nodes: List[DevicePathNode] = []
    offset = 0
    while offset + 4 <= len(raw):
        type_id, subtype, length = struct.unpack("<BBH", raw[offset:offset + 4])
        if length < 4 or offset + length > len(raw):
            break
        nodes.append(DevicePathNode(type_id, subtype,
                                    raw[offset + 4:offset + length]))
        offset += length
        if type_id == TYPE_END and subtype == END_ENTIRE:
            break
    return nodes


def build_device_path(nodes: List[DevicePathNode]) -> bytes:
    """Dugumleri ham aygit yoluna cevirir; bitis dugumu yoksa ekler."""
    body = b"".join(
        node.to_bytes() for node in nodes
        if not (node.type_id == TYPE_END and node.subtype == END_ENTIRE))
    return body + DevicePathNode(TYPE_END, END_ENTIRE).to_bytes()


def device_path_text(nodes: List[DevicePathNode]) -> str:
    """Butun yolun metin gosterimi: `PciRoot(0x0)/Pci(...)/HD(...)/File(...)`."""
    parts = [node.text() for node in nodes if not node.is_end]
    return "/".join(p for p in parts if p)


def file_path_of(nodes: List[DevicePathNode]) -> str:
    """Yoldaki `File(...)` dugumunun icerigi (yoksa bos)."""
    for node in nodes:
        if node.type_id == TYPE_MEDIA and node.subtype == MEDIA_FILE_PATH:
            return _utf16(node.data)
    return ""


def partition_of(nodes: List[DevicePathNode]) -> Optional[Dict]:
    """Yoldaki `HD(...)` dugumunun alanlari (yoksa None).

    Girisin **hangi bolumu** gosterdigini bulmak icin kullanilir: numara ve
    baslangic sektoru, acik bir diskteki bolumle eslestirilebilir.
    """
    for node in nodes:
        if not (node.type_id == TYPE_MEDIA and node.subtype == MEDIA_HARD_DRIVE):
            continue
        if len(node.data) < 38:
            return None
        number, start, size = struct.unpack("<IQQ", node.data[:20])
        signature = node.data[20:36]
        fmt, sig_type = struct.unpack("<BB", node.data[36:38])
        return {
            "number": number,
            "start_lba": start,
            "sector_count": size,
            "scheme": "gpt" if fmt == PARTITION_GPT else "mbr",
            "guid": (guid_from_bytes(signature) if sig_type == SIGNATURE_GUID
                     else ""),
            "mbr_signature": (struct.unpack("<I", signature[:4])[0]
                              if sig_type == SIGNATURE_MBR else 0),
        }
    return None


def make_file_node(path: str) -> DevicePathNode:
    """Bir EFI dosya yolunu gosteren `File(...)` dugumu uretir.

    Yol ayirici olarak **ters egik cizgi** kullanilir: UEFI dosya yollari her
    platformda boyledir, calisan isletim sistemi ne olursa olsun.
    """
    clean = path.replace("/", "\\")
    if not clean.startswith("\\"):
        clean = "\\" + clean
    return DevicePathNode(TYPE_MEDIA, MEDIA_FILE_PATH, _utf16_bytes(clean))


def make_hard_drive_node(number: int, start_lba: int, sector_count: int,
                         scheme: str = "gpt", guid: str = "",
                         mbr_signature: int = 0) -> DevicePathNode:
    """Bir bolumu gosteren `HD(...)` dugumu uretir.

    GPT'de bolum **GUID'iyle**, MBR'de diskin imzasiyla tanimlanir; bellenim
    girisi bu bilgiyle bulur, diskin takili oldugu porta bagli kalmaz.
    """
    if scheme == "gpt" and guid:
        signature = guid_to_bytes(guid)
        sig_type = SIGNATURE_GUID
        fmt = PARTITION_GPT
    elif mbr_signature:
        signature = struct.pack("<I", mbr_signature & 0xFFFFFFFF) + b"\x00" * 12
        sig_type = SIGNATURE_MBR
        fmt = PARTITION_MBR
    else:
        signature = b"\x00" * 16
        sig_type = SIGNATURE_NONE
        fmt = PARTITION_GPT if scheme == "gpt" else PARTITION_MBR
    body = (struct.pack("<IQQ", number, start_lba, sector_count) + signature
            + struct.pack("<BB", fmt, sig_type))
    return DevicePathNode(TYPE_MEDIA, MEDIA_HARD_DRIVE, body)


# ==========================================================================
# Onyukleme girisi (EFI_LOAD_OPTION)
# ==========================================================================
@dataclass
class LoadOption:
    """Tek bir onyukleme girisi — `Boot0000` gibi bir degiskenin icerigi.

    Yapisi (UEFI 3.1.3):
        UINT32 Attributes
        UINT16 FilePathListLength
        CHAR16 Description[]          (bos karakterle biter)
        UINT8  FilePathList[FilePathListLength]
        UINT8  OptionalData[]         (kalan her sey)
    """

    number: int = 0                       # 0x0000 - 0xFFFF
    kind: str = "Boot"                    # Boot | Driver | SysPrep
    attributes: int = LOAD_OPTION_ACTIVE
    description: str = ""
    path_nodes: List[DevicePathNode] = field(default_factory=list)
    optional_data: bytes = b""
    var_attributes: int = BOOT_ENTRY_ATTRIBUTES

    # -- durum ------------------------------------------------------------
    @property
    def name(self) -> str:
        """Degisken adi: `Boot0001`."""
        return f"{self.kind}{self.number:04X}"

    @property
    def active(self) -> bool:
        return bool(self.attributes & LOAD_OPTION_ACTIVE)

    @active.setter
    def active(self, value: bool) -> None:
        if value:
            self.attributes |= LOAD_OPTION_ACTIVE
        else:
            self.attributes &= ~LOAD_OPTION_ACTIVE

    @property
    def hidden(self) -> bool:
        return bool(self.attributes & LOAD_OPTION_HIDDEN)

    @hidden.setter
    def hidden(self, value: bool) -> None:
        if value:
            self.attributes |= LOAD_OPTION_HIDDEN
        else:
            self.attributes &= ~LOAD_OPTION_HIDDEN

    @property
    def is_application(self) -> bool:
        return (self.attributes & LOAD_OPTION_CATEGORY) == LOAD_OPTION_CATEGORY_APP

    @property
    def path_text(self) -> str:
        return device_path_text(self.path_nodes)

    @property
    def file_path(self) -> str:
        return file_path_of(self.path_nodes)

    @property
    def partition(self) -> Optional[Dict]:
        return partition_of(self.path_nodes)

    @property
    def optional_text(self) -> str:
        """Ek verinin okunabilir hali.

        Cogu giriste bu alan UTF-16 bir cekirdek komut satiridir, bazen de ham
        bayttir. Metne benzemiyorsa onaltilik gosterilir — anlamsiz karakterler
        basmaktansa ham veriyi durustce gostermek daha iyidir.
        """
        raw = self.optional_data
        if not raw:
            return ""
        if len(raw) % 2 == 0:
            try:
                stripped = raw.decode("utf-16-le").rstrip("\x00")
                if stripped and _printable(stripped):
                    return stripped
            except UnicodeDecodeError:
                pass
        try:
            text = raw.rstrip(b"\x00").decode("ascii")
            if text and _printable(text):
                return text
        except UnicodeDecodeError:
            pass
        return raw.hex()

    # -- cozumleme --------------------------------------------------------
    @classmethod
    def parse(cls, raw: bytes, number: int = 0, kind: str = "Boot",
              var_attributes: int = BOOT_ENTRY_ATTRIBUTES) -> "LoadOption":
        if len(raw) < 6:
            raise EfiBootError(tr("Onyukleme girisi cok kisa ({} bayt)", len(raw)))
        attributes, path_length = struct.unpack("<IH", raw[:6])
        # Aciklama, bos karakterle biten UTF-16 dizedir; sonunu bulmak icin
        # 16 bitlik sifir aranir (tek bayt aramak cok baytli karakteri bolerdi).
        offset = 6
        end = offset
        while end + 1 < len(raw):
            if raw[end] == 0 and raw[end + 1] == 0:
                break
            end += 2
        description = raw[offset:end].decode("utf-16-le", "replace")
        offset = end + 2
        path_raw = raw[offset:offset + path_length]
        optional = raw[offset + path_length:]
        return cls(number=number, kind=kind, attributes=attributes,
                   description=description,
                   path_nodes=parse_device_path(path_raw),
                   optional_data=optional, var_attributes=var_attributes)

    def to_bytes(self) -> bytes:
        path_raw = build_device_path(self.path_nodes) if self.path_nodes else b""
        return (struct.pack("<IH", self.attributes, len(path_raw))
                + _utf16_bytes(self.description) + path_raw + self.optional_data)

    # -- disa aktarma -----------------------------------------------------
    def to_dict(self) -> Dict:
        """Yedek dosyasina yazilan sozluk gosterimi."""
        return {
            "name": self.name,
            "kind": self.kind,
            "number": self.number,
            "attributes": self.attributes,
            "description": self.description,
            "device_path": self.path_text,
            "optional_data": self.optional_data.hex(),
            "var_attributes": self.var_attributes,
            "raw": self.to_bytes().hex(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "LoadOption":
        """Yedekten geri okur.

        Ham baytlar varsa **onlar** kullanilir: metin gosterimi bilgi
        kaybeder (cozulemeyen dugumler onaltiliga doner), ham veri kayipsizdir.
        """
        raw = data.get("raw", "")
        kind = str(data.get("kind", "Boot"))
        number = int(data.get("number", 0))
        var_attributes = int(data.get("var_attributes", BOOT_ENTRY_ATTRIBUTES))
        if raw:
            return cls.parse(bytes.fromhex(raw), number=number, kind=kind,
                             var_attributes=var_attributes)
        return cls(number=number, kind=kind,
                   attributes=int(data.get("attributes", LOAD_OPTION_ACTIVE)),
                   description=str(data.get("description", "")),
                   optional_data=bytes.fromhex(data.get("optional_data", "")),
                   var_attributes=var_attributes)


def _printable(text: str) -> bool:
    """Metin gosterilebilir mi (denetim karakteri tasimiyor mu)?"""
    return all(ch in ("\t", "\n", "\r") or ch >= " " for ch in text)


# ==========================================================================
# Kisayol tusu (EFI_KEY_OPTION)
# ==========================================================================
SCAN_CODES = {
    0x01: "Up", 0x02: "Down", 0x03: "Right", 0x04: "Left", 0x05: "Home",
    0x06: "End", 0x07: "Insert", 0x08: "Delete", 0x09: "PageUp",
    0x0A: "PageDown", 0x0B: "F1", 0x0C: "F2", 0x0D: "F3", 0x0E: "F4",
    0x0F: "F5", 0x10: "F6", 0x11: "F7", 0x12: "F8", 0x13: "F9", 0x14: "F10",
    0x15: "F11", 0x16: "F12", 0x17: "Esc",
}


@dataclass
class KeyOption:
    """`Key0000` gibi bir kisayol tanimi (UEFI 3.1.6).

        UINT32 KeyData
        UINT32 BootOptionCrc
        UINT16 BootOption
        EFI_INPUT_KEY Keys[]        (her biri 4 bayt)
    """

    number: int = 0
    key_data: int = 0
    boot_option_crc: int = 0
    boot_option: int = 0
    keys: List[Tuple[int, str]] = field(default_factory=list)
    var_attributes: int = BOOT_ENTRY_ATTRIBUTES

    @property
    def name(self) -> str:
        return f"Key{self.number:04X}"

    @property
    def key_count(self) -> int:
        return (self.key_data >> 30) & 0x3

    @property
    def shift(self) -> bool:
        return bool(self.key_data & (1 << 8))

    @property
    def control(self) -> bool:
        return bool(self.key_data & (1 << 9))

    def modifiers(self) -> List[str]:
        """Basili degistirici tuslar: `["Ctrl", "Alt", "Shift"]`.

        Ayri ayri `control`/`alt`/`shift` ozellikleri yerine tek liste
        veriliyor; `alt` adi `tests.platform_check` icin Turkce bir sozcukle
        cakisiyor ve zaten uc bayragin tek kullanimi bu listeyi kurmakti.
        """
        names = []
        if self.control:
            names.append("Ctrl")
        if self.key_data & (1 << 10):
            names.append("Alt")
        if self.shift:
            names.append("Shift")
        return names

    def text(self) -> str:
        """Okunabilir kisayol gosterimi (`Ctrl+Alt+F1` gibi)."""
        parts = self.modifiers()
        for scan, char in self.keys:
            if scan:
                parts.append(SCAN_CODES.get(scan, f"Scan({scan:#x})"))
            elif char:
                parts.append(char.upper())
        return "+".join(parts) if parts else "-"

    @classmethod
    def parse(cls, raw: bytes, number: int = 0,
              var_attributes: int = BOOT_ENTRY_ATTRIBUTES) -> "KeyOption":
        if len(raw) < 10:
            raise EfiBootError(tr("Kisayol tanimi cok kisa ({} bayt)", len(raw)))
        key_data, crc, option = struct.unpack("<IIH", raw[:10])
        keys = []
        count = (key_data >> 30) & 0x3
        for index in range(count):
            offset = 10 + index * 4
            if offset + 4 > len(raw):
                break
            scan, char = struct.unpack("<HH", raw[offset:offset + 4])
            keys.append((scan, chr(char) if char else ""))
        return cls(number=number, key_data=key_data, boot_option_crc=crc,
                   boot_option=option, keys=keys, var_attributes=var_attributes)

    def to_bytes(self) -> bytes:
        body = struct.pack("<IIH", self.key_data, self.boot_option_crc,
                           self.boot_option)
        for scan, char in self.keys:
            body += struct.pack("<HH", scan, ord(char[0]) if char else 0)
        return body

    def to_dict(self) -> Dict:
        return {"name": self.name, "number": self.number,
                "key_data": self.key_data, "boot_option": self.boot_option,
                "text": self.text(), "raw": self.to_bytes().hex(),
                "var_attributes": self.var_attributes}


# ==========================================================================
# Sira listeleri
# ==========================================================================
def parse_order(raw: bytes) -> List[int]:
    """`BootOrder` gibi bir UINT16 dizisini sayi listesine cevirir."""
    count = len(raw) // 2
    return list(struct.unpack(f"<{count}H", raw[:count * 2])) if count else []


def build_order(numbers: List[int]) -> bytes:
    """Sayi listesini `BootOrder` bicimine cevirir."""
    return struct.pack(f"<{len(numbers)}H", *numbers) if numbers else b""


def parse_uint16(raw: bytes) -> Optional[int]:
    """`Timeout` / `BootNext` gibi tek sayilik degiskenler."""
    if len(raw) < 2:
        return None
    return struct.unpack("<H", raw[:2])[0]


def build_uint16(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)

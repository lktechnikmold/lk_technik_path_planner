# -*- coding: utf-8 -*-
"""
CNH CN1-Format (Case IH / Steyr / New Holland): Lesen und Schreiben der
Binär-Datensätze.

Die Struktur wurde anhand von Exporten aus dem CNH Operations Center
nachvollzogen; ein Nachbau aus den decodierten Werten war byte-identisch.

Ordnerstruktur:
    <ID>.cn1/
        index.vy1                                   Kennung des Datenträgers
        shared/growers.gws                          alle Kunden
        shared/<Betrieb-ID>.FMS                     Betrieb
        shared/<Betrieb-ID>.FMD/<Feld-ID>.FLS       Feld (Name + Fläche)
        shared/<Betrieb-ID>.FMD/<Feld-ID>.FLD/
            <Feld-ID>.bnd                           Feldgrenze (ein Ring)
            <Feld-ID>.wob                           Hindernisse (Punkte/Flächen)
            <Spur-ID>.ags                           je Fahrspur eine Datei

Jeder Datensatz:
    "CNH1" | u32 Länge Nutzdaten | u8 Prüfsumme | u8 Typ | u8 Version |
    ID[8] | Eltern-ID[8] | Zeit[6] (Jahr-1970, Monat, Tag, Std, Min, Sek; UTC) |
    Nutzdaten
    Prüfsumme = Summe aller Bytes ab dem Typ-Byte (inkl. Nutzdaten) mod 256.
Mehrere Datensätze können direkt hintereinander in einer Datei stehen.

Koordinaten: i16 Breite/Länge des Ursprungs in ganzen Grad, u16 Anzahl,
dann je Punkt i32 Breite, i32 Länge in 1e-8 Grad relativ zum Ursprung.
Namen: feste 18 Byte (ohne Pflicht-Nullterminator).
"""

import datetime
import hashlib
import os
import random
import struct

MAGIC = b"CNH1"
HEADER_LEN = 33
NAME_LEN = 18

# Datensatz-Typen und die dazu beobachteten Versionsnummern
T_INDEX, V_INDEX = 0x00, 1
T_GROWER, V_GROWER = 0x25, 2
T_FARM, V_FARM = 0x0A, 2
T_FIELD, V_FIELD = 0x08, 3
T_BOUNDARY, V_BOUNDARY = 0x73, 2
T_OBJECT, V_OBJECT = 0x70, 2
T_GUIDANCE, V_GUIDANCE = 0x24, 8

# Hindernis-Art (wob) und Spur-Art (ags)
OBJ_POINT = 0
OBJ_AREA = 2
LINE_AB = 0
LINE_CURVE = 3

_ID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ID_MAX = 36 ** 8


# ------------------------------------------------------------------
# IDs
# ------------------------------------------------------------------
def _b36(n):
    s = ""
    for _ in range(8):
        n, r = divmod(n, 36)
        s = _ID_CHARS[r] + s
    return s


def stable_id(*parts):
    """8-stellige ID, die für dieselben Eingaben immer gleich ist."""
    h = hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).digest()  # nosec B324 - keine Kryptografie
    return _b36(int.from_bytes(h[:8], "big") % _ID_MAX)


def new_root_ids():
    """(Ordner-ID, Index-ID). Die Index-ID ist im Original die Ordner-ID + 1296 (Basis 36)."""
    n = random.randrange(0, _ID_MAX - 1296)  # nosec B311 - keine Kryptografie
    return _b36(n), _b36(n + 1296)


def _id_bytes(value):
    if value is None:
        return b"\0" * 8
    if isinstance(value, int):
        return struct.pack("<Q", value)
    return value.encode("ascii")[:8].ljust(8, b"\0")


def id_text(raw):
    """ID-Bytes eines Datensatzes als Text (leer bei reinen Null-Bytes)."""
    return raw.split(b"\0")[0].decode("ascii", "replace")


# ------------------------------------------------------------------
# Namen
# ------------------------------------------------------------------
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def cn1_name(text):
    """Name für CN1: Umlaute umschreiben, nur ASCII, höchstens 18 Zeichen."""
    s = " ".join(str(text or "").split()).translate(_TRANSLIT)
    s = s.encode("ascii", "replace").decode("ascii")
    return s[:NAME_LEN]


def name_bytes(text):
    return cn1_name(text).encode("ascii")[:NAME_LEN].ljust(NAME_LEN, b"\0")


def name_text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip()


# ------------------------------------------------------------------
# Datensätze
# ------------------------------------------------------------------
def _timestamp(ts=None):
    ts = ts or datetime.datetime.now(datetime.timezone.utc)
    return bytes([ts.year - 1970, ts.month, ts.day, ts.hour, ts.minute, ts.second])


def encode_record(rtype, version, rid, parent, payload, ts=None):
    body = bytes([rtype, version]) + _id_bytes(rid) + _id_bytes(parent) + _timestamp(ts) + payload
    return MAGIC + struct.pack("<I", len(payload)) + bytes([sum(body) & 0xFF]) + body


def decode_records(data):
    """Liefert dicts mit type, version, id, parent, payload, checksum_ok."""
    out = []
    off = 0
    while off + HEADER_LEN <= len(data):
        if data[off:off + 4] != MAGIC:
            break
        ln = struct.unpack_from("<I", data, off + 4)[0]
        end = off + HEADER_LEN + ln
        if end > len(data):
            break
        rec = data[off:end]
        out.append({
            "type": rec[9],
            "version": rec[10],
            "id": rec[11:19],
            "parent": rec[19:27],
            "payload": rec[HEADER_LEN:],
            "checksum_ok": (sum(rec[9:]) & 0xFF) == rec[8],
        })
        off = end
    return out


def read_records(path):
    try:
        with open(path, "rb") as fh:
            return decode_records(fh.read())
    except OSError:
        return []


# ------------------------------------------------------------------
# Koordinaten
# ------------------------------------------------------------------
def encode_coords(points):
    """points: [(lat, lon), ...] in WGS84."""
    lat0 = int(min(p[0] for p in points) // 1)
    lon0 = int(min(p[1] for p in points) // 1)
    out = struct.pack("<hhH", lat0, lon0, len(points))
    for lat, lon in points:
        out += struct.pack("<ii", int(round((lat - lat0) * 1e8)), int(round((lon - lon0) * 1e8)))
    return out


def decode_coords(payload, offset):
    """-> ([(lat, lon), ...], Offset nach den Punkten)."""
    lat0, lon0, n = struct.unpack_from("<hhH", payload, offset)
    pts = []
    pos = offset + 6
    for _ in range(n):
        a, b = struct.unpack_from("<ii", payload, pos)
        pts.append((lat0 + a / 1e8, lon0 + b / 1e8))
        pos += 8
    return pts, pos


def _points_after_count(payload, count_offset, lat0, lon0):
    n = struct.unpack_from("<H", payload, count_offset)[0]
    pts = []
    pos = count_offset + 2
    for _ in range(n):
        a, b = struct.unpack_from("<ii", payload, pos)
        pts.append((lat0 + a / 1e8, lon0 + b / 1e8))
        pos += 8
    return pts


# ------------------------------------------------------------------
# Nutzdaten der einzelnen Typen
# ------------------------------------------------------------------
def grower_payload(name):
    return name_bytes(name)


def farm_payload(name, grower_id, grower_name):
    return name_bytes(name) + _id_bytes(grower_id) + name_bytes(grower_name)


def field_payload(name, area_m2):
    area = max(0, min(int(round(area_m2 * 10)), 0xFFFFFFFF))   # Einheit 0,1 m²
    return name_bytes(name) + struct.pack("<I", area)


def geometry_payload(name, kind, points):
    """Feldgrenze (kind 0) bzw. Hindernis (kind 0 = Punkt, 2 = Fläche)."""
    return b"\0" + name_bytes(name) + struct.pack("<H", kind) + encode_coords(points)


def parse_geometry_payload(payload):
    """-> (name, kind, [(lat, lon), ...])"""
    name = name_text(payload[1:1 + NAME_LEN])
    kind = struct.unpack_from("<H", payload, 1 + NAME_LEN)[0]
    pts, _ = decode_coords(payload, 3 + NAME_LEN)
    return name, kind, pts


def guidance_payload(line_id, name, kind, heading_deg, points):
    lat0 = int(min(p[0] for p in points) // 1)
    lon0 = int(min(p[1] for p in points) // 1)
    p = b"\0" + _id_bytes(line_id) + name_bytes(name) + struct.pack("<I", kind)
    p += b"\0" * 9 + b"\x0f" + b"\0" * 4       # Bedeutung unbekannt, im Original immer so
    p += struct.pack("<i", int(round((heading_deg or 0.0) * 1e5)))
    p += b"\0" * 14
    p += struct.pack("<hh", lat0, lon0)
    p += b"\0" * 32
    p += struct.pack("<H", len(points))
    for lat, lon in points:
        p += struct.pack("<ii", int(round((lat - lat0) * 1e8)), int(round((lon - lon0) * 1e8)))
    return p


def parse_guidance_payload(payload):
    """-> (name, kind, heading_deg, [(lat, lon), ...])"""
    name = name_text(payload[9:9 + NAME_LEN])
    kind = struct.unpack_from("<I", payload, 27)[0]
    heading = struct.unpack_from("<i", payload, 45)[0] / 1e5
    lat0, lon0 = struct.unpack_from("<hh", payload, 63)
    pts = _points_after_count(payload, 99, lat0, lon0)
    return name, kind, heading, pts


# ------------------------------------------------------------------
# Ordner finden
# ------------------------------------------------------------------
def find_cn1_root(path):
    """Liefert den <ID>.cn1-Ordner zu einem Pfad (Ordner, .cn1-Ordner oder index.vy1) oder None."""
    if not path:
        return None
    if os.path.isfile(path) and os.path.basename(path).lower() == "index.vy1":
        return os.path.dirname(path)
    if not os.path.isdir(path):
        return None
    if os.path.isfile(os.path.join(path, "index.vy1")) and os.path.isdir(os.path.join(path, "shared")):
        return path
    try:
        for n in sorted(os.listdir(path)):
            p = os.path.join(path, n)
            if n.lower().endswith(".cn1") and os.path.isfile(os.path.join(p, "index.vy1")):
                return p
    except OSError:
        pass
    return None


def extract_cn1_zip(zip_path):
    """
    Entpackt eine ZIP (z. B. Download aus dem CNH Operations Center) in einen
    temporären Ordner und liefert den darin enthaltenen CN1-Ordner oder None.
    """
    import tempfile
    import zipfile
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if not any(n.lower().endswith("index.vy1") for n in names):
                return None
            dest = tempfile.mkdtemp(prefix="lk_cn1_")
            dest_abs = os.path.abspath(dest)
            for n in names:
                target = os.path.abspath(os.path.join(dest, n))
                # Schutz gegen Pfade außerhalb des Zielordners ("zip slip")
                if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
                    return None
            zf.extractall(dest)
    except (OSError, zipfile.BadZipFile):
        return None
    for dirpath, _dirs, files in os.walk(dest):
        if "index.vy1" in files:
            return find_cn1_root(dirpath)
    return None


def existing_cn1_dirs(out_dir):
    """Alle CN1-Ordner direkt im Zielordner."""
    res = []
    try:
        for n in os.listdir(out_dir):
            p = os.path.join(out_dir, n)
            if n.lower().endswith(".cn1") and os.path.isfile(os.path.join(p, "index.vy1")):
                res.append(p)
    except OSError:
        pass
    return res

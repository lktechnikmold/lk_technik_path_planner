# -*- coding: utf-8 -*-
"""
AGCO-KML-Format (Fendt / Valtra / Massey Ferguson), aus Exporten eines Fendt
nachgebaut.

Der Fendt exportiert je Feld eine ZIP "Field_<Name>.zip" mit:
    Field_<Name kodiert>.kml   Geometrie (KML 2.2, UTF-8 mit BOM)
    Field_<Name kodiert>.ini   Feldname, UUID, Formatversion (QSettings-INI)
    TASKDATA/...               dasselbe Feld als ISOXML ("Hybrid")
Im Dateinamen innerhalb der ZIP sind Leerzeichen durch "_" ersetzt und
Sonderzeichen als UTF-8 %-kodiert (Grünland -> Gr%C3%BCnland).

Placemarks unterscheiden sich über ExtendedData "MapType":
    5  Feldgrenze          LineString (geschlossen), genau eine je Feld
    3  Flächenhindernis    Polygon, nur Außenring; Löcher der Feldgrenze werden
                           zu unbenannten Flächenhindernissen
    2  Punkthindernis      Point, nicht befahrbar (Symbol "Caution")
    0  Markierung          Point, befahrbar (Pushpin, ColorId 4)
    1  Fahrspur            LineString, "WaylineType":
                             0 = AB-Linie: A, B, dann A - 100 m und von dort
                                 1000 m in Richtung A (geodätisch, WGS84)
                             3 = Kurve: erster Punkt doppelt
Placemarks ohne MapType sind nur Anzeigesymbole (A/B-Fähnchen an den Spuren,
Warnsymbol am ersten Punkt eines Flächenhindernisses) und werden beim Import
übergangen.

Kunde und Betrieb stehen nicht im KML, nur in der TASKDATA der ZIP. Die Md5 in
der .ini lässt sich aus den Dateien nicht nachrechnen und wird nicht
geschrieben. Kontursegmente und befahrbare Flächenhindernisse kennt das KML
nicht.
"""

import io
import os
import re
import urllib.parse
import zipfile
from xml.sax.saxutils import escape

# Nur zum Parsen fremder Dateien - gehärtete Variante gegen XXE/Entity-Expansion
try:
    from .defusedxml import ElementTree as _SafeET
except Exception:
    from defusedxml import ElementTree as _SafeET

KML_NS = "{http://www.opengis.net/kml/2.2}"

MAP_MARKER = 0          # befahrbarer Punkt
MAP_WAYLINE = 1
MAP_POINT_OBSTACLE = 2
MAP_AREA_OBSTACLE = 3
MAP_BOUNDARY = 5

WAYLINE_AB = 0
WAYLINE_CURVE = 3

AB_BACK_M = 100.0       # 3. Punkt einer AB-Linie: so weit vor A
AB_LENGTH_M = 1000.0    # 4. Punkt: so weit hinter dem 3. Punkt

DEFAULT_CUSTOMER = "AGCO KML"
DEFAULT_FARM = "Import"


# ---------------------------------------------------------------------------
# Namen
# ---------------------------------------------------------------------------
def file_stem(name):
    """Dateiname in der ZIP: Field_ + Name, Leerzeichen -> _, Rest %-kodiert."""
    text = "_".join(str(name or "").split()) or "Feld"
    return "Field_" + urllib.parse.quote(text, safe="")


def zip_name(name):
    """Name der ZIP-Datei (wie beim Fendt unkodiert, aber Windows-tauglich)."""
    text = "_".join(str(name or "").split()) or "Feld"
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).rstrip(". ")
    return f"Field_{text or 'Feld'}.zip"


def name_from_stem(stem):
    """Umkehrung von file_stem (Rückfallebene, wenn das KML keinen Namen hat)."""
    if stem.startswith("Field_"):
        stem = stem[len("Field_"):]
    return urllib.parse.unquote(stem).replace("_", " ").strip()


def _ini_escape(text):
    """Wert wie QSettings in eine INI schreibt (Nicht-ASCII als \\xNN)."""
    out = []
    after_hex = False
    for ch in text:
        code = ord(ch)
        if ch in '\\"':
            out.append("\\" + ch)
            after_hex = False
        elif code < 0x20 or code >= 0x7F:
            out.append(f"\\x{code:x}")
            after_hex = True
        elif after_hex and ch in "0123456789abcdefABCDEF":
            # Hexziffer direkt nach einer \x-Folge würde mitgelesen -> ebenfalls kodieren
            out.append(f"\\x{code:x}")
        else:
            out.append(ch)
            after_hex = False
    value = "".join(out)
    if re.search(r"[;,=]", value) or value != value.strip():
        value = f'"{value}"'
    return value


def _ini_unescape(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "x":
                m = re.match(r"[0-9a-fA-F]{1,4}", value[i + 2:])
                if m:
                    out.append(chr(int(m.group(0), 16)))
                    i += 2 + len(m.group(0))
                    continue
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def build_ini(name, uuid_text):
    lines = ["[General]", "ExportFormat=Hybrid", f"NAME={_ini_escape(name)}", "TYPE=1",
             f"UUID={uuid_text}", "Version=2.1"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def ini_name(data):
    """Feldname aus einer .ini (bytes) oder ""."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    for line in text.splitlines():
        if line.startswith("NAME="):
            return _ini_unescape(line[5:])
    return ""


# ---------------------------------------------------------------------------
# KML lesen
# ---------------------------------------------------------------------------
def _coords(el):
    """<coordinates>-Text -> [(lon, lat), ...]."""
    pts = []
    for tok in (el.text or "").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return pts


def _dedupe(pts):
    out = []
    for p in pts:
        if not out or p != out[-1]:
            out.append(p)
    return out


def _closed(ring):
    ring = _dedupe(ring)
    if len(ring) >= 3 and ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    return ring if len(ring) >= 4 else None


def _polygons(pm):
    """Alle Polygone eines Placemarks als [[außen, loch, ...], ...] (lon, lat)."""
    out = []
    for poly in pm.iter(KML_NS + "Polygon"):
        rings = []
        for tag in ("outerBoundaryIs", "innerBoundaryIs"):
            for b in poly.findall(KML_NS + tag):
                for c in b.iter(KML_NS + "coordinates"):
                    r = _closed(_coords(c))
                    if r:
                        rings.append(r)
        if rings:
            out.append(rings)
    in_polygon = {id(r) for p in pm.iter(KML_NS + "Polygon") for r in p.iter(KML_NS + "LinearRing")}
    for tag in ("LineString", "LinearRing"):
        for ls in pm.iter(KML_NS + tag):
            if id(ls) in in_polygon:
                continue
            for c in ls.iter(KML_NS + "coordinates"):
                r = _closed(_coords(c))
                if r:
                    out.append([r])
    return out


def parse_kml(data):
    """
    KML (bytes) -> {"name", "boundaries": [[außen, loch...]], "areas": [(name, [außen, loch...])],
                    "points": [(name, befahrbar, (lon, lat))], "lines": [(name, typ, [(lon, lat)])]}
    """
    root = _SafeET.fromstring(data)
    doc = root.find(KML_NS + "Document")
    if doc is None:
        doc = root
    res = {"name": (doc.findtext(KML_NS + "name") or "").strip(),
           "boundaries": [], "areas": [], "points": [], "lines": []}
    for pm in doc.iter(KML_NS + "Placemark"):
        data_ = {d.get("name"): (d.findtext(KML_NS + "value") or "").strip()
                 for d in pm.iter(KML_NS + "Data")}
        try:
            map_type = int(data_.get("MapType", ""))
        except ValueError:
            continue                        # Anzeigesymbol (A/B-Fähnchen, Warnsymbol)
        name = (pm.findtext(KML_NS + "name") or "").strip()
        if map_type == MAP_BOUNDARY:
            res["boundaries"].extend(_polygons(pm))
        elif map_type == MAP_AREA_OBSTACLE:
            for rings in _polygons(pm):
                res["areas"].append((name, rings))
        elif map_type in (MAP_POINT_OBSTACLE, MAP_MARKER):
            for pt in pm.iter(KML_NS + "Point"):
                for c in pt.iter(KML_NS + "coordinates"):
                    pts = _coords(c)
                    if pts:
                        res["points"].append((name, 1 if map_type == MAP_MARKER else 0, pts[0]))
        elif map_type == MAP_WAYLINE:
            try:
                kind = int(data_.get("WaylineType", WAYLINE_CURVE))
            except ValueError:
                kind = WAYLINE_CURVE
            for ls in pm.iter(KML_NS + "LineString"):
                for c in ls.iter(KML_NS + "coordinates"):
                    pts = _coords(c)
                    # AB: nur A und B, die Verlängerungspunkte dienen nur der Anzeige
                    pts = pts[:2] if kind == WAYLINE_AB else _dedupe(pts)
                    if len(pts) > 2 or (len(pts) == 2 and pts[0] != pts[1]):
                        res["lines"].append((name, kind, pts))
    return res


def taskdata_farm(data, field_name=""):
    """(Kunde, Betrieb) aus einer TASKDATA.XML (bytes) oder (None, None)."""
    try:
        root = _SafeET.fromstring(data)
    except Exception:
        return None, None
    ctr = {e.get("A"): (e.get("B") or "").strip() for e in root.iter("CTR")}
    frm = {e.get("A"): (e.get("B") or "").strip() for e in root.iter("FRM")}
    pfds = list(root.iter("PFD"))
    match = [p for p in pfds if (p.get("C") or "").strip() == field_name] or pfds
    if not match:
        return None, None
    p = match[0]
    return ctr.get(p.get("E")) or None, frm.get(p.get("F")) or None


def read_sources(path):
    """
    Sammelt KML-Felder aus einer .kml, einer Fendt-ZIP oder einem Ordner (auch
    mehreren ZIPs). -> [(kml_bytes, ini_bytes|None, taskdata_bytes|None, stem)]
    """
    out = []

    def _from_zip(zpath):
        try:
            zf = zipfile.ZipFile(zpath)
        except (zipfile.BadZipFile, OSError):
            return
        with zf:
            names = zf.namelist()
            lower = {n.lower(): n for n in names}
            task = next((n for n in names if n.replace("\\", "/").lower().endswith("taskdata/taskdata.xml")), None)
            task_data = zf.read(task) if task else None
            for n in names:
                if not n.lower().endswith(".kml"):
                    continue
                stem = n[:-4]
                ini = lower.get(stem.lower() + ".ini")
                out.append((zf.read(n), zf.read(ini) if ini else None, task_data,
                            os.path.basename(stem.replace("\\", "/"))))

    def _from_file(kpath):
        stem = os.path.splitext(kpath)[0]
        with open(kpath, "rb") as fh:
            kml = fh.read()
        ini = task = None
        if os.path.isfile(stem + ".ini"):
            with open(stem + ".ini", "rb") as fh:
                ini = fh.read()
        tpath = os.path.join(os.path.dirname(kpath), "TASKDATA", "TASKDATA.XML")
        if os.path.isfile(tpath):
            with open(tpath, "rb") as fh:
                task = fh.read()
        out.append((kml, ini, task, os.path.basename(stem)))

    if os.path.isdir(path):
        for dirpath, _dirs, files in os.walk(path):
            for fn in sorted(files):
                full = os.path.join(dirpath, fn)
                if fn.lower().endswith(".kml"):
                    _from_file(full)
                elif fn.lower().endswith(".zip"):
                    _from_zip(full)
    elif path.lower().endswith(".zip"):
        _from_zip(path)
    elif path.lower().endswith(".kml"):
        _from_file(path)
    return out


def zip_has_kml(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return any(n.lower().endswith(".kml") for n in zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def has_kml(path):
    """True, wenn der Ordner (rekursiv) eine .kml oder eine ZIP mit .kml enthält."""
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            low = fn.lower()
            if low.endswith(".kml") or (low.endswith(".zip") and zip_has_kml(os.path.join(dirpath, fn))):
                return True
    return False


# ---------------------------------------------------------------------------
# KML schreiben (Aufbau wie beim Fendt)
# ---------------------------------------------------------------------------
_STYLES = """  <Style id="PaddleA">
    <IconStyle>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/paddle/A.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="PaddleB">
    <IconStyle>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/paddle/B.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="PaddlePlayGreen">
    <IconStyle>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/paddle/go.png</href>
      </Icon>
    </IconStyle>
    <LabelStyle>
      <scale>0</scale>
    </LabelStyle>
  </Style>
  <Style id="PaddleStopGreen">
    <IconStyle>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/paddle/grn-square.png</href>
      </Icon>
    </IconStyle>
    <LabelStyle>
      <scale>0</scale>
    </LabelStyle>
  </Style>
  <Style id="PaddleCircle">
    <IconStyle>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="Caution">
    <IconStyle>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/shapes/caution.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="Pushpinff0000">
    <IconStyle>
      <color>ff0000ff</color>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/pushpin/wht-pushpin.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="Pushpin0000ff">
    <IconStyle>
      <color>ffff0000</color>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/pushpin/wht-pushpin.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="Pushpin00ff00">
    <IconStyle>
      <color>ff00ff00</color>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/pushpin/wht-pushpin.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="Pushpinffff00">
    <IconStyle>
      <color>ff00ffff</color>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/pushpin/wht-pushpin.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="Pushpin000000">
    <IconStyle>
      <color>ff000000</color>
      <Icon>
        <href>http://maps.google.com/mapfiles/kml/pushpin/wht-pushpin.png</href>
      </Icon>
    </IconStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="AreaObstacleStyle">
    <LineStyle>
      <color>960000ff</color>
      <width>1</width>
    </LineStyle>
    <PolyStyle>
      <color>960000ff</color>
    </PolyStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="BoundaryStyle">
    <LineStyle>
      <color>ffff0000</color>
      <width>2</width>
    </LineStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="WaylineStyle">
    <LineStyle>
      <color>ff0d9eda</color>
      <width>2</width>
    </LineStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
  <Style id="WorkedAreaStyle">
    <LineStyle>
      <width>0</width>
    </LineStyle>
    <BalloonStyle>
      <displayMode>hide</displayMode>
    </BalloonStyle>
  </Style>
"""


def _num(v):
    text = f"{v:.9f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _coord_block(pts, indent):
    pad = " " * indent
    return "".join(f"{pad}{_num(lon)},{_num(lat)},0\n" for lon, lat in pts)


def _name_el(name):
    return f"  <name>{escape(name)}</name>\n" if name else "  <name/>\n"


def _data(items):
    body = "".join(f'    <Data name="{k}">\n      <value>{v}</value>\n    </Data>\n' for k, v in items)
    return f"  <ExtendedData>\n{body}  </ExtendedData>\n"


def _point(name, style, pt, data=None):
    return ("<Placemark>\n" + _name_el(name) + f"  <styleUrl>{style}</styleUrl>\n"
            + (_data(data) if data else "")
            + "  <Point>\n    <coordinates>\n" + _coord_block([pt], 6)
            + "    </coordinates>\n  </Point>\n</Placemark>\n")


def _line(name, style, pts, data):
    return ("<Placemark>\n" + _name_el(name) + f"  <styleUrl>{style}</styleUrl>\n" + _data(data)
            + "  <LineString>\n    <tessellate>1</tessellate>\n    <altitudeMode>clampToGround</altitudeMode>\n"
            + "    <coordinates>\n" + _coord_block(pts, 6) + "    </coordinates>\n  </LineString>\n</Placemark>\n")


def _polygon(name, ring):
    return ("<Placemark>\n" + _name_el(name) + "  <styleUrl>AreaObstacleStyle</styleUrl>\n"
            + _data([("MapType", MAP_AREA_OBSTACLE), ("GisRefAlt", 0)])
            + "  <Polygon>\n    <tessellate>1</tessellate>\n    <altitudeMode>clampToGround</altitudeMode>\n"
            + "    <outerBoundaryIs>\n      <LinearRing>\n        <coordinates>\n" + _coord_block(ring, 10)
            + "        </coordinates>\n      </LinearRing>\n    </outerBoundaryIs>\n  </Polygon>\n</Placemark>\n")


def build_kml(name, boundary=None, areas=(), points=(), lines=()):
    """
    boundary: geschlossener Ring [(lon, lat)] oder None
    areas:    [(name, Ring)]
    points:   [(name, befahrbar, (lon, lat))]
    lines:    [(name, typ, [(lon, lat)])] - AB bereits mit 4 Punkten
    -> bytes (UTF-8 mit BOM)
    """
    parts = ['<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">\n',
             "<Document>\n", f"  <name>{escape(name)}</name>\n", _STYLES, "\n"]
    if boundary:
        parts.append(_line("", "BoundaryStyle", boundary, [("MapType", MAP_BOUNDARY), ("GisRefAlt", 0)]))
    for nm, ring in areas:
        parts.append(_polygon(nm, ring))
    for nm, passable, pt in points:
        if passable:
            parts.append(_point(nm, "#Pushpin000000", pt, [("MapType", MAP_MARKER), ("ColorId", 4)]))
        else:
            parts.append(_point(nm, "Caution", pt, [("MapType", MAP_POINT_OBSTACLE)]))
    for nm, kind, pts in lines:
        if kind == WAYLINE_AB:
            data = [("MapType", MAP_WAYLINE), ("WaylineType", WAYLINE_AB), ("WaylineVersion", 2),
                    ("WaylineSource", 0), ("GisRefAlt", 0)]
            parts.append(_line(nm, "WaylineStyle", pts, data))
        else:
            data = [("MapType", MAP_WAYLINE), ("WaylineType", WAYLINE_CURVE), ("WaylineVersion", 1),
                    ("WaylineSource", 0), ("GisRefAlt", 0)]
            parts.append(_line(nm, "WaylineStyle", [pts[0]] + list(pts), data))
    # Anzeigesymbole ohne MapType (wie beim Fendt)
    for nm, ring in areas:
        parts.append(_point(nm, "Caution", ring[0]))
    for _nm, kind, pts in lines:
        parts.append(_point("", "PaddleA", pts[0]))
        parts.append(_point("", "PaddleB", pts[1] if kind == WAYLINE_AB else pts[-1]))
    parts.append("</Document>\n</kml>\n")
    return b"\xef\xbb\xbf" + "".join(parts).encode("utf-8")


def write_zip(path, field_name, kml, ini):
    stem = file_stem(field_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(stem + ".kml", kml)
        zf.writestr(stem + ".ini", ini)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())

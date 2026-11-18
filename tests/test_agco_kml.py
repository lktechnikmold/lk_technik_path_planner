# -*- coding: utf-8 -*-
"""AGCO KML (Fendt): Dateiaufbau, Export und Re-Import."""

import importlib
import math
import os
import zipfile
import xml.etree.ElementTree as ET  # nosec B405 - nur selbst erzeugte Testdateien

from qgis.core import QgsCoordinateReferenceSystem, QgsDistanceArea, QgsPointXY, QgsProject

from support import (LONG_NAME, PACKAGE, TEST_DATA, PluginTestCase, QMessageBox, catalog, clear_project, features,
                     groups, line_pts, ll, metric, rect)
from test_roundtrip import A, B, RoundtripBase

F = importlib.import_module(f"{PACKAGE}.agco_kml_format")

K = "{http://www.opengis.net/kml/2.2}"
KML = (F.DEFAULT_CUSTOMER, F.DEFAULT_FARM)
C = ("Testkunde C", "Testbetrieb C")
LONG_STEM = "Field_Gr%C3%BCnland_S%C3%BCd_%C3%96st_%C3%9F_Langname_1234"
ZIPS = ["Field_Flache_Kurve.zip", "Field_Grünland_Süd_Öst_ß_Langname_1234.zip", "Field_Rechteck_mit_Loch.zip",
        "Field_Streifen.zip", "Field_Zwei_Teile.zip"]


def read_zip(path):
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def placemarks(kml_bytes):
    out = []
    for pm in ET.fromstring(kml_bytes[3:]).iter(K + "Placemark"):
        data = {d.get("name"): d.findtext(K + "value") for d in pm.iter(K + "Data")}
        pts = [tuple(map(float, c.split(",")[:2])) for el in pm.iter(K + "coordinates") for c in el.text.split()]
        out.append((pm.findtext(K + "name") or "", pm.findtext(K + "styleUrl"), data, pts))
    return out


def add_taskdata(zip_path, ctr, frm, field_name):
    """Hängt eine TASKDATA mit Kunde/Betrieb an, wie sie der Fendt mitliefert."""
    root = ET.Element("ISO11783_TaskData")
    ET.SubElement(root, "CTR", {"A": "CTR1", "B": ctr})
    ET.SubElement(root, "FRM", {"A": "FRM7", "B": frm, "I": "CTR1"})
    ET.SubElement(root, "PFD", {"A": "PFD1", "C": field_name, "E": "CTR1", "F": "FRM7"})
    with zipfile.ZipFile(zip_path, "a") as zf:
        zf.writestr("TASKDATA/TASKDATA.XML", ET.tostring(root))


class TestAgcoKml(RoundtripBase):

    def _export(self, **kw):
        out = self.export("AGCO", "KML", **kw)
        self.assertIn("AGCO-KML-Export erstellt: 5 Feld(er)", self.last_msg())
        self.assertIn("1 Loch/Löcher", self.last_msg())
        return out

    def test_files_and_names(self):
        out = self._export()
        self.assertEqual(sorted(os.listdir(out)), ZIPS)
        files = read_zip(os.path.join(out, "Field_Grünland_Süd_Öst_ß_Langname_1234.zip"))
        self.assertEqual(sorted(files), [LONG_STEM + ".ini", LONG_STEM + ".kml"])
        ini = files[LONG_STEM + ".ini"].decode("ascii").splitlines()
        # Kodierung wie QSettings bzw. der Fendt ("d" nach \xfc ebenfalls als \x64)
        self.assertIn(r"NAME=Gr\xfcnland S\xfc\x64 \xd6st \xdf Langname 1234", ini)
        self.assertEqual([line.split("=")[0] for line in ini],
                         ["[General]", "ExportFormat", "NAME", "TYPE", "UUID", "Version"])
        kml = files[LONG_STEM + ".kml"]
        self.assertTrue(kml.startswith(b"\xef\xbb\xbf"), "UTF-8 mit BOM")
        self.assertEqual(ET.fromstring(kml[3:]).find(K + "Document").findtext(K + "name"), LONG_NAME)

    def test_same_uuid_on_reexport(self):
        out = self._export()
        first = read_zip(os.path.join(out, "Field_Streifen.zip"))["Field_Streifen.ini"]
        self.export("AGCO", "KML", out=out)
        self.assertEqual(read_zip(os.path.join(out, "Field_Streifen.zip"))["Field_Streifen.ini"], first)

    def test_kml_content(self):
        out = self._export()
        pms = placemarks(read_zip(os.path.join(out, "Field_Rechteck_mit_Loch.zip"))["Field_Rechteck_mit_Loch.kml"])
        typed = [(n, d["MapType"]) for n, _s, d, _p in pms if "MapType" in d]
        self.assertEqual(typed, [("", "5"), ("Teich", "3"), ("", "3"), ("", "3"), ("Stein", "2"), ("Fahne", "0"),
                                 ("AB Ost", "1"), ("Kurve West", "1"), ("Nord Gerade", "1"), ("Nord Kurve", "1")])
        # Anzeigesymbole: Warnsymbol je Fläche, A/B-Fähnchen je Spur
        styles = [s for _n, s, d, _p in pms if "MapType" not in d]
        self.assertEqual(styles, ["Caution"] * 3 + ["PaddleA", "PaddleB"] * 4)
        boundary = pms[0][3]
        self.assertEqual(len(boundary), 5, "ein Außenring ohne Loch")
        self.assertEqual(boundary[0], boundary[-1])

        da = QgsDistanceArea()
        da.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext())
        da.setEllipsoid("WGS84")
        ab = next(p for n, _s, d, p in pms if n == "AB Ost")
        a, b, c, e = (QgsPointXY(*p) for p in ab)
        self.assertEqual(next(d for n, _s, d, _p in pms if n == "AB Ost")["WaylineType"], "0")
        self.assertAlmostEqual(da.measureLine(a, c), 100.0, delta=0.01)
        self.assertAlmostEqual(da.measureLine(c, e), 1000.0, delta=0.01)
        turn = math.degrees(da.bearing(c, e) - da.bearing(a, b))
        self.assertAlmostEqual((turn + 180) % 360 - 180, 0.0, delta=0.01, msg="Verlängerung in Richtung A->B")
        curve = next(p for n, _s, d, p in pms if n == "Kurve West")
        self.assertEqual(curve[0], curve[1], "Kurve: erster Punkt doppelt")
        self.assertEqual(next(d for n, _s, d, _p in pms if n == "Kurve West")["WaylineType"], "3")

    def test_roundtrip(self):
        out = self._export()
        self.reimport(out)
        self.assertEqual(set(groups()), {KML})
        self.assertEqual(sorted(catalog(*KML).values()),
                         sorted(["Flache Kurve", LONG_NAME, "Rechteck mit Loch", "Streifen", "Zwei Teile"]))
        fid = self.field_id(KML, "Rechteck mit Loch")
        bounds = features(*KML, "Feldgrenzen", fid)
        self.assertEqual(len(bounds), 1)
        self.assertEqual(len(metric(bounds[0].geometry()).asMultiPolygon()[0]), 1, "Loch ist jetzt ein Hindernis")
        self.assertAlmostEqual(metric(bounds[0].geometry()).area(), 300 * 200, delta=60)
        two = features(*KML, "Feldgrenzen", self.field_id(KML, "Zwei Teile"))
        self.assertEqual(len(two), 1, "aneinanderliegende Grenzen vereinigt")
        self.assertAlmostEqual(metric(two[0].geometry()).area(), 400 * 200, delta=80)

        for f in features(*KML, "Fahrspuren", fid):
            self.assertLess(metric(f.geometry()).hausdorffDistance(self.ref_tracks[f["Name"]]), 0.02, f["Name"])
        self.assertEqual(sorted(f["Name"] for f in features(*KML, "Fahrspuren", fid)),
                         ["AB Ost", "Kurve West", "Nord Gerade", "Nord Kurve"])
        self.assertEqual(self.obstacles(KML, "Punkthindernis", fid), [("Fahne", 1), ("Stein", 0)])
        # befahrbare Fläche und Loch kommen als unbefahrbare, unbenannte Hindernisse zurück
        self.assertEqual(self.obstacles(KML, "Flaechenhindernis", fid), [(None, 0), (None, 0), ("Teich", 0)])

    def test_customer_from_taskdata(self):
        out = self._export()
        src = os.path.join(out, "Field_Streifen.zip")
        add_taskdata(src, A[0], A[1], "Streifen")
        self.reimport(src)
        self.assertEqual(set(groups()), {A})
        self.assertEqual(catalog(*A), {1: "Streifen"})

    def test_single_kml_file(self):
        out = self._export()
        files = read_zip(os.path.join(out, "Field_Zwei_Teile.zip"))
        loose = os.path.join(self.tmp, "lose")
        os.makedirs(loose)
        for name, data in files.items():
            with open(os.path.join(loose, name), "wb") as fh:
                fh.write(data)
        self.reimport(os.path.join(loose, "Field_Zwei_Teile.kml"))
        self.assertEqual(set(groups()), {KML})
        self.assertEqual(catalog(*KML), {1: "Zwei Teile"})

    def test_densify_curves_only(self):
        out = self.export("AGCO", "KML", densify=5.0)
        pms = placemarks(read_zip(os.path.join(out, "Field_Rechteck_mit_Loch.zip"))["Field_Rechteck_mit_Loch.kml"])
        pts = {n: p for n, _s, d, p in pms if d.get("MapType") == "1"}
        self.assertEqual(len(pts["AB Ost"]), 4, "AB-Linie bleibt A, B + Anzeigepunkte")
        self.assertGreater(len(pts["Kurve West"]), 30, "Kurve verdichtet")

    def test_overwrite_question(self):
        out = self._export()
        path = os.path.join(out, "Field_Streifen.zip")
        other = os.path.join(out, "Field_Anderes_Feld.zip")
        with open(other, "wb") as fh:
            fh.write(b"x")
        size = os.path.getsize(path)
        with open(path, "ab") as fh:
            fh.write(b"Markierung")
        self.msgs.clear()
        self.export("AGCO", "KML", out=out, answer=QMessageBox.StandardButton.No)
        self.assertEqual(os.path.getsize(path), size + len(b"Markierung"), "Nein: Datei bleibt unverändert")
        self.assertEqual(self.msgs, [])
        self.export("AGCO", "KML", out=out, answer=QMessageBox.StandardButton.Yes)
        self.assertEqual(os.path.getsize(path), size, "Ja: Datei wird neu geschrieben")
        self.assertTrue(os.path.exists(other), "andere Felder im Ordner bleiben erhalten")


# ---------------------------------------------------------------------------
# Künstliches KML im Aufbau eines Fendt-Exports: Anzeigesymbole ohne MapType,
# AB-Linie mit 4 Punkten, Kurve mit doppeltem Startpunkt, geschlossene
# Kreisspur, Feldgrenze als Polygon mit Loch
# ---------------------------------------------------------------------------
def _c(*xy):
    """Meter -> KML-Koordinaten (lon,lat,0)."""
    return " ".join("%.9f,%.9f,0" % (ll(x, y)[1], ll(x, y)[0]) for x, y in xy)


def _pm(name, map_type, geom, extra=""):
    data = f'<Data name="MapType"><value>{map_type}</value></Data>' if map_type is not None else ""
    return f"<Placemark><name>{name}</name><ExtendedData>{data}{extra}</ExtendedData>{geom}</Placemark>"


def _poly(outer, *holes):
    inner = "".join(f"<innerBoundaryIs><LinearRing><coordinates>{_c(*h)}</coordinates></LinearRing>"
                    "</innerBoundaryIs>" for h in holes)
    return (f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{_c(*outer)}</coordinates></LinearRing>"
            f"</outerBoundaryIs>{inner}</Polygon>")


def _wayline(kind, *xy):
    return (f"<LineString><coordinates>{_c(*xy)}</coordinates></LineString>",
            f'<Data name="WaylineType"><value>{kind}</value></Data>')


CIRCLE = [(100 + 30 * math.cos(math.radians(a)), 50 + 30 * math.sin(math.radians(a))) for a in range(0, 361, 30)]
FENDT_STYLE_KML = "".join([
    '﻿<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Handgeschrieben</name>',
    _pm("", 5, _poly([(0, 0), (200, 0), (200, 100), (0, 100), (0, 0)], [(50, 40), (70, 40), (70, 60), (50, 60)])),
    _pm("Graben", 3, _poly([(100, 10), (120, 10), (120, 20), (100, 10)])),
    _pm("Stein", 2, f"<Point><coordinates>{_c((10, 10))}</coordinates></Point>"),
    _pm("Fahne", 0, f"<Point><coordinates>{_c((20, 20))}</coordinates></Point>",
        '<Data name="ColorId"><value>4</value></Data>'),
    _pm("AB", 1, *_wayline(0, (5, 5), (195, 5), (-95, 5), (905, 5))),
    _pm("Kurve", 1, *_wayline(3, (5, 10), (5, 10), (50, 30), (100, 35))),
    _pm("Kreis", 1, *_wayline(3, *CIRCLE)),
    _pm("Graben", None, f"<Point><coordinates>{_c((100, 10))}</coordinates></Point>"),
    _pm("", None, f"<Point><coordinates>{_c((5, 5))}</coordinates></Point>"),
    "</Document></kml>",
])


class TestAgcoKmlFormat(PluginTestCase):
    """Dateiaufbau und Import ohne Umweg über den eigenen Export."""

    def test_parse_fendt_style(self):
        res = F.parse_kml(FENDT_STYLE_KML.encode("utf-8"))
        self.assertEqual(res["name"], "Handgeschrieben")
        self.assertEqual([len(r) for r in res["boundaries"][0]], [5, 5], "Außenring + geschlossenes Loch")
        self.assertEqual([n for n, _r in res["areas"]], ["Graben"])
        self.assertEqual([(n, p) for n, p, _pt in res["points"]], [("Stein", 0), ("Fahne", 1)])
        lines = {n: (k, len(p), p[0] == p[-1]) for n, k, p in res["lines"]}
        self.assertEqual(lines, {"AB": (0, 2, False), "Kurve": (3, 3, False), "Kreis": (3, 13, True)})

    def test_import_fendt_style_kml(self):
        path = os.path.join(self.tmp, "Field_Handgeschrieben.kml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(FENDT_STYLE_KML)
        self.assertIn("AGCO-KML-Daten wurden importiert", self.import_path(path))
        self.assertEqual(catalog(*KML), {1: "Handgeschrieben"})
        g = metric(features(*KML, "Feldgrenzen", 1)[0].geometry())
        self.assertAlmostEqual(g.area(), 200 * 100 - 20 * 20, delta=30, msg="Polygon-Grenze mit Loch")
        self.assertEqual(sorted(len(line_pts(f.geometry())) for f in features(*KML, "Fahrspuren", 1)), [2, 3, 13])
        self.assertEqual(len(features(*KML, "Punkthindernis", 1)), 2, "Anzeigesymbole werden übergangen")
        self.assertEqual(len(features(*KML, "Flaechenhindernis", 1)), 1)

    def test_names(self):
        for name in ("Grünland Süd", 'Feld "A"; B=1, C', " Rand ", "Ä\\x1"):
            self.assertEqual(F.ini_name(F.build_ini(name, "{x}")), name, name)
        self.assertEqual(F.file_stem("Feld 1/2 ä"), "Field_Feld_1%2F2_%C3%A4")
        self.assertEqual(F.name_from_stem("Field_Feld_1_%C3%A4"), "Feld 1 ä")
        self.assertEqual(F.zip_name('Feld: 1/2 "x"?'), "Field_Feld__1_2__x__.zip")
        self.assertEqual(F.zip_name("   "), "Field_Feld.zip")

    def test_unsupported_folder(self):
        empty = os.path.join(self.tmp, "leer")
        os.makedirs(empty)
        with open(os.path.join(empty, "notiz.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertIn("keine unterstützten Felddaten", self.import_path(empty))
        self.assertEqual(groups(), {})

    def test_same_name_in_two_farms_and_folder_import(self):
        data = TEST_DATA + [(C[0], C[1], [
            dict(id=1, name="Streifen", boundaries=[[rect(0, 2000, 100, 2100)]], tracks=[], points=[], areas=[])])]
        self.import_test_data(data)
        out = self.export("AGCO", "KML")
        self.assertIn("6 Feld(er)", self.last_msg())
        self.assertIn("Field_Streifen.zip", os.listdir(out))
        self.assertIn("Field_Streifen_2.zip", os.listdir(out))
        # Kunde/Betrieb je ZIP aus der TASKDATA, ZIPs ohne TASKDATA unter dem Standard
        add_taskdata(os.path.join(out, "Field_Streifen.zip"), B[0], B[1], "Streifen")
        add_taskdata(os.path.join(out, "Field_Streifen_2.zip"), C[0], C[1], "Streifen")
        add_taskdata(os.path.join(out, "Field_Zwei_Teile.zip"), A[0], A[1], "Zwei Teile")
        clear_project()
        self.assertIn("AGCO-KML-Daten wurden importiert", self.import_path(out, "reimport"))
        self.assertEqual(set(groups()), {A, B, C, KML})
        self.assertEqual(catalog(*A), {1: "Zwei Teile"})
        self.assertEqual(catalog(*B), {1: "Streifen"})
        self.assertEqual(catalog(*C), {1: "Streifen"})
        self.assertEqual(sorted(catalog(*KML).values()), sorted(["Flache Kurve", LONG_NAME, "Rechteck mit Loch"]))

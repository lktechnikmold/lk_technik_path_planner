# -*- coding: utf-8 -*-
"""
Export in jedes Format und Re-Import: Inhalt, Geometrie-Genauigkeit und die
Eigenheiten der einzelnen Formate.
"""

import math
import os
import shutil
import xml.etree.ElementTree as ET  # nosec B405 - nur selbst erzeugte Testdateien

from support import (LONG_NAME, PluginTestCase, Qt, QMessageBox, catalog, clear_project, cn1, features,
                     find_file, groups, metric, nkey, read_text, val)

A = ("Testkunde A", "Testbetrieb A")
B = ("Testkunde B", "Testbetrieb B")


class RoundtripBase(PluginTestCase):
    """Importiert die Testdaten und merkt sich Referenzgeometrien."""

    def setUp(self):
        super().setUp()
        self.import_test_data()
        self.ref_tracks = {f["Name"]: metric(f.geometry()) for f in features(*A, "Fahrspuren")}
        self.ref_bounds = {}
        for key in (A, B):
            for f in features(*key, "Feldgrenzen"):
                self.ref_bounds.setdefault((key, catalog(*key)[int(f["ID"])]), []).append(metric(f.geometry()))

    def reimport(self, src):
        clear_project()
        msg = self.import_path(src, "reimport")
        self.assertIn("importiert", msg)

    def field_id(self, key, name):
        ids = [i for i, n in catalog(*key).items() if n == name]
        self.assertEqual(len(ids), 1, f"Feld {name!r} nicht eindeutig im Katalog: {catalog(*key)}")
        return ids[0]

    def assert_track_geometry(self, max_dev=0.02):
        """Jede re-importierte Spur deckt sich mit dem Original (Abweichung in m)."""
        for f in features(*A, "Fahrspuren"):
            ref = self.ref_tracks[f["Name"]]
            self.assertLess(metric(f.geometry()).hausdorffDistance(ref), max_dev, f["Name"])

    def assert_boundary_areas(self, names=None, tol=0.002):
        for (key, name), geoms in self.ref_bounds.items():
            if names is not None and name not in names:
                continue
            fid = self.field_id(key, names[name] if isinstance(names, dict) else name)
            got = sorted(metric(f.geometry()).area() for f in features(*key, "Feldgrenzen", fid))
            ref = sorted(g.area() for g in geoms)
            self.assertEqual(len(got), len(ref), name)
            for a, b in zip(got, ref):
                self.assertAlmostEqual(a, b, delta=b * tol, msg=name)

    def obstacles(self, key, layer_name, fid):
        return sorted(((val(f["Name"]), val(f["befahrbar"]) if val(f["befahrbar"]) is None else int(f["befahrbar"]))
                       for f in features(*key, layer_name, fid)), key=nkey)


class TestIsoxml(RoundtripBase):

    def _check_common(self):
        self.assertEqual(set(groups()), {A, B})
        self.assertEqual(catalog(*A), {1: "Rechteck mit Loch", 2: "Zwei Teile", 3: LONG_NAME})
        self.assert_boundary_areas()
        self.assert_track_geometry(0.01)
        self.assertEqual(self.obstacles(A, "Punkthindernis", 1), [("Fahne", 1), ("Stein", 0)])
        self.assertEqual(self.obstacles(A, "Flaechenhindernis", 1), sorted([("Teich", 0), (None, 1)], key=nkey))
        g = features(*A, "Feldgrenzen", 1)[0].geometry()
        polys = g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]
        self.assertEqual(len(polys[0]), 2, "Loch bleibt erhalten")

    def test_v4(self):
        out = self.export("ISOXML", "v4")
        self.assertIn("TASKDATA.XML geschrieben", self.last_msg())
        xml = find_file(out, "TASKDATA.XML")
        root = ET.parse(xml).getroot()
        self.assertEqual(root.get("VersionMajor"), "4")
        self.assertTrue(all(p.get("E") for p in root.iter("PLN")), "v4: jede PLN hat eine ID (E)")
        self.assertTrue(all(p.get("G") for p in root.iter("PNT") if p.get("A") in ("1", "5")), "v4: PointId (G)")
        names = {p.get("B") for p in root.iter("PLN") if p.get("A") in ("6", "8")}
        self.assertIn("Teich", names)
        self.reimport(xml)
        self._check_common()
        # ohne Haken "Kontursegmente" werden Spuren eines Segments weggelassen
        self.assertEqual(sorted(f["Name"] for f in features(*A, "Fahrspuren", 1)), ["AB Ost", "Kurve West"])

    def test_v3(self):
        out = self.export("ISOXML", "v3")
        xml = find_file(out, "TASKDATA.XML")
        root = ET.parse(xml).getroot()
        self.assertEqual(root.get("VersionMajor"), "3")
        self.assertFalse(any(p.get("E") for p in root.iter("PLN")), "v3 kennt keine PLN-ID")
        self.assertFalse(any(p.get("G") for p in root.iter("PNT")), "v3 kennt keine PointId")
        self.assertTrue({p.get("A") for p in root.iter("PNT") if p.get("B")} <= {"1", "2"}, "v3: PointType 1/2")
        self.reimport(xml)
        self._check_common()
        # v3: Segment-Spuren werden als einzelne Spuren exportiert
        self.assertEqual(sorted(f["Name"] for f in features(*A, "Fahrspuren", 1)),
                         ["AB Ost", "Kurve West", "Nord Gerade", "Nord Kurve"])

    def test_overwrite_question(self):
        out = self.export("ISOXML", "v4")
        xml = find_file(out, "TASKDATA.XML")
        with open(xml, "a", encoding="utf-8") as fh:
            fh.write("<!-- Markierung -->")
        self.msgs.clear()
        self.export("ISOXML", "v4", out=out, answer=QMessageBox.StandardButton.No)
        self.assertIn("Markierung", read_text(xml), "Nein: Datei bleibt unverändert")
        self.assertEqual(self.msgs, [])
        self.export("ISOXML", "v4", out=out, answer=QMessageBox.StandardButton.Yes)
        self.assertNotIn("Markierung", read_text(xml), "Ja: Datei wird neu geschrieben")

    def test_partial_selection(self):
        # nur Feld 3 von Kunde A auswählen
        self.dlg.refresh_tree()
        tree = self.dlg.tree
        for i in range(tree.topLevelItemCount()):
            ctr = tree.topLevelItem(i)
            for j in range(ctr.childCount()):
                frm = ctr.child(j)
                for k in range(frm.childCount()):
                    fld = frm.child(k)
                    fld.setCheckState(0, Qt.CheckState.Checked if fld.text(0) == LONG_NAME
                                      else Qt.CheckState.Unchecked)
        sel = self.dlg.selected_export_map()
        self.assertEqual(sel, {"Testkunde A": {"Testbetrieb A": {3}}})
        out = os.path.join(self.tmp, "teil")
        os.makedirs(out)
        self.dlg.mode_export.setChecked(True)
        self.select_terminal("ISOXML", "v4")
        self.dlg.out_line.setText(out)
        self.plugin._on_run()
        root = ET.parse(find_file(out, "TASKDATA.XML")).getroot()
        self.assertEqual([p.get("C") for p in root.iter("PFD")], [LONG_NAME])


class TestGen4(RoundtripBase):

    def test_roundtrip(self):
        out = self.export("John Deere", "GS4")
        self.assertIn("Gen4 Export erstellt", self.last_msg())
        md = find_file(out, "MasterData.xml")
        ns = "{urn:schemas-johndeere-com:Setup}"
        root = ET.parse(md).getroot()
        self.assertEqual({c.get("Name") for c in root.iter(ns + "Client")}, {"Testkunde A", "Testkunde B"})
        self.assertEqual({f.get("Name") for f in root.iter(ns + "Field")},
                         {"Rechteck mit Loch", "Zwei Teile", LONG_NAME, "Streifen", "Flache Kurve"})
        flags = sorted(f.get("Name") for f in root.iter(ns + "Flag"))
        self.assertEqual(flags, sorted(["Stein", "Fahne", "Teich", "befahrbares Hindernis"]))
        self.reimport(os.path.dirname(md))
        self.assertEqual(set(groups()), {A, B})
        self.assertEqual(set(catalog(*A).values()), {"Rechteck mit Loch", "Zwei Teile", LONG_NAME})
        self.assert_boundary_areas()
        self.assert_track_geometry(0.02)
        fid = self.field_id(A, "Rechteck mit Loch")
        # Gen4 kennt keine Kontursegmente: alle Spuren kommen als einzelne Spuren
        self.assertEqual(sorted(f["Name"] for f in features(*A, "Fahrspuren", fid)),
                         ["AB Ost", "Kurve West", "Nord Gerade", "Nord Kurve"])
        self.assertEqual(self.obstacles(A, "Punkthindernis", fid), [("Fahne", 1), ("Stein", 0)])
        self.assertEqual(self.obstacles(A, "Flaechenhindernis", fid), sorted([("Teich", 0), (None, 1)], key=nkey))


class TestAgGPS(RoundtripBase):

    def test_roundtrip(self):
        out = self.export("Trimble", "CFX 750")
        self.assertIn("AgGPS-Export erstellt", self.last_msg())
        for name in ("boundary.shp", "swaths.shp", "AreaFeature.shp", "PointFeature.shp"):
            self.assertIsNotNone(find_file(out, name), name)
        self.reimport(os.path.join(out, "AgGPS"))
        self.assertEqual(set(groups()), {A, B})
        self.assertEqual(catalog(*A), {1: "Rechteck mit Loch", 2: "Zwei Teile", 3: LONG_NAME})
        self.assert_boundary_areas()
        self.assert_track_geometry(0.02)
        self.assertEqual(sorted(f["Name"] for f in features(*A, "Fahrspuren", 1)),
                         ["AB Ost", "Kurve West", "Nord Gerade", "Nord Kurve"])
        # AgGPS kennt kein "befahrbar"; Flächenhindernisse behalten ihren Namen
        self.assertEqual(self.obstacles(A, "Flaechenhindernis", 1), sorted([("Teich", 0), (None, 0)], key=nkey))
        self.assertEqual(len(features(*A, "Punkthindernis", 1)), 2)


class TestCN1(RoundtripBase):

    def _export(self):
        out = self.export("CN1", "CNH")
        self.assertIn("CN1-Export erstellt", self.last_msg())
        self.assertIn("1 Loch/Löcher", self.last_msg())
        root = cn1.find_cn1_root(out)
        self.assertIsNotNone(root)
        return out, root

    def test_records_and_heading(self):
        out, root = self._export()
        n = 0
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                path = os.path.join(dirpath, fn)
                for r in cn1.read_records(path):
                    n += 1
                    self.assertTrue(r["checksum_ok"], f"Prüfsumme {fn}")
                    if fn.lower().endswith(".ags"):
                        name, kind, heading, pts = cn1.parse_guidance_payload(r["payload"])
                        self.assertLessEqual(len(name), 18)
                        if kind == cn1.LINE_AB:
                            (la1, lo1), (la2, lo2) = pts[0], pts[1]
                            p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
                            brg = math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                                          math.cos(p1) * math.sin(p2) -
                                                          math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360
                            self.assertAlmostEqual(heading % 360, brg, delta=1e-4, msg=name)
        self.assertGreater(n, 10)

    def test_roundtrip(self):
        out, root = self._export()
        self.reimport(out)
        self.assertEqual(set(groups()), {A, B})
        short = {n: cn1.cn1_name(n) for n in ("Rechteck mit Loch", "Zwei Teile", LONG_NAME)}
        self.assertEqual(set(catalog(*A).values()), set(short.values()))
        self.assertEqual(short[LONG_NAME], "Gruenland Sued Oes")
        # mehrere Feldgrenzen werden vereinigt, Löcher werden zu Hindernissen
        fid2 = self.field_id(A, short["Zwei Teile"])
        self.assertEqual(len(features(*A, "Feldgrenzen", fid2)), 1)
        self.assertAlmostEqual(metric(features(*A, "Feldgrenzen", fid2)[0].geometry()).area(), 80000, delta=160)
        fid1 = self.field_id(A, short["Rechteck mit Loch"])
        self.assertAlmostEqual(metric(features(*A, "Feldgrenzen", fid1)[0].geometry()).area(), 60000, delta=120)
        self.assertEqual(len(features(*A, "Flaechenhindernis", fid1)), 3, "2 Hindernisse + 1 Loch")
        self.assert_track_geometry(0.02)
        self.assertEqual(len(features(*A, "Punkthindernis", fid1)), 2)

    def test_import_variants(self):
        out, root = self._export()
        # über index.vy1
        self.reimport(os.path.join(root, "index.vy1"))
        self.assertEqual(set(groups()), {A, B})
        # als ZIP
        zip_base = os.path.join(self.tmp, "cn1zip")
        shutil.make_archive(zip_base, "zip", out)
        self.reimport(zip_base + ".zip")
        self.assertEqual(set(groups()), {A, B})

    def test_overwrite_existing_cn1(self):
        out, root = self._export()
        self.export("CN1", "CNH", out=out, answer=QMessageBox.StandardButton.Yes)
        self.assertEqual(len([d for d in os.listdir(out) if d.lower().endswith(".cn1")]), 1)


class TestNoData(PluginTestCase):

    def test_import_without_path(self):
        self.import_path("")
        self.assertIn("Keine Datei", self.last_msg())

    def test_export_without_selection(self):
        self.dlg.refresh_tree()
        self.dlg.mode_export.setChecked(True)
        self.dlg.out_line.setText(self.tmp)
        self.plugin._on_run()
        self.assertIn("Keine Auswahl", self.last_msg())


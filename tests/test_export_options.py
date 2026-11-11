# -*- coding: utf-8 -*-
"""
Kontursegmente und erweiterte Exportoptionen (Kurven verdichten / verlängern)
für alle Formate.
"""

import math
import os
import xml.etree.ElementTree as ET  # nosec B405 - nur selbst erzeugte Testdateien

from support import PluginTestCase, clear_project, features, find_file, line_pts, metric, mod, val

A = ("Testkunde A", "Testbetrieb A")

SEGMENT_TERMINALS = [("Fendt", "One"), ("Fendt", "Vario Terminal INT 01"), ("Valtra", "Smart Touch"),
                     ("Massey Ferguson", "Datatronic 5"), ("ISOXML", "v4")]

# Format -> (Terminal, Pfad für den Re-Import aus dem Exportordner)
FORMATS = {
    "ISOXML v4": (("ISOXML", "v4"), lambda out: find_file(out, "TASKDATA.XML")),
    "ISOXML v3": (("ISOXML", "v3"), lambda out: find_file(out, "TASKDATA.XML")),
    "Gen4": (("John Deere", "GS4 / GS5"), lambda out: os.path.dirname(find_file(out, "MasterData.xml"))),
    "AgGPS": (("Trimble", "CFX / FM / FMX / GFX / TMX"), lambda out: os.path.join(out, "AgGPS")),
    "CN1": (("CN1", "CNH"), lambda out: out),
}


def _spacing(geom):
    pts = line_pts(metric(geom))
    return [math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(pts, pts[1:])]


class TestContourSegments(PluginTestCase):

    def setUp(self):
        super().setUp()
        self.import_test_data()

    def test_supported_terminals(self):
        for brand, model in SEGMENT_TERMINALS:
            self.assertTrue(mod._supports_contour_segments(brand, model), (brand, model))
        for brand, model, _fmt in mod.TERMINALS:
            if (brand, model) not in SEGMENT_TERMINALS:
                self.assertFalse(mod._supports_contour_segments(brand, model), (brand, model))

    def test_checkbox_only_for_supported_terminals(self):
        for brand, model, _fmt in mod.TERMINALS:
            self.dlg.chk_seg.setChecked(True)
            self.select_terminal(brand, model)
            supported = (brand, model) in SEGMENT_TERMINALS
            self.assertEqual(not self.dlg.chk_seg.isHidden(), supported, (brand, model))
            if not supported:
                self.assertFalse(self.dlg.chk_seg.isChecked(), (brand, model))

    def _segment_export(self, brand, model):
        out = self.export(brand, model, seg=True)
        root = ET.parse(find_file(out, "TASKDATA.XML")).getroot()
        ggps = {g.get("B"): g for g in root.iter("GGP")}
        self.assertIn("Kontur Nord", ggps)
        gpns = ggps["Kontur Nord"].findall("GPN")
        self.assertEqual([(n.get("B"), n.get("C")) for n in gpns], [("Nord Gerade", "1"), ("Nord Kurve", "3")])
        for n in gpns:
            self.assertEqual((n.get("E"), n.get("F")), ("1", "1"))
        # Spuren ohne Segment: je eine eigene GGP
        self.assertEqual(len(ggps["AB Ost"].findall("GPN")), 1)
        self.assertEqual(len(ggps["Kurve West"].findall("GPN")), 1)
        return out

    def test_export_with_segments_all_terminals(self):
        for brand, model in SEGMENT_TERMINALS:
            with self.subTest(terminal=f"{brand} {model}"):
                self._segment_export(brand, model)

    def test_segments_survive_roundtrip(self):
        out = self._segment_export("Fendt", "One")
        clear_project()
        self.import_path(find_file(out, "TASKDATA.XML"), "re")
        got = {f["Name"]: val(f["Segment"]) for f in features(*A, "Fahrspuren", 1)}
        self.assertEqual(got, {"AB Ost": None, "Kurve West": None,
                               "Nord Gerade": "Kontur Nord", "Nord Kurve": "Kontur Nord"})

    def test_segment_tracks_left_out_without_checkbox(self):
        out = self.export("ISOXML", "v4", seg=False)
        root = ET.parse(find_file(out, "TASKDATA.XML")).getroot()
        names = {n.get("B") for n in root.iter("GPN")}
        self.assertNotIn("Nord Gerade", names)
        self.assertIn("AB Ost", names)

    def test_unsupported_terminal_ignores_checkbox(self):
        # Claas Cemis 1200 schreibt ISOXML v4, kennt aber keine Kontursegmente
        out = self.export("Claas", "Cemis 1200", seg=True)
        root = ET.parse(find_file(out, "TASKDATA.XML")).getroot()
        self.assertNotIn("Kontur Nord", {g.get("B") for g in root.iter("GGP")})
        self.assertTrue(all(len(g.findall("GPN")) == 1 for g in root.iter("GGP")))

    def test_created_tracks_as_segment_are_exported_together(self):
        # Spurenerstellung mit "Als Kontursegment" -> ein gemeinsames Segment im Export
        self.dlg.refresh_tree()
        self.dlg.mode_tracks.setChecked(True)
        self.dlg.txt_track_prefix.setText("Grubber")
        self.dlg.chk_track_segment.setChecked(True)
        self.plugin._on_run()
        out = self.export("ISOXML", "v4", seg=True)
        root = ET.parse(find_file(out, "TASKDATA.XML")).getroot()
        grubber = [g for g in root.iter("GGP") if g.get("B") == "Grubber"]
        self.assertTrue(grubber)
        self.assertTrue(all(n.get("B").startswith("Grubber ") for g in grubber for n in g.findall("GPN")))


class TestDensifyAndExtend(PluginTestCase):
    """Verdichten und Verlängern gelten nur für Kurven, nicht für Geraden."""

    INTERVAL = 5.0
    EXTEND = 15.0

    def setUp(self):
        super().setUp()
        self.import_test_data()
        self.ref = {f["Name"]: f.geometry() for f in features(*A, "Fahrspuren", 1)}

    def _roundtrip(self, fmt, **opts):
        (brand, model), src = FORMATS[fmt]
        out = self.export(brand, model, **opts)
        clear_project()
        self.import_path(src(out), "re_" + fmt.replace(" ", ""))
        return {f["Name"]: f.geometry() for f in features(*A, "Fahrspuren")}

    def test_densify(self):
        for fmt in FORMATS:
            with self.subTest(format=fmt):
                got = self._roundtrip(fmt, densify=self.INTERVAL)
                curve = got["Kurve West"]
                self.assertLessEqual(max(_spacing(curve)), self.INTERVAL + 0.05, fmt)
                self.assertGreater(len(line_pts(curve)), 20, fmt)
                # Form bleibt gleich
                self.assertLess(metric(curve).hausdorffDistance(metric(self.ref["Kurve West"])), 0.05, fmt)
                # Gerade bleibt eine Gerade mit 2 Punkten
                self.assertEqual(len(line_pts(got["AB Ost"])), 2, fmt)
                self.setUp_again()

    def test_extend(self):
        for fmt in FORMATS:
            with self.subTest(format=fmt):
                got = self._roundtrip(fmt, extend=self.EXTEND)
                ref_curve, curve = metric(self.ref["Kurve West"]), metric(got["Kurve West"])
                self.assertAlmostEqual(curve.length(), ref_curve.length() + 2 * self.EXTEND, delta=0.1, msg=fmt)
                # Anfang liegt 15 m vor dem alten Anfang, in Richtung der ersten Kante
                r = line_pts(ref_curve)
                c = line_pts(curve)
                ux, uy = r[1].x() - r[0].x(), r[1].y() - r[0].y()
                n = math.hypot(ux, uy)
                exp_x, exp_y = r[0].x() - ux / n * self.EXTEND, r[0].y() - uy / n * self.EXTEND
                self.assertLess(math.hypot(c[0].x() - exp_x, c[0].y() - exp_y), 0.1, fmt)
                # Gerade unverändert
                self.assertAlmostEqual(metric(got["AB Ost"]).length(), metric(self.ref["AB Ost"]).length(),
                                       delta=0.05, msg=fmt)
                self.setUp_again()

    def test_densify_and_extend_together(self):
        got = self._roundtrip("ISOXML v4", densify=self.INTERVAL, extend=self.EXTEND)
        curve = metric(got["Kurve West"])
        self.assertAlmostEqual(curve.length(), metric(self.ref["Kurve West"]).length() + 2 * self.EXTEND, delta=0.1)
        inner = _spacing(got["Kurve West"])[1:-1]
        self.assertLessEqual(max(inner), self.INTERVAL + 0.05)

    def test_options_off_change_nothing(self):
        got = self._roundtrip("ISOXML v4")
        self.assertEqual(len(line_pts(got["Kurve West"])), len(line_pts(self.ref["Kurve West"])))

    def setUp_again(self):
        """Ausgangsdaten für den nächsten Format-Durchlauf wiederherstellen."""
        clear_project()
        self.import_test_data()
        self.ref = {f["Name"]: f.geometry() for f in features(*A, "Fahrspuren", 1)}


# -*- coding: utf-8 -*-
"""Spurenerstellung: Geometrie-Regeln und der Reiter im Dialog."""

import math

from support import (PluginTestCase, QgsGeometry, QgsPointXY, Qt, features, layer, line_pts, metric, tc,
                     val)

A = ("Testkunde A", "Testbetrieb A")
B = ("Testkunde B", "Testbetrieb B")


def P(*xy):
    return [QgsPointXY(x, y) for x, y in xy]


def seg_lengths(ring):
    """Punktanzahl je Abschnitt (ohne Mindestlängen-Filter)."""
    return [len(s) for s in tc.ring_to_segments(ring)]


class TestSegmentRules(PluginTestCase):
    """Teilen der Feldgrenze in Geraden und Kurven."""

    def test_rectangle(self):
        self.assertEqual(seg_lengths(P((0, 0), (100, 0), (100, 50), (0, 50), (0, 0))), [2, 2, 2, 2])

    def test_rounded_corners(self):
        for r, expected in ((8, [2, 2, 10, 2, 2]), (20, [2, 12, 2]), (60, [2, 2, 11, 2])):
            arc = [(100 + r * math.sin(math.radians(a)), 50 - r + r * math.cos(math.radians(a)))
                   for a in range(0, 91, 10)]
            ring = P((0, 0), (100 + r, 0), *reversed(arc), (0, 50), (0, 0))
            with self.subTest(radius=r):
                self.assertEqual(seg_lengths(ring), expected)

    def test_chamfered_corner(self):
        ring = P((0, 0), (100, 0), (105, 3), (108, 8), (108, 50), (0, 50), (0, 0))
        self.assertEqual(seg_lengths(ring), [2, 3, 2, 2, 2])

    def test_circles(self):
        circle = P(*[(50 * math.cos(math.radians(a)), 50 * math.sin(math.radians(a))) for a in range(0, 360, 10)],
                   (50, 0))
        self.assertEqual(seg_lengths(circle), [37])
        small = P(*[(3 * math.cos(math.radians(a)), 3 * math.sin(math.radians(a))) for a in range(0, 360, 30)],
                  (3, 0))
        self.assertEqual(seg_lengths(small), [13])

    def test_almost_straight_vertex_is_kept(self):
        # Zwischenpunkt 7 cm neben der Linie bleibt erhalten -> Kurve exakt auf der Grenze
        ring = P((0, 0), (300, 0.07), (600, 0), (600, 50), (0, 50), (0, 0))
        segs = tc.ring_to_segments(ring)
        self.assertIn(3, [len(s) for s in segs])
        # exakt kollinearer Punkt (< 1 mm) wird entfernt -> Gerade
        ring = P((0, 0), (300, 0.0001), (600, 0), (600, 50), (0, 50), (0, 0))
        self.assertEqual(seg_lengths(ring), [2, 2, 2, 2])

    def test_stub_at_sharp_tip_is_split_off(self):
        # Spitze mit kurzem Schrägstück (5,7 m) und deutlichem Knick (> 10°) danach
        ring = P((0, 0), (600, 0), (600.1, 0.05), (595, 3), (0, 3), (0, 0))
        tracks = tc.tracks_for_boundary(QgsGeometry.fromPolygonXY([ring]))
        lengths = sorted(round(QgsGeometry.fromPolylineXY(t).length()) for t in tracks)
        self.assertEqual(lengths, [595, 600])

    def test_flat_curve_runs_to_corner(self):
        # flache Kurve, deren letztes, kurzes Stück (8 m) direkt an einer Ecke endet
        curve = [(300 - x, 300 + 0.0004 * x * x) for x in [*range(0, 300, 25), 292, 300]]
        ring = P((0, 0), (300, 0), *curve, (0, 0))
        tracks = tc.tracks_for_boundary(QgsGeometry.fromPolygonXY([ring]))
        curves = [t for t in tracks if len(t) > 2]
        self.assertEqual(len(curves), 1)
        self.assertEqual((round(curves[0][0].x()), round(curves[0][-1].x())), (300, 0))

    def test_shared_border_line(self):
        a = QgsGeometry.fromPolygonXY([P((0, 0), (100, 0), (100, 50), (0, 50), (0, 0))])
        b = QgsGeometry.fromPolygonXY([P((100, 0), (200, 0), (200, 50), (100, 50), (100, 0))])
        self.assertEqual(len(tc.tracks_for_boundaries([a, b], 0)), 5)
        shifted = tc.tracks_for_boundaries([a, b], 6)
        xs = sorted(round(t[0].x()) for t in shifted if round(t[0].x()) == round(t[-1].x()))
        self.assertIn(94, xs)
        self.assertIn(106, xs)

    def test_offset_keeps_length(self):
        ring = P((0, 0), (100, 0), (100, 50), (0, 50), (0, 0))
        poly = QgsGeometry.fromPolygonXY([ring])
        for t in tc.tracks_for_boundary(poly, 6):
            g = QgsGeometry.fromPolylineXY(t)
            self.assertIn(round(g.length()), (100, 50))
            mid = g.interpolate(g.length() / 2)
            self.assertAlmostEqual(QgsGeometry.fromPolylineXY(ring).distance(mid), 6, places=3)

    def test_offset_too_large(self):
        poly = QgsGeometry.fromPolygonXY([P((0, 0), (100, 0), (100, 50), (0, 50), (0, 0))])
        self.assertEqual(tc.tracks_for_boundary(poly, 1000), [])


class TestTrackTab(PluginTestCase):
    """Reiter Spurenerstellung über den Dialog."""

    def setUp(self):
        super().setUp()
        self.import_test_data()
        for key in (A, B):
            lyr = layer(*key, "Fahrspuren")
            lyr.dataProvider().deleteFeatures([f.id() for f in lyr.getFeatures()])
        self.dlg.refresh_tree()
        self.dlg.mode_tracks.setChecked(True)

    def run_tracks(self, offset=0.0, prefix="", segment=False):
        self.dlg.spin_track_offset.setValue(offset)
        self.dlg.txt_track_prefix.setText(prefix)
        self.dlg.chk_track_segment.setChecked(segment)
        self.msgs.clear()
        self.plugin._on_run()
        return self.last_msg()

    def boundary_line(self, key, fid):
        lines = []
        for f in features(*key, "Feldgrenzen", fid):
            g = metric(f.geometry())
            for poly in (g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]):
                lines.append(QgsGeometry.fromPolylineXY(poly[0]))
        return QgsGeometry.unaryUnion(lines)

    def test_tracks_on_boundary(self):
        msg = self.run_tracks(0)
        self.assertIn("Fahrspuren für 5 Feld(er) erstellt", msg)
        for key, fid in ((A, 1), (A, 3), (B, 1), (B, 2)):
            border = self.boundary_line(key, fid)
            for f in features(*key, "Fahrspuren", fid):
                g = metric(f.geometry())
                for t in (0.1, 0.5, 0.9):
                    self.assertLess(border.distance(g.interpolate(g.length() * t)), 0.001, (key, fid, f["Name"]))

    def test_names_and_counts(self):
        self.run_tracks(0)
        names = sorted(f["Name"] for f in features(*A, "Fahrspuren", 1))
        self.assertEqual(names, ["Gerade 1", "Gerade 2", "Gerade 3", "Gerade 4"], "nur Außenring, kein Loch")
        self.assertEqual(sorted(f["Name"] for f in features(*B, "Fahrspuren", 1)), ["Gerade 1", "Gerade 2"],
                         "Streifen: zwei lange Seiten, Stummel entfallen")
        curve = [f for f in features(*B, "Fahrspuren", 2) if f["Name"].startswith("Kurve")]
        self.assertEqual(len(curve), 1)
        self.assertEqual(len(line_pts(curve[0].geometry())), 14, "flache Kurve mit allen Punkten bis zur Ecke")

    def test_shared_border_of_two_boundaries(self):
        self.run_tracks(0)
        self.assertEqual(len(features(*A, "Fahrspuren", 2)), 5)
        self.run_tracks(6)
        self.assertEqual(len(features(*A, "Fahrspuren", 2)), 5 + 6, "angehängt: 5 + (4 außen + 2 an der Grenze)")

    def test_offset_parallel_and_length(self):
        self.run_tracks(0)
        base = {f["Name"]: metric(f.geometry()).length() for f in features(*A, "Fahrspuren", 3)}
        lyr = layer(*A, "Fahrspuren")
        lyr.dataProvider().deleteFeatures([f.id() for f in lyr.getFeatures()])
        msg = self.run_tracks(6)
        self.assertIn("Abstand 6 m", msg)
        border = self.boundary_line(A, 3)
        for f in features(*A, "Fahrspuren", 3):
            g = metric(f.geometry())
            self.assertAlmostEqual(g.length(), base[f["Name"]], delta=0.01)
            self.assertAlmostEqual(border.distance(g.interpolate(g.length() / 2)), 6, delta=0.01)

    def test_offset_too_large_message(self):
        msg = self.run_tracks(1000)
        self.assertIn("Abstand zu groß", msg)

    def test_prefix_and_segment_names(self):
        self.run_tracks(0, prefix="  Grubber  ", segment=False)
        self.run_tracks(0, prefix="Grubber", segment=True)
        self.run_tracks(0, prefix="Grubber", segment=True)
        self.run_tracks(0, prefix="", segment=True)
        feats = features(*A, "Fahrspuren", 3)
        self.assertEqual(sorted({val(f["Segment"]) or "" for f in feats}),
                         ["", "Grubber", "Grubber 2", "Kontursegment"])
        self.assertTrue(all(f["Name"].startswith("Grubber ") for f in feats if val(f["Segment"]) != "Kontursegment"))
        self.assertTrue(all(len(f["Name"]) <= 32 for f in feats))

    def test_prefix_field_limit(self):
        self.assertEqual(self.dlg.txt_track_prefix.maxLength(), 20)

    def test_no_selection(self):
        for i in range(self.dlg.tree_tracks.topLevelItemCount()):
            self.dlg.tree_tracks.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)
        self.assertIn("Keine Auswahl", self.run_tracks(0))

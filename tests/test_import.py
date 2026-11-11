# -*- coding: utf-8 -*-
"""ISOXML-Import der künstlichen Testdaten."""

from support import (LONG_NAME, PluginTestCase, catalog, features, groups, line_pts, metric, mod, nkey,
                     val)


class TestIsoxmlImport(PluginTestCase):

    def setUp(self):
        super().setUp()
        self.import_test_data()

    def test_hierarchy_and_catalog(self):
        self.assertEqual(set(groups()), {("Testkunde A", "Testbetrieb A"), ("Testkunde B", "Testbetrieb B")})
        self.assertEqual(catalog("Testkunde A", "Testbetrieb A"),
                         {1: "Rechteck mit Loch", 2: "Zwei Teile", 3: LONG_NAME})
        self.assertEqual(catalog("Testkunde B", "Testbetrieb B"), {1: "Streifen", 2: "Flache Kurve"})

    def test_layers_exist_and_saved_as_gpkg(self):
        grp = groups()[("Testkunde A", "Testbetrieb A")]
        for n in ("Feldgrenzen", "Fahrspuren", "Punkthindernis", "Flaechenhindernis"):
            lyr = mod._find_child_layer(grp, n)
            self.assertIsNotNone(lyr, n)
            self.assertIn(".gpkg", lyr.source().lower(), n)

    def test_boundary_with_hole(self):
        feats = features("Testkunde A", "Testbetrieb A", "Feldgrenzen", 1)
        self.assertEqual(len(feats), 1)
        g = feats[0].geometry()
        polys = g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]
        self.assertEqual(len(polys), 1)
        self.assertEqual(len(polys[0]), 2, "Außenring + 1 Loch")
        self.assertAlmostEqual(metric(g).area(), 300 * 200 - 40 * 40, delta=60)

    def test_two_boundaries_one_field(self):
        self.assertEqual(len(features("Testkunde A", "Testbetrieb A", "Feldgrenzen", 2)), 2)

    def test_area_attribute(self):
        f = features("Testkunde A", "Testbetrieb A", "Feldgrenzen", 3)[0]
        self.assertAlmostEqual(float(f["Flaeche"]), 250 * 150, delta=40)

    def test_tracks_and_segments(self):
        feats = features("Testkunde A", "Testbetrieb A", "Fahrspuren", 1)
        got = sorted(((f["Name"], val(f["Segment"]), len(line_pts(f.geometry()))) for f in feats), key=nkey)
        self.assertEqual(got, sorted([
            ("AB Ost", None, 2), ("Kurve West", None, 4),
            ("Nord Gerade", "Kontur Nord", 2), ("Nord Kurve", "Kontur Nord", 4)], key=nkey))

    def test_point_obstacles(self):
        feats = features("Testkunde A", "Testbetrieb A", "Punkthindernis", 1)
        self.assertEqual(sorted((f["Name"], int(f["befahrbar"])) for f in feats), [("Fahne", 1), ("Stein", 0)])

    def test_area_obstacles_with_name(self):
        feats = features("Testkunde A", "Testbetrieb A", "Flaechenhindernis", 1)
        got = sorted(((val(f["Name"]), int(f["befahrbar"])) for f in feats), key=nkey)
        self.assertEqual(got, sorted([("Teich", 0), (None, 1)], key=nkey))

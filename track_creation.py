# -*- coding: utf-8 -*-
"""
Spurenerstellung: erzeugt Fahrspuren entlang der Feldgrenze.

Vorgehen (angelehnt an das Processing-Skript "Kostenaufstellung
Fahrspurplanung"):
    1. Feldgrenze in ein metrisches KBS umrechnen.
    2. Den Außenring an jedem Knick teilen, dessen Innenwinkel <= Grenzwinkel
       (Standard 140°) ist. Abgeschrägte Ecken (mehrere flache Knicke dicht
       hintereinander, die zusammen eine Ecke ergeben) zählen ebenfalls als
       Ecke. Löcher werden nicht verwendet.
    3. Abschnitte mit 2 Stützpunkten werden Geraden (AB-Linien), ab 3
       Stützpunkten Kurven (alle Stützpunkte bleiben erhalten). Punkte, die
       praktisch auf einer Geraden liegen, zählen dabei nicht mit.
    4. Abschnitte unter 15 m (z. B. Eckstücke) werden weggelassen.
    5. Bei Abstand > 0 jede Spur parallel um diesen Abstand nach innen
       verschieben (Länge bleibt wie an der Feldgrenze).
Mehrere Feldgrenzen eines Felds werden für die Außengrenze vereinigt. Wo zwei
Feldgrenzen aneinander liegen, entsteht zusätzlich eine Spur entlang der
gemeinsamen Grenze (bei Abstand > 0 je eine Spur in jeden Feldteil versetzt).
"""

import math

from qgis.core import Qgis, QgsGeometry, QgsPointXY, QgsWkbTypes

DEFAULT_ANGLE_DEG = 140.0
COLLINEAR_DEG = 179.5
# Abgeschrägte Ecken: Knicke, die über Kanten < 15 m und höchstens 30 m
# Gesamtlänge zusammen eine Ecke bilden, werden wie eine Ecke behandelt.
CHAMFER_EDGE_M = 15.0
CHAMFER_TOTAL_M = 30.0
# Kürzere Abschnitte werden nicht als Spur erstellt
MIN_TRACK_M = 15.0


def _angle_at(p_prev, p_curr, p_next):
    """Innenwinkel am Punkt p_curr in Grad (None bei doppelten Punkten)."""
    v1 = (p_prev.x() - p_curr.x(), p_prev.y() - p_curr.y())
    v2 = (p_next.x() - p_curr.x(), p_next.y() - p_curr.y())
    len1 = math.hypot(*v1)
    len2 = math.hypot(*v2)
    if len1 == 0 or len2 == 0:
        return None
    cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (len1 * len2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosang))))


def _turn_at(p_prev, p_curr, p_next):
    """Richtungsänderung am Punkt p_curr in Grad mit Vorzeichen (links +, rechts -)."""
    h1 = math.atan2(p_curr.y() - p_prev.y(), p_curr.x() - p_prev.x())
    h2 = math.atan2(p_next.y() - p_curr.y(), p_next.x() - p_curr.x())
    return (math.degrees(h2 - h1) + 180.0) % 360.0 - 180.0


def _chamfer_corners(pts, splits, threshold):
    """
    Abgeschrägte Ecken: mehrere Knicke kurz hintereinander, von denen jeder für
    sich zu flach ist (Innenwinkel > threshold), die zusammen aber eine Ecke
    <= threshold ergeben (z. B. 2 x 142° im Abstand von 8 m = Ecke von ca. 76°).
    Liefert die Indizes, an denen zusätzlich geteilt wird: Anfang und Ende
    jeder solchen Ecke.
    """
    n = len(pts)
    need = 180.0 - threshold                 # nötige Richtungsänderung für eine Ecke
    turns = [_turn_at(pts[(i - 1) % n], pts[i], pts[(i + 1) % n]) for i in range(n)]
    edge = [math.hypot(pts[(i + 1) % n].x() - pts[i].x(), pts[(i + 1) % n].y() - pts[i].y())
            for i in range(n)]

    def _link(k):
        """Kante k (Punkt k -> k+1) gehört zu einer möglichen Ecke."""
        a, b = k, (k + 1) % n
        return (edge[k] < CHAMFER_EDGE_M and a not in splits and b not in splits
                and turns[a] != 0 and turns[a] * turns[b] > 0)

    links = [_link(k) for k in range(n)]
    if all(links) or not any(links):
        return set()

    # Jede zusammenhängende Folge kurzer Kanten als Ganzes bewerten (nicht
    # stückweise) - sonst zerfiele eine fein digitalisierte Rundung in Stücke.
    extra = set()
    start = next(k for k in range(n) if links[k] and not links[(k - 1) % n])
    k = start
    for _ in range(n):
        if links[k] and not links[(k - 1) % n]:
            a = k
            length = 0.0
            j = k
            while links[j]:
                length += edge[j]
                j = (j + 1) % n
                if j == a:
                    break
            b = j                                  # letzter Punkt der Folge
            idx = [(a + t) % n for t in range(((b - a) % n) + 1)]
            total = sum(turns[t] for t in idx)
            if abs(total) >= need and length <= CHAMFER_TOTAL_M:
                extra.add(a)
                extra.add(b)
        k = (k + 1) % n
    return extra


def ring_to_segments(ring, threshold=DEFAULT_ANGLE_DEG):
    """
    Teilt einen geschlossenen Ring an allen Knicken mit Innenwinkel <= threshold.
    Rückgabe: Liste von Punktlisten (QgsPointXY).
    """
    pts = [QgsPointXY(p) for p in ring]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    # direkt aufeinanderfolgende doppelte Punkte entfernen
    clean = []
    for p in pts:
        if not clean or p != clean[-1]:
            clean.append(p)
    if len(clean) >= 2 and clean[0] == clean[-1]:
        clean.pop()
    # Punkte, die praktisch auf einer Geraden liegen (Innenwinkel >= 179,5°),
    # sind kein Knick und keine Krümmung - sonst würde aus einer geraden
    # Kante mit Zwischenpunkt eine "Kurve" (ab 3 Stützpunkten).
    changed = True
    while changed and len(clean) > 3:
        changed = False
        for i in range(len(clean)):
            m = len(clean)
            ang = _angle_at(clean[(i - 1) % m], clean[i], clean[(i + 1) % m])
            if ang is not None and ang >= COLLINEAR_DEG:
                del clean[i]
                changed = True
                break
    n = len(clean)
    if n < 3:
        return []

    split_set = set()
    for i in range(n):
        ang = _angle_at(clean[(i - 1) % n], clean[i], clean[(i + 1) % n])
        if ang is not None and ang <= threshold:
            split_set.add(i)
    split_set |= _chamfer_corners(clean, split_set, threshold)
    splits = sorted(split_set)

    # kein Knick: ganzer Ring als eine (geschlossene) Kurve
    if not splits:
        return [clean + [clean[0]]]

    # ein Knick: geschlossene Kurve ab diesem Punkt
    if len(splits) == 1:
        s = splits[0]
        return [[clean[(s + k) % n] for k in range(n + 1)]]

    segments = []
    for k, start in enumerate(splits):
        end = splits[(k + 1) % len(splits)]
        seg = [clean[start]]
        i = start
        while i != end:
            i = (i + 1) % n
            seg.append(clean[i])
        if len(seg) >= 2:
            segments.append(seg)
    return segments


def _styles():
    try:
        return Qgis.EndCapStyle.Flat, Qgis.JoinStyle.Miter
    except AttributeError:          # QGIS < 3.30
        return QgsGeometry.CapFlat, QgsGeometry.JoinStyleMiter


def _inward_offset(geom, distance_m):
    """Versetzt ein Polygon (metrisches KBS) um distance_m nach innen; spitze Ecken bleiben spitz."""
    cap, join = _styles()
    return geom.buffer(-float(distance_m), 8, cap, join, 5.0)


def _signed_area(pts):
    """Fläche mit Vorzeichen (> 0: Ring gegen den Uhrzeigersinn, Inneres links)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        a += p.x() * q.y() - q.x() * p.y()
    return a / 2.0


def _longest_line(geom):
    """Längster Linienzug einer (Multi-)Linie als Punktliste."""
    if geom is None or geom.isEmpty():
        return []
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
        return max(parts, key=lambda pl: QgsGeometry.fromPolylineXY(pl).length()) if parts else []
    return geom.asPolyline()


def _shift_segment(seg, distance_m, left):
    """
    Verschiebt eine offene Spur parallel um distance_m zur Seite (left=True:
    links in Fahrtrichtung). Die Enden werden rechtwinklig mitverschoben, die
    Spur bleibt also so lang wie an der Feldgrenze.
    """
    d = float(distance_m) if left else -float(distance_m)
    if len(seg) == 2:
        p0, p1 = seg
        length = math.hypot(p1.x() - p0.x(), p1.y() - p0.y())
        if length == 0:
            return []
        nx, ny = -(p1.y() - p0.y()) / length * d, (p1.x() - p0.x()) / length * d
        return [QgsPointXY(p0.x() + nx, p0.y() + ny), QgsPointXY(p1.x() + nx, p1.y() + ny)]
    _cap, join = _styles()
    out = _longest_line(QgsGeometry.fromPolylineXY(seg).offsetCurve(d, 8, join, 5.0))
    if len(out) < 2:
        return []
    # ältere GEOS-Versionen liefern die rechte Seite verkehrt herum
    def _dist(a, b):
        return math.hypot(a.x() - b.x(), a.y() - b.y())
    if _dist(out[0], seg[0]) + _dist(out[-1], seg[-1]) > _dist(out[-1], seg[0]) + _dist(out[0], seg[-1]):
        out = list(reversed(out))
    return [QgsPointXY(p) for p in out]


def _shift_closed(seg, distance_m):
    """Geschlossene Kurve (Ring ohne Ecke) nach innen versetzen; Start beim nächstgelegenen Punkt."""
    g = _inward_offset(QgsGeometry.fromPolygonXY([seg]), distance_m)
    if g is None or g.isEmpty():
        return []
    polys = g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]
    polys = [p for p in polys if p and len(p[0]) >= 4]
    if not polys:
        return []
    ring = max(polys, key=lambda p: abs(_signed_area(p[0][:-1])))[0][:-1]
    start = min(range(len(ring)), key=lambda i: math.hypot(ring[i].x() - seg[0].x(), ring[i].y() - seg[0].y()))
    ring = ring[start:] + ring[:start]
    # gleiche Umlaufrichtung wie die Feldgrenze
    if (_signed_area(ring) > 0) != (_signed_area(seg[:-1]) > 0):
        ring = [ring[0]] + list(reversed(ring[1:]))
    return [QgsPointXY(p) for p in ring] + [QgsPointXY(ring[0])]


def line_to_segments(line, threshold=DEFAULT_ANGLE_DEG):
    """
    Wie ring_to_segments, aber für eine offene Linie (z. B. die gemeinsame
    Grenze zweier Feldteile): geteilt wird an inneren Knicken <= threshold.
    """
    clean = []
    for p in (QgsPointXY(q) for q in line):
        if not clean or p != clean[-1]:
            clean.append(p)
    changed = True
    while changed and len(clean) > 2:
        changed = False
        for i in range(1, len(clean) - 1):
            ang = _angle_at(clean[i - 1], clean[i], clean[i + 1])
            if ang is not None and ang >= COLLINEAR_DEG:
                del clean[i]
                changed = True
                break
    if len(clean) < 2:
        return []
    segments = []
    seg = [clean[0]]
    for i in range(1, len(clean)):
        seg.append(clean[i])
        if i < len(clean) - 1:
            ang = _angle_at(clean[i - 1], clean[i], clean[i + 1])
            if ang is not None and ang <= threshold:
                segments.append(seg)
                seg = [clean[i]]
    segments.append(seg)
    return [s for s in segments if len(s) >= 2]


# Toleranz, ab der zwei Feldgrenzen als "aneinander liegend" gelten (m)
SHARED_TOL_M = 0.1


def _single_polygons(geoms):
    out = []
    for g in geoms:
        if g is None or g.isEmpty():
            continue
        for poly in (g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]):
            if poly and len(poly[0]) >= 4:
                out.append(poly)
    return out


def _shared_borders(geoms):
    """Linien, an denen zwei Feldgrenzen eines Felds aneinander liegen."""
    polys = _single_polygons(geoms)
    cap, join = _styles()
    lines = []
    for i in range(len(polys)):
        ring_a = QgsGeometry.fromPolylineXY(polys[i][0])
        for j in range(i + 1, len(polys)):
            near_b = QgsGeometry.fromPolygonXY(polys[j]).buffer(SHARED_TOL_M, 8, cap, join, 5.0)
            if not ring_a.boundingBox().intersects(near_b.boundingBox()):
                continue
            inter = ring_a.intersection(near_b)
            if inter is None or inter.isEmpty() or QgsWkbTypes.geometryType(inter.wkbType()) != QgsWkbTypes.LineGeometry:
                continue
            merged = inter.mergeLines()
            parts = merged.asMultiPolyline() if merged.isMultipart() else [merged.asPolyline()]
            lines.extend(pl for pl in parts if len(pl) >= 2)
    return lines


def tracks_for_boundaries(geoms_metric, offset_m=0.0, threshold=DEFAULT_ANGLE_DEG):
    """
    Spuren für ein Feld mit einer oder mehreren Feldgrenzen (metrisches KBS):
    Spuren entlang der gemeinsamen Außengrenze und zusätzlich entlang der
    Grenzen, an denen zwei Feldgrenzen aneinander liegen. Diese werden bei
    Abstand > 0 auf beide Seiten versetzt (je eine Spur pro Feldteil).
    """
    geoms = [g for g in geoms_metric if g is not None and not g.isEmpty()]
    if not geoms:
        return []
    merged = QgsGeometry.unaryUnion(geoms) if len(geoms) > 1 else geoms[0]
    result = tracks_for_boundary(merged, offset_m, threshold)
    if not result or (len(geoms) < 2 and not geoms[0].isMultipart()):
        return result
    offset = float(offset_m or 0.0)
    for line in _shared_borders(geoms):
        for seg in line_to_segments(line, threshold):
            if QgsGeometry.fromPolylineXY(seg).length() < MIN_TRACK_M:
                continue
            if offset > 0:
                for left in (True, False):
                    shifted = _shift_segment(seg, offset, left)
                    if len(shifted) >= 2:
                        result.append(shifted)
            else:
                result.append(seg)
    return result


def tracks_for_boundary(geom_metric, offset_m=0.0, threshold=DEFAULT_ANGLE_DEG):
    """
    geom_metric: Polygon/MultiPolygon in einem metrischen KBS.
    Die Spuren werden an der Feldgrenze selbst gebildet und dann um offset_m
    parallel nach innen verschoben - so werden sie mit größerem Abstand nicht
    kürzer.
    Rückgabe: Liste von Punktlisten (QgsPointXY, metrisch). Leere Liste, wenn
    die Fläche durch den Abstand verschwindet.
    """
    if geom_metric is None or geom_metric.isEmpty():
        return []
    g = QgsGeometry(geom_metric)
    offset = float(offset_m or 0.0)
    if offset > 0:
        inner = _inward_offset(g, offset)
        if inner is None or inner.isEmpty():
            return []
    polys = g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]
    result = []
    for poly in polys:
        if not poly or len(poly[0]) < 4:
            continue
        # nur der Außenring - Löcher (z. B. Fahrgassen) bekommen keine Spuren
        ring = poly[0]
        inside_left = _signed_area(ring[:-1]) > 0
        for seg in ring_to_segments(ring, threshold):
            # Mini-Stücke (z. B. an abgeschrägten Ecken) sind als Spur unbrauchbar
            if QgsGeometry.fromPolylineXY(seg).length() < MIN_TRACK_M:
                continue
            if offset > 0:
                if seg[0] == seg[-1]:
                    seg = _shift_closed(seg, offset)
                else:
                    seg = _shift_segment(seg, offset, inside_left)
                if len(seg) < 2:
                    continue
            result.append(seg)
    return result

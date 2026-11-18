# -*- coding: utf-8 -*-
"""
AGCO-KML-Export (Fendt / Valtra / Massey Ferguson).

Je Feld eine ZIP "Field_<Name>.zip" mit .kml und .ini im Zielordner (Format
siehe agco_kml_format.py). Zuordnung wie beim Fendt:
    Feldgrenzen              -> genau EINE Grenze je Feld (vereinigt; bleiben
                                getrennte Teile übrig, nur die größte)
    Löcher der Feldgrenze    -> unbenannte Flächenhindernisse
    Flaechenhindernis        -> Flächenhindernis (nur Außenring, auch befahrbare)
    Punkthindernis           -> nicht befahrbar: Hindernis, befahrbar: Markierung
    Fahrspuren               -> AB-Linie (2 Punkte) bzw. Kurve; Kontursegmente
                                gibt es im KML nicht, jede Spur wird einzeln
                                geschrieben
"""

import math
import os
import uuid

from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsLayerTreeGroup, QgsDistanceArea, QgsUnitTypes, QgsPointXY,
)

try:
    from . import agco_kml_format as kml
except Exception:
    import agco_kml_format as kml

# fester Namensraum, damit ein Feld bei jedem Export dieselbe UUID bekommt
_UUID_NS = uuid.UUID("6f1c2a7e-3b8d-4c55-9a40-1e2f3a4b5c6d")


def _ccw_closed(ring):
    """[(lon, lat)] geschlossen und gegen den Uhrzeigersinn."""
    pts = list(ring)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
    if area < 0:
        pts.reverse()
    return pts + [pts[0]]


def existing_zips(out_dir):
    """Vorhandene AGCO-KML-ZIPs im Zielordner."""
    if not os.path.isdir(out_dir):
        return []
    return sorted(os.path.join(out_dir, fn) for fn in os.listdir(out_dir)
                  if fn.startswith("Field_") and fn.lower().endswith(".zip"))


def export_agco_kml(
    plugin, out_dir, selected,
    densify_enabled=False, interval_m=1.0,
    extend_enabled=False, extend_m=0.0
):
    """
    selected = {Kunde: {Betrieb: set(ids)|None}}.

    Rückgabe: dict mit
        ok            True, wenn etwas exportiert wurde
        files         geschriebene ZIP-Dateien
        multi_fields  Felder, von deren Feldgrenzen nur ein Teil exportiert wurde
        holes         Anzahl Löcher, die als Hindernis geschrieben wurden
    """
    try:
        from .lk_technik_path_planner import (
            _field_catalog_for_frm, _field_map, _pick_field, _is_nullish, _find_child_layer,
            _densify_geometry_for_export, _extend_geometry_for_export, _geometry_to_lines_xy,
            _polygon_parts,
        )
    except Exception:
        from lk_technik_path_planner import (
            _field_catalog_for_frm, _field_map, _pick_field, _is_nullish, _find_child_layer,
            _densify_geometry_for_export, _extend_geometry_for_export, _geometry_to_lines_xy,
            _polygon_parts,
        )

    project = QgsProject.instance()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

    da = QgsDistanceArea()
    da.setSourceCrs(wgs84, project.transformContext())
    da.setEllipsoid("WGS84")

    result = {"ok": False, "files": [], "multi_fields": [], "holes": 0}

    def _find_group(parent, name):
        for n in parent.children():
            if isinstance(n, QgsLayerTreeGroup) and n.name() == name:
                return n
        return None

    def _ct_for(layer):
        if layer is not None and layer.crs().isValid() and layer.crs() != wgs84:
            return QgsCoordinateTransform(layer.crs(), wgs84, project)
        return None

    def _to_wgs(geom, ct):
        if geom is None or geom.isEmpty():
            return None
        g = QgsGeometry(geom)
        if ct is not None:
            g.transform(ct)
        return g

    def _area_m2(geom):
        try:
            return float(da.convertAreaMeasurement(da.measureArea(geom), QgsUnitTypes.AreaSquareMeters))
        except Exception:
            return 0.0

    def _bucket(layer):
        out = {}
        if layer is None:
            return out, None
        fmap = _field_map(layer)
        idf = _pick_field(fmap, "ID")
        namef = _pick_field(fmap, "Name")
        if idf is None:
            return out, namef
        for f in layer.getFeatures():
            v = f[idf]
            if _is_nullish(v):
                continue
            try:
                out.setdefault(int(v), []).append(f)
            except Exception:  # nosec B112
                continue
        return out, namef

    def _feat_name(f, namef):
        if not namef:
            return ""
        v = f[namef]
        return "" if _is_nullish(v) else " ".join(str(v).split())

    def _rings(polygon):
        return [[(p.x(), p.y()) for p in ring] for ring in polygon]

    def _ab_points(a, b):
        """A, B und die beiden Anzeigepunkte (100 m vor A, von dort 1000 m Richtung A)."""
        pa, pb = QgsPointXY(*a), QgsPointXY(*b)
        back = da.computeSpheroidProject(pa, kml.AB_BACK_M, da.bearing(pa, pb) + math.pi)
        far = da.computeSpheroidProject(back, kml.AB_LENGTH_M, da.bearing(back, pa))
        return [a, b, (back.x(), back.y()), (far.x(), far.y())]

    used_zips = set()

    for ctr_name, frm_map in (selected or {}).items():
        ctr_group = _find_group(project.layerTreeRoot(), ctr_name)
        if ctr_group is None:
            continue
        for frm_name, field_ids in frm_map.items():
            frm_group = _find_group(ctr_group, frm_name)
            if frm_group is None:
                continue

            poly = _find_child_layer(frm_group, "Feldgrenzen")
            line = _find_child_layer(frm_group, "Fahrspuren")
            pt = _find_child_layer(frm_group, "Punkthindernis")
            ar = _find_child_layer(frm_group, "Flaechenhindernis")

            b_by_id, _ = _bucket(poly)
            s_by_id, s_name = _bucket(line)
            p_by_id, p_name = _bucket(pt)
            a_by_id, a_name = _bucket(ar)
            ct_poly, ct_line, ct_pt, ct_ar = _ct_for(poly), _ct_for(line), _ct_for(pt), _ct_for(ar)
            passable_f = _pick_field(_field_map(pt), "befahrbar") if pt is not None else None

            for fid, fname in sorted(dict(_field_catalog_for_frm(frm_group)).items()):
                if field_ids is not None and fid not in field_ids:
                    continue
                field_label = " ".join(str(fname or f"Feld {fid}").split())

                # -- Feldgrenze: alle Grenzen vereinigen, EINE behalten --
                boundary, areas, holes = None, [], []
                b_geoms = [g for g in (_to_wgs(f.geometry(), ct_poly) for f in b_by_id.get(fid, [])) if g is not None]
                if b_geoms:
                    merged = QgsGeometry.unaryUnion(b_geoms) if len(b_geoms) > 1 else b_geoms[0]
                    parts = _polygon_parts(merged)
                    if len(parts) > 1:
                        result["multi_fields"].append(f"{ctr_name} / {frm_name} / {field_label}")
                    if parts:
                        best = _rings(max(parts, key=lambda p: _area_m2(QgsGeometry.fromPolygonXY(p))))
                        boundary = _ccw_closed(best[0])
                        for hole in best[1:]:
                            h = _ccw_closed(hole)
                            if h is not None:
                                holes.append(("", h))
                result["holes"] += len(holes)

                # -- Hindernisse --
                for f in a_by_id.get(fid, []):
                    for part in _polygon_parts(_to_wgs(f.geometry(), ct_ar)):
                        r = _ccw_closed(_rings(part)[0])
                        if r is not None:
                            areas.append((_feat_name(f, a_name), r))
                areas += holes
                points = []
                for f in p_by_id.get(fid, []):
                    g = _to_wgs(f.geometry(), ct_pt)
                    if g is None:
                        continue
                    passable = 0
                    if passable_f and not _is_nullish(f[passable_f]):
                        try:
                            passable = 1 if int(f[passable_f]) else 0
                        except Exception:
                            passable = 0
                    for p in (g.asMultiPoint() if g.isMultipart() else [g.asPoint()]):
                        points.append((_feat_name(f, p_name), passable, (p.x(), p.y())))

                # -- Fahrspuren --
                lines = []
                for f in s_by_id.get(fid, []):
                    raw = f.geometry()
                    if raw is None or raw.isEmpty():
                        continue
                    nm = _feat_name(f, s_name) or "Fahrspur"
                    for src_line in _geometry_to_lines_xy(raw):
                        if len(src_line) < 2:
                            continue
                        single = QgsGeometry.fromPolylineXY(src_line)
                        is_curve = len(src_line) > 2
                        working = single
                        # Verdichten/Verlängern nur bei Kurven; die Hilfsfunktionen
                        # liefern die Geometrie bereits in WGS84
                        if is_curve and densify_enabled:
                            d = _densify_geometry_for_export(working, line, interval_m)
                            if d is not None and not d.isEmpty():
                                working = d
                        if is_curve and extend_enabled:
                            e = _extend_geometry_for_export(working, line, extend_m)
                            if e is not None and not e.isEmpty():
                                working = e
                        if working is single:
                            working = _to_wgs(single, ct_line)
                        parts = _geometry_to_lines_xy(working)
                        if not parts or len(parts[0]) < 2:
                            continue
                        pts = [(p.x(), p.y()) for p in parts[0]]
                        if len(pts) == 2:
                            lines.append((nm, kml.WAYLINE_AB, _ab_points(pts[0], pts[1])))
                        else:
                            lines.append((nm, kml.WAYLINE_CURVE, pts))

                if boundary is None and not areas and not points and not lines:
                    continue

                zname = kml.zip_name(field_label)
                base, n = zname[:-4], 2
                while zname.lower() in used_zips:
                    zname = f"{base}_{n}.zip"
                    n += 1
                used_zips.add(zname.lower())

                os.makedirs(out_dir, exist_ok=True)
                field_uuid = "{%s}" % uuid.uuid5(_UUID_NS, f"{ctr_name}\n{frm_name}\n{fid}")
                path = os.path.join(out_dir, zname)
                kml.write_zip(path, field_label,
                              kml.build_kml(field_label, boundary, areas, points, lines),
                              kml.build_ini(field_label, field_uuid))
                result["files"].append(path)

    result["ok"] = bool(result["files"])
    return result

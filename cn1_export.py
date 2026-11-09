# -*- coding: utf-8 -*-
"""
CN1-Export (CNH: Case IH / Steyr AFS Pro 700, New Holland IntelliView 4).

Zuordnung:
    Kunde / Betrieb / Feld   -> growers.gws / <Betrieb>.FMS / <Feld>.FLS
    Feldgrenzen              -> <Feld>.bnd (genau EIN Außenring je Feld)
    Löcher der Feldgrenze    -> unbefahrbare Flächenhindernisse in <Feld>.wob
    Punkt-/Flaechenhindernis -> <Feld>.wob (Punkt bzw. Fläche)
    Fahrspuren               -> je Linie eine <Spur>.ags (Gerade bzw. Kurve)

CN1 kennt nur eine Feldgrenze je Feld: mehrere Feldgrenzen werden vereinigt;
bleiben danach getrennte Teile übrig, wird nur der größte exportiert.
Ein "befahrbar"-Kennzeichen gibt es in CN1 nicht. Namen sind auf 18 Zeichen
begrenzt.

Die Format-Details stehen in cn1_format.py.
"""

import math
import os
import shutil

from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsLayerTreeGroup, QgsDistanceArea, QgsUnitTypes,
)

try:
    from . import cn1_format as cn1
except Exception:
    import cn1_format as cn1


def _signed_area_xy(ring):
    """Shoelace über (lon, lat): > 0 = gegen den Uhrzeigersinn."""
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _ccw_closed(ring_xy):
    """Ring [(lon, lat), ...] geschlossen und gegen den Uhrzeigersinn."""
    pts = list(ring_xy)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    if _signed_area_xy(pts) < 0:
        pts.reverse()
    return pts + [pts[0]]


def _latlon(ring_xy):
    return [(y, x) for x, y in ring_xy]


def _initial_bearing(a, b):
    """
    Anfangsrichtung des Großkreises (Kugel) von a nach b in Grad, 0 = Nord,
    im Uhrzeigersinn. a, b = (lat, lon). So rechnet auch CNH (im Beispiel exakt
    51,79797°); die ellipsoidische Richtung weicht um ca. 0,08° ab.
    """
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def export_cn1(
    plugin, out_dir, selected,
    densify_enabled=False, interval_m=1.0,
    extend_enabled=False, extend_m=0.0
):
    """
    Schreibt einen CN1-Ordner in out_dir. selected = {Kunde: {Betrieb: set(ids)|None}}.

    Rückgabe: dict mit
        ok            True, wenn etwas exportiert wurde
        path          Pfad des erzeugten <ID>.cn1-Ordners
        multi_fields  Felder, von deren Feldgrenzen nur ein Teil exportiert wurde
        holes         Anzahl Löcher, die als Hindernis geschrieben wurden
        renamed       Namen, die gekürzt/angepasst wurden: [(alt, neu), ...]
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

    result = {"ok": False, "path": None, "multi_fields": [], "holes": 0, "renamed": []}
    renamed_seen = set()

    def _name(text):
        """CN1-Name; merkt sich Kürzungen/Anpassungen für den Hinweis."""
        text = " ".join(str(text or "").split())
        new = cn1.cn1_name(text)
        if new != text and text not in renamed_seen:
            renamed_seen.add(text)
            result["renamed"].append((text, new))
        return new

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
        return "" if _is_nullish(v) else str(v).strip()

    def _rings_xy(polygon):
        """QGIS-Polygon (Liste von Ringen aus QgsPointXY) -> [(lon, lat), ...]-Ringe."""
        return [[(p.x(), p.y()) for p in ring] for ring in polygon]

    # ---- Daten je Kunde / Betrieb / Feld einsammeln ----
    growers = {}   # grower_id -> name
    farms = []     # (farm_id, name, grower_id, grower_name)
    fields = []    # dict je Feld

    for ctr_name, frm_map in (selected or {}).items():
        ctr_group = _find_group(project.layerTreeRoot(), ctr_name)
        if ctr_group is None:
            continue
        grower_id = cn1.stable_id("grower", ctr_name)
        grower_name = _name(ctr_name)

        for frm_name, field_ids in frm_map.items():
            frm_group = _find_group(ctr_group, frm_name)
            if frm_group is None:
                continue
            farm_id = cn1.stable_id("farm", ctr_name, frm_name)

            poly = _find_child_layer(frm_group, "Feldgrenzen")
            line = _find_child_layer(frm_group, "Fahrspuren")
            pt = _find_child_layer(frm_group, "Punkthindernis")
            ar = _find_child_layer(frm_group, "Flaechenhindernis")

            b_by_id, _ = _bucket(poly)
            s_by_id, s_name = _bucket(line)
            p_by_id, p_name = _bucket(pt)
            a_by_id, a_name = _bucket(ar)
            ct_poly, ct_line, ct_pt, ct_ar = _ct_for(poly), _ct_for(line), _ct_for(pt), _ct_for(ar)

            farm_fields = []
            for fid, fname in sorted(dict(_field_catalog_for_frm(frm_group)).items()):
                if field_ids is not None and fid not in field_ids:
                    continue
                field_label = fname or f"Feld {fid}"
                field_id = cn1.stable_id("field", ctr_name, frm_name, fid)

                # -- Feldgrenze: alle Grenzen vereinigen, EIN Polygon behalten --
                boundary = None
                holes = []
                b_geoms = [g for g in (_to_wgs(f.geometry(), ct_poly) for f in b_by_id.get(fid, [])) if g is not None]
                if b_geoms:
                    merged = QgsGeometry.unaryUnion(b_geoms) if len(b_geoms) > 1 else b_geoms[0]
                    parts = _polygon_parts(merged)
                    if len(parts) > 1:
                        result["multi_fields"].append(f"{ctr_name} / {frm_name} / {field_label}")
                    if parts:
                        best = max(parts, key=lambda p: _area_m2(QgsGeometry.fromPolygonXY(p)))
                        rings = _rings_xy(best)
                        outer = _ccw_closed(rings[0])
                        if outer is not None:
                            boundary = {
                                "ring": _latlon(outer),
                                "area": _area_m2(QgsGeometry.fromPolygonXY(best)),
                            }
                            for hole in rings[1:]:
                                h = _ccw_closed(hole)
                                if h is not None:
                                    holes.append(_latlon(h))

                # -- Hindernisse --
                objects = []   # (name, kind, [(lat, lon), ...])
                for f in p_by_id.get(fid, []):
                    g = _to_wgs(f.geometry(), ct_pt)
                    if g is None:
                        continue
                    pts = g.asMultiPoint() if g.isMultipart() else [g.asPoint()]
                    for p in pts:
                        objects.append((_name(_feat_name(f, p_name) or "Hindernis"), cn1.OBJ_POINT, [(p.y(), p.x())]))
                for f in a_by_id.get(fid, []):
                    g = _to_wgs(f.geometry(), ct_ar)
                    for part in _polygon_parts(g):
                        r = _ccw_closed(_rings_xy(part)[0])
                        if r is not None:
                            objects.append((_name(_feat_name(f, a_name) or "Hindernis"), cn1.OBJ_AREA, _latlon(r)))
                for i, h in enumerate(holes, 1):
                    objects.append((_name(f"Loch {i}"), cn1.OBJ_AREA, h))
                result["holes"] += len(holes)

                # -- Fahrspuren --
                guidance = []  # (name, kind, heading, [(lat, lon), ...])
                for f in s_by_id.get(fid, []):
                    raw = f.geometry()
                    if raw is None or raw.isEmpty():
                        continue
                    nm = _name(_feat_name(f, s_name) or "Fahrspur")
                    for src_line in _geometry_to_lines_xy(raw):
                        if len(src_line) < 2:
                            continue
                        single = QgsGeometry.fromPolylineXY(src_line)
                        is_curve = len(src_line) > 2
                        working = single
                        # Verdichten/Verlängern nur bei Kurven - wie bei ISOXML/Gen4/AgGPS.
                        # Die Hilfsfunktionen liefern die Geometrie bereits in WGS84.
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
                        lines = _geometry_to_lines_xy(working)
                        if not lines or len(lines[0]) < 2:
                            continue
                        pts = [(p.y(), p.x()) for p in lines[0]]
                        if len(pts) == 2:
                            guidance.append((nm, cn1.LINE_AB, _initial_bearing(pts[0], pts[1]), pts))
                        else:
                            guidance.append((nm, cn1.LINE_CURVE, 0.0, pts))

                if boundary is None and not objects and not guidance:
                    continue
                farm_fields.append({
                    "id": field_id,
                    "key": (ctr_name, frm_name, fid),
                    "name": _name(field_label),
                    "boundary": boundary,
                    "objects": objects,
                    "guidance": guidance,
                })

            if farm_fields:
                growers[grower_id] = grower_name
                farms.append((farm_id, _name(frm_name), grower_id, grower_name))
                for ff in farm_fields:
                    ff["farm_id"] = farm_id
                fields.extend(farm_fields)

    if not fields:
        return result

    # ---- Schreiben ----
    root_id, index_id = cn1.new_root_ids()
    root = os.path.join(out_dir, f"{root_id}.cn1")
    shared = os.path.join(root, "shared")
    os.makedirs(shared, exist_ok=True)

    def _write(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    _write(os.path.join(root, "index.vy1"),
           cn1.encode_record(cn1.T_INDEX, cn1.V_INDEX, index_id, None, b"\0DSKTP"))

    _write(os.path.join(shared, "growers.gws"), b"".join(
        cn1.encode_record(cn1.T_GROWER, cn1.V_GROWER, gid, None, cn1.grower_payload(gname))
        for gid, gname in growers.items()))

    for farm_id, farm_name, grower_id, grower_name in farms:
        _write(os.path.join(shared, f"{farm_id}.FMS"),
               cn1.encode_record(cn1.T_FARM, cn1.V_FARM, farm_id, grower_id,
                                 cn1.farm_payload(farm_name, grower_id, grower_name)))

    for ff in fields:
        fmd = os.path.join(shared, f"{ff['farm_id']}.FMD")
        fld = os.path.join(fmd, f"{ff['id']}.FLD")
        area = ff["boundary"]["area"] if ff["boundary"] else 0.0
        _write(os.path.join(fmd, f"{ff['id']}.FLS"),
               cn1.encode_record(cn1.T_FIELD, cn1.V_FIELD, ff["id"], ff["farm_id"],
                                 cn1.field_payload(ff["name"], area)))
        os.makedirs(fld, exist_ok=True)

        if ff["boundary"]:
            # Die Feldgrenze trägt im Original keinen eigenen Namen.
            _write(os.path.join(fld, f"{ff['id']}.bnd"),
                   cn1.encode_record(cn1.T_BOUNDARY, cn1.V_BOUNDARY, 0, ff["id"],
                                     cn1.geometry_payload("", 0, ff["boundary"]["ring"])))

        if ff["objects"]:
            _write(os.path.join(fld, f"{ff['id']}.wob"), b"".join(
                cn1.encode_record(cn1.T_OBJECT, cn1.V_OBJECT, i, ff["id"],
                                  cn1.geometry_payload(nm, kind, pts))
                for i, (nm, kind, pts) in enumerate(ff["objects"])))

        used = set()
        for i, (nm, kind, heading, pts) in enumerate(ff["guidance"]):
            line_id = cn1.stable_id("line", *ff["key"], i, nm)
            while line_id in used:
                line_id = cn1.stable_id("line", *ff["key"], i, nm, len(used))
            used.add(line_id)
            _write(os.path.join(fld, f"{line_id}.ags"),
                   cn1.encode_record(cn1.T_GUIDANCE, cn1.V_GUIDANCE, line_id, ff["id"],
                                     cn1.guidance_payload(line_id, nm, kind, heading, pts)))

    result["ok"] = True
    result["path"] = root
    return result


def remove_existing_cn1(out_dir):
    """Löscht vorhandene CN1-Ordner im Zielordner (nach Rückfrage beim Benutzer)."""
    for p in cn1.existing_cn1_dirs(out_dir):
        shutil.rmtree(p, ignore_errors=True)

# -*- coding: utf-8 -*-
"""
AgGPS-Export (Trimble / Case / New Holland u.a.).

Ordnerstruktur:
    AgGPS/Data/<Kunde>/<Betrieb>/<Feld>/
        boundary.shp      (Feldgrenzen)      Spalten: id, Name, Area (m²)
        swaths.shp        (Fahrspuren)       Spalten: id, Name   (id: 0 = Gerade, 1 = Kurve)
        AreaFeature.shp   (Flaechenhindernis)
        PointFeature.shp  (Punkthindernis)

Mehrere Kunden/Betriebe/Felder ergeben jeweils mehrere Ordner.
Geometrien werden nach WGS84 (EPSG:4326) geschrieben.
"""

import os

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsVectorFileWriter,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFields, QgsField, QgsFeature, QgsGeometry, QgsWkbTypes, QgsLayerTreeGroup,
    QgsDistanceArea, QgsUnitTypes,
)
from qgis.PyQt.QtCore import QVariant


def export_aggps(plugin, out_dir, selected):
    """
    Schreibt die AgGPS-Ordnerstruktur. selected = {Kunde: {Betrieb: set(ids)|None}}.
    Gibt True bei Erfolg zurück.
    """
    # Helfer aus dem Hauptmodul (lazy, um Zirkularimport zu vermeiden)
    try:
        from .lk_technik_path_planner import (
            _field_catalog_for_frm, _field_map, _pick_field, _is_nullish, _safe, _find_child_layer
        )
    except Exception:
        from lk_technik_path_planner import (
            _field_catalog_for_frm, _field_map, _pick_field, _is_nullish, _safe, _find_child_layer
        )

    project = QgsProject.instance()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    base_dir = os.path.join(out_dir, "AgGPS", "Data")

    def _iter_groups():
        root = project.layerTreeRoot()
        for n in root.children():
            if isinstance(n, QgsLayerTreeGroup):
                yield n

    def _find_group(parent, name):
        for n in parent.children():
            if isinstance(n, QgsLayerTreeGroup) and n.name() == name:
                return n
        return None

    def _ct_for(layer):
        if layer and layer.crs().isValid() and layer.crs() != wgs84:
            return QgsCoordinateTransform(layer.crs(), wgs84, project)
        return None

    def _area_calc(layer):
        """QgsDistanceArea passend zur Quell-CRS des Layers (ellipsoidisch, m²)."""
        if layer is None:
            return None
        da = QgsDistanceArea()
        da.setSourceCrs(layer.crs(), project.transformContext())
        ell = project.ellipsoid()
        da.setEllipsoid(ell if ell and ell != "NONE" else "WGS84")
        return da

    def _area_m2(da, geom):
        if da is None or geom is None or geom.isEmpty():
            return 0.0
        try:
            raw = da.measureArea(geom)
            return float(da.convertAreaMeasurement(raw, QgsUnitTypes.AreaSquareMeters))
        except Exception:
            return 0.0

    def _geom_wgs(geom, ct, to_multi):
        if geom is None or geom.isEmpty():
            return None
        g = QgsGeometry(geom)
        if ct is not None:
            g.transform(ct)
        if to_multi and not g.isMultipart():
            g.convertToMultiType()
        return g

    def _id_of(feat, idf):
        if not idf:
            return None
        v = feat[idf]
        if _is_nullish(v):
            return None
        try:
            return int(v)
        except Exception:
            return None

    def _name_of(feat, namef):
        if not namef:
            return ""
        v = feat[namef]
        return "" if _is_nullish(v) else str(v).strip()

    def _is_curve(geom):
        """Gerade = genau 2 Stützpunkte, sonst Kurve (typ-unabhängig gezählt)."""
        if geom is None or geom.isEmpty():
            return False
        try:
            n = sum(1 for _ in geom.vertices())
        except Exception:
            n = 0
        return n > 2

    def _bucket(layer):
        """Features eines Layers nach Feld-ID gruppieren -> {id: [feat,...]}."""
        out = {}
        if layer is None:
            return out, None, None
        fmap = _field_map(layer)
        idf = _pick_field(fmap, "ID")
        namef = _pick_field(fmap, "Name")
        for f in layer.getFeatures():
            fid = _id_of(f, idf)
            if fid is None:
                continue
            out.setdefault(fid, []).append(f)
        return out, idf, namef

    def _write_shp(path, wkb_type, field_defs, rows):
        """rows: Liste von (QgsGeometry, attr_dict). Schreibt nur bei >=1 Feature."""
        if not rows:
            return False
        fields = QgsFields()
        for fname, ftype in field_defs:
            fields.append(QgsField(fname, ftype))

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "ESRI Shapefile"
        opts.fileEncoding = "UTF-8"

        writer = QgsVectorFileWriter.create(
            path, fields, wkb_type, wgs84, project.transformContext(), opts
        )
        if writer.hasError() != QgsVectorFileWriter.NoError:
            del writer
            return False

        for geom, attrs in rows:
            if geom is None:
                continue
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat.setAttributes([attrs.get(fd[0]) for fd in field_defs])
            writer.addFeature(feat)
        del writer  # flush/close
        return True

    id_name = [("id", QVariant.Int), ("Name", QVariant.String)]
    boundary_fields = [("id", QVariant.Int), ("Name", QVariant.String), ("Area", QVariant.Double)]
    made_any = False

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

            catalog = dict(_field_catalog_for_frm(frm_group))  # {id: name}

            b_by_id, _, b_name = _bucket(poly)
            s_by_id, _, s_name = _bucket(line)
            p_by_id, _, _ = _bucket(pt)
            a_by_id, _, _ = _bucket(ar)

            ct_poly = _ct_for(poly)
            ct_line = _ct_for(line)
            ct_pt = _ct_for(pt)
            ct_ar = _ct_for(ar)
            da_poly = _area_calc(poly)

            for fid, fname in sorted(catalog.items()):
                if field_ids is not None and fid not in field_ids:
                    continue

                field_name = fname or f"Feld {fid}"
                field_dir = os.path.join(
                    base_dir, _safe(ctr_name), _safe(frm_name), _safe(field_name)
                )

                # boundary.shp  (id, Name, Area in m²)
                b_rows = []
                for f in b_by_id.get(fid, []):
                    raw_geom = f.geometry()
                    g = _geom_wgs(raw_geom, ct_poly, to_multi=True)
                    if g is not None:
                        area_m2 = round(_area_m2(da_poly, raw_geom), 2)
                        b_rows.append((g, {"id": fid,
                                           "Name": _name_of(f, b_name) or field_name,
                                           "Area": area_m2}))

                # swaths.shp  (id: 0 = Gerade, 1 = Kurve)
                s_rows = []
                for f in s_by_id.get(fid, []):
                    raw = f.geometry()
                    g = _geom_wgs(raw, ct_line, to_multi=True)
                    if g is not None:
                        s_rows.append((g, {"id": 1 if _is_curve(raw) else 0,
                                           "Name": _name_of(f, s_name)}))

                # AreaFeature.shp (Flaechenhindernis)
                a_rows = []
                for f in a_by_id.get(fid, []):
                    g = _geom_wgs(f.geometry(), ct_ar, to_multi=True)
                    if g is not None:
                        a_rows.append((g, {"id": fid, "Name": field_name}))

                # PointFeature.shp (Punkthindernis)
                p_rows = []
                for f in p_by_id.get(fid, []):
                    g = _geom_wgs(f.geometry(), ct_pt, to_multi=False)
                    if g is not None:
                        p_rows.append((g, {"id": fid, "Name": field_name}))

                if not (b_rows or s_rows or a_rows or p_rows):
                    continue

                os.makedirs(field_dir, exist_ok=True)
                _write_shp(os.path.join(field_dir, "boundary.shp"),
                           QgsWkbTypes.MultiPolygon, boundary_fields, b_rows)
                _write_shp(os.path.join(field_dir, "swaths.shp"),
                           QgsWkbTypes.MultiLineString, id_name, s_rows)
                _write_shp(os.path.join(field_dir, "AreaFeature.shp"),
                           QgsWkbTypes.MultiPolygon, id_name, a_rows)
                _write_shp(os.path.join(field_dir, "PointFeature.shp"),
                           QgsWkbTypes.Point, id_name, p_rows)
                made_any = True

    return made_any

# -*- coding: utf-8 -*-

import os
import json
import xml.etree.ElementTree as ET

from qgis.core import (
    Qgis,
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsLayerTreeGroup,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsVectorFileWriter
)
from qgis.PyQt.QtCore import QVariant


def import_john_deere_gen4(plugin, gen4_dir, out_dir=None):
    """
    Importiert einen John Deere Gen4 Ordner:
    - MasterData.xml
    - SpatialFiles/*.gjson

    plugin  = Instanz von LkTechnikPathPlanner
    gen4_dir = Ordner, in dem MasterData.xml liegt
    out_dir  = optional, aktuell noch nicht genutzt
    """

    masterdata_path = os.path.join(gen4_dir, "MasterData.xml")
    spatial_dir = os.path.join(gen4_dir, "SpatialFiles")

    if not os.path.exists(masterdata_path):
        plugin.iface.messageBar().pushMessage(
            "Fehler",
            "Im gewählten Ordner wurde keine MasterData.xml gefunden.",
            level=Qgis.Warning,
            duration=5
        )
        return False

    if not os.path.isdir(spatial_dir):
        plugin.iface.messageBar().pushMessage(
            "Fehler",
            "Im Gen4-Ordner wurde kein Unterordner 'SpatialFiles' gefunden.",
            level=Qgis.Warning,
            duration=5
        )
        return False

    try:
        tree = ET.parse(masterdata_path)
        root = tree.getroot()
    except Exception as e:
        plugin.iface.messageBar().pushMessage(
            "Fehler",
            f"MasterData.xml konnte nicht gelesen werden: {e}",
            level=Qgis.Critical,
            duration=6
        )
        return False

    ns = {"jd": "urn:schemas-johndeere-com:Setup"}

    src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    use_project_crs = plugin.dlg.rb_import_project.isChecked()
    target_crs = QgsProject.instance().crs() if use_project_crs else src_crs
    to_target = QgsCoordinateTransform(src_crs, target_crs, QgsProject.instance())

    area_crs = None
    area_transform = None

    try:
        if target_crs.isValid() and target_crs.mapUnits() == Qgis.DistanceUnit.Meters:
            area_crs = target_crs
        else:
            area_crs = QgsCoordinateReferenceSystem("EPSG:32633")
        area_transform = QgsCoordinateTransform(src_crs, area_crs, QgsProject.instance())
    except Exception:
        area_crs = QgsCoordinateReferenceSystem("EPSG:32633")
        area_transform = QgsCoordinateTransform(src_crs, area_crs, QgsProject.instance())

    def _calc_area_from_ring_coords_wgs84(rings_coords):
        """
        rings_coords:
            Liste von Ringen im GeoJSON-Format:
            [
                [[lon, lat], [lon, lat], ...],   # äußerer Ring
                [[lon, lat], [lon, lat], ...],   # Loch optional
            ]
        Berechnet Fläche in m² in einem metrischen CRS.
        """
        try:
            rings_metric = []
            for ring in rings_coords:
                pts_metric = []
                for c in ring:
                    if len(c) < 2:
                        continue
                    pt = area_transform.transform(QgsPointXY(float(c[0]), float(c[1])))
                    pts_metric.append(QgsPointXY(pt.x(), pt.y()))
                if len(pts_metric) >= 3:
                    rings_metric.append(pts_metric)

            if not rings_metric:
                return 0.0

            geom_metric = QgsGeometry.fromPolygonXY(rings_metric)
            return float(geom_metric.area())
        except Exception:
            return 0.0

    def _tx_xy(lon, lat):
        pt = QgsPointXY(float(lon), float(lat))
        if target_crs != src_crs:
            pt = to_target.transform(pt)
        return QgsPointXY(pt.x(), pt.y())

    def _norm_name(s):
        return " ".join((s or "").split())

    def _safe(name):
        return (name or "_untitled_").replace(os.sep, "_").replace("/", "_").strip()

    def _safe_float(v, default=0.0):
        try:
            return float(v)
        except Exception:
            return default

    def _find_or_create_group(parent_group: QgsLayerTreeGroup, name: str) -> QgsLayerTreeGroup:
        wanted = _norm_name(name)
        for ch in parent_group.children():
            if isinstance(ch, QgsLayerTreeGroup) and _norm_name(ch.name()) == wanted:
                return ch
        return parent_group.addGroup(wanted)

    def _create_memory_layers():
        crs_authid = target_crs.authid() if target_crs.isValid() else "EPSG:4326"

        field_layer = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "Feldgrenzen", "memory")
        dp = field_layer.dataProvider()
        dp.addAttributes([
            QgsField("ID", QVariant.Int),
            QgsField("Name", QVariant.String),
            QgsField("Flaeche", QVariant.Double),
        ])
        field_layer.updateFields()

        line_layer = QgsVectorLayer(f"MultiLineString?crs={crs_authid}", "Fahrspuren", "memory")
        dp = line_layer.dataProvider()
        dp.addAttributes([
            QgsField("ID", QVariant.Int),
            QgsField("Name", QVariant.String),
            QgsField("Segment", QVariant.String),
        ])
        line_layer.updateFields()

        point_layer = QgsVectorLayer(f"Point?crs={crs_authid}", "Punkthindernis", "memory")
        dp = point_layer.dataProvider()
        dp.addAttributes([
            QgsField("ID", QVariant.Int),
            QgsField("Name", QVariant.String),
            QgsField("befahrbar", QVariant.Int),
        ])
        point_layer.updateFields()

        area_layer = QgsVectorLayer(f"MultiPolygon?crs={crs_authid}", "Flaechenhindernis", "memory")
        dp = area_layer.dataProvider()
        dp.addAttributes([
            QgsField("ID", QVariant.Int),
            QgsField("befahrbar", QVariant.Int),
        ])
        area_layer.updateFields()

        return {
            "Feldgrenzen": field_layer,
            "Fahrspuren": line_layer,
            "Punkthindernis": point_layer,
            "Flaechenhindernis": area_layer,
        }
    
    def _persist_farm_layers(layers_dict, ctr_name, frm_name, frm_group):
        if not out_dir:
            return layers_dict

        base = os.path.join(out_dir, _safe(ctr_name), _safe(frm_name))
        os.makedirs(base, exist_ok=True)

        new_layers = {}
        tr_ctx = project.transformContext()

        for key, mem_layer in layers_dict.items():
            gpkg_path = os.path.join(base, f"{_safe(key)}.gpkg")

            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = key

            try:
                opts.attributesToExport = [
                    f.name() for f in mem_layer.fields()
                    if f.name().lower() != "fid"
                ]
            except Exception:
                pass

            ret = QgsVectorFileWriter.writeAsVectorFormatV3(
                mem_layer, gpkg_path, tr_ctx, opts
            )

            res = ret[0] if isinstance(ret, (tuple, list)) else ret
            if res != QgsVectorFileWriter.NoError:
                new_layers[key] = mem_layer
                continue

            uri = f"{gpkg_path}|layername={key}"
            file_layer = QgsVectorLayer(uri, mem_layer.name(), "ogr")

            if file_layer.isValid():
                try:
                    parent = project.layerTreeRoot().findLayer(mem_layer.id()).parent()
                except Exception:
                    parent = None

                project.removeMapLayer(mem_layer.id())
                project.addMapLayer(file_layer, False)

                if parent and isinstance(parent, QgsLayerTreeGroup):
                    parent.addLayer(file_layer)
                else:
                    frm_group.addLayer(file_layer)

                plugin._apply_predefined_style(file_layer)
                if key == "Feldgrenzen":
                    plugin._apply_feldgrenzen_color(file_layer, frm_group)

                new_layers[key] = file_layer
            else:
                new_layers[key] = mem_layer

        plugin._reorder_frm_group_layers(frm_group)
        return new_layers

    def _read_json(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _polygon_from_geojson_geometry(geom_json):
        gtype = geom_json.get("type")
        coords = geom_json.get("coordinates", [])

        polygons = []
        source_polygons = []

        if gtype == "Polygon":
            coords = [coords]
        elif gtype != "MultiPolygon":
            return None, None

        for poly in coords:
            rings_xy = []
            rings_src = []

            for ring in poly:
                pts = []
                src_ring = []

                for c in ring:
                    if len(c) < 2:
                        continue
                    src_ring.append([float(c[0]), float(c[1])])
                    pts.append(_tx_xy(c[0], c[1]))

                if len(pts) >= 3:
                    rings_xy.append(pts)
                    rings_src.append(src_ring)

            if rings_xy:
                polygons.append(rings_xy)
                source_polygons.append(rings_src)

        if not polygons:
            return None, None

        return QgsGeometry.fromMultiPolygonXY(polygons), source_polygons

    def _multiline_from_geojson_geometry(geom_json):
        gtype = geom_json.get("type")
        coords = geom_json.get("coordinates", [])

        lines = []

        if gtype == "LineString":
            coords = [coords]
        elif gtype != "MultiLineString":
            return None

        for line in coords:
            pts = []
            for c in line:
                if len(c) < 2:
                    continue
                pts.append(_tx_xy(c[0], c[1]))
            if len(pts) >= 2:
                lines.append(pts)

        if not lines:
            return None

        return QgsGeometry.fromMultiPolylineXY(lines)

    def _point_from_geojson_geometry(geom_json):
        if geom_json.get("type") != "Point":
            return None
        coords = geom_json.get("coordinates", [])
        if len(coords) < 2:
            return None
        return QgsGeometry.fromPointXY(_tx_xy(coords[0], coords[1]))

    # -------------------------------------------------
    # Stammdaten lesen
    # -------------------------------------------------
    client_name_by_guid = {}
    farm_info_by_guid = {}
    field_info_by_guid = {}
    flag_category_active_by_guid = {}

    for el in root.findall(".//jd:Client", ns):
        guid = el.get("StringGuid")
        name = el.get("Name", "Unbenannter Kunde")
        if guid:
            client_name_by_guid[guid] = name

    for el in root.findall(".//jd:Farm", ns):
        guid = el.get("StringGuid")
        name = el.get("Name", "Unbenannter Betrieb")
        client_guid = el.get("Client")
        if guid:
            farm_info_by_guid[guid] = {
                "name": name,
                "client_guid": client_guid
            }

    running_field_id = 1
    for el in root.findall(".//jd:Field", ns):
        guid = el.get("StringGuid")
        name = el.get("Name", f"Feld_{running_field_id}")

        farm_ref_el = el.find("jd:Farm", ns)
        farm_guid = farm_ref_el.text.strip() if farm_ref_el is not None and farm_ref_el.text else None

        if guid:
            field_info_by_guid[guid] = {
                "field_id": running_field_id,
                "name": name,
                "farm_guid": farm_guid
            }
            running_field_id += 1

    for el in root.findall(".//jd:FlagCategory", ns):
        guid = el.get("StringGuid")
        active_el = el.find("jd:AlertPreferences/jd:Active", ns)
        active = False
        if active_el is not None and active_el.text:
            active = active_el.text.strip().lower() == "true"
        if guid:
            flag_category_active_by_guid[guid] = active

    # -------------------------------------------------
    # Gruppen + Layer je Betrieb anlegen
    # -------------------------------------------------
    project = QgsProject.instance()
    per_farm_layers = {}
    per_farm_groups = {}

    def _ensure_farm_layers(farm_guid):
        farm_info = farm_info_by_guid.get(farm_guid, {})
        frm_name = farm_info.get("name", "Unbenannter Betrieb")
        ctr_guid = farm_info.get("client_guid")
        ctr_name = client_name_by_guid.get(ctr_guid, "Unbenannter Kunde")

        key = (ctr_name, frm_name)
        if key in per_farm_layers:
            return per_farm_layers[key]

        root_group = project.layerTreeRoot()
        ctr_group = _find_or_create_group(root_group, ctr_name)
        frm_group = _find_or_create_group(ctr_group, frm_name)

        layers = _create_memory_layers()

        for lyr in layers.values():
            project.addMapLayer(lyr, False)
            frm_group.addLayer(lyr)
            plugin._apply_predefined_style(lyr)

        plugin._apply_feldgrenzen_color(layers["Feldgrenzen"], frm_group)
        plugin._reorder_frm_group_layers(frm_group)

        layers = _persist_farm_layers(layers, ctr_name, frm_name, frm_group)

        per_farm_layers[key] = layers
        per_farm_groups[key] = frm_group
        return layers

    # -------------------------------------------------
    # Feldgrenzen importieren
    # -------------------------------------------------
    for ob in root.findall(".//jd:OperationalBoundary", ns):
        tagged_entity = ob.get("TaggedEntity")
        if tagged_entity not in field_info_by_guid:
            continue

        field_info = field_info_by_guid[tagged_entity]
        farm_guid = field_info.get("farm_guid")
        if not farm_guid:
            continue

        geom_el = ob.find("jd:Geometry", ns)
        if geom_el is None:
            continue

        fn_el = geom_el.find("jd:FilenameWithExtension", ns)
        if fn_el is None or not fn_el.text:
            continue

        gj_path = os.path.join(spatial_dir, fn_el.text.strip())
        if not os.path.exists(gj_path):
            continue

        try:
            data = _read_json(gj_path)
        except Exception:
            continue

        features = data.get("features", [])
        if not features:
            continue

        geom_json = features[0].get("geometry")
        if not geom_json:
            continue

        qgs_geom, source_polygons = _polygon_from_geojson_geometry(geom_json)
        if qgs_geom is None:
            continue

        layers = _ensure_farm_layers(farm_guid)
        layer = layers["Feldgrenzen"]

        feat = QgsFeature(layer.fields())
        feat.setAttribute("ID", int(field_info["field_id"]))
        feat.setAttribute("Name", field_info["name"])
        calc_area = 0.0
        if source_polygons:
            for poly_rings in source_polygons:
                calc_area += _calc_area_from_ring_coords_wgs84(poly_rings)

        feat.setAttribute("Flaeche", calc_area)
        feat.setGeometry(qgs_geom)
        layer.dataProvider().addFeatures([feat])

    # -------------------------------------------------
    # ABLine importieren
    # -------------------------------------------------
    for ab in root.findall(".//jd:Guidance/jd:Tracks/jd:ABLine", ns):
        tagged_entity = ab.get("TaggedEntity")
        field_info = field_info_by_guid.get(tagged_entity)
        if not field_info:
            continue

        farm_guid = field_info.get("farm_guid")
        if not farm_guid:
            continue

        a_el = ab.find("jd:APoint", ns)
        b_el = ab.find("jd:BPoint", ns)
        if a_el is None or b_el is None:
            continue

        try:
            a_pt = _tx_xy(a_el.get("Longitude"), a_el.get("Latitude"))
            b_pt = _tx_xy(b_el.get("Longitude"), b_el.get("Latitude"))
        except Exception:
            continue

        qgs_geom = QgsGeometry.fromPolylineXY([a_pt, b_pt])
        layers = _ensure_farm_layers(farm_guid)
        layer = layers["Fahrspuren"]

        feat = QgsFeature(layer.fields())
        feat.setAttribute("ID", int(field_info["field_id"]))
        feat.setAttribute("Name", ab.get("Name", "AB Line"))
        feat.setAttribute("Segment", None)
        feat.setGeometry(qgs_geom)
        layer.dataProvider().addFeatures([feat])

    # -------------------------------------------------
    # ABCurve importieren
    # -------------------------------------------------
    for curve in root.findall(".//jd:Guidance/jd:Tracks/jd:ABCurve", ns):
        tagged_entity = curve.get("TaggedEntity")
        field_info = field_info_by_guid.get(tagged_entity)
        if not field_info:
            continue

        farm_guid = field_info.get("farm_guid")
        if not farm_guid:
            continue

        geom_el = curve.find("jd:Geometry", ns)
        if geom_el is None:
            continue

        fn_el = geom_el.find("jd:FilenameWithExtension", ns)
        if fn_el is None or not fn_el.text:
            continue

        gj_path = os.path.join(spatial_dir, fn_el.text.strip())
        if not os.path.exists(gj_path):
            continue

        try:
            data = _read_json(gj_path)
        except Exception:
            continue

        features = data.get("features", [])
        if not features:
            continue

        geom_json = features[0].get("geometry")
        if not geom_json:
            continue

        qgs_geom = _multiline_from_geojson_geometry(geom_json)
        if qgs_geom is None:
            continue

        layers = _ensure_farm_layers(farm_guid)
        layer = layers["Fahrspuren"]

        feat = QgsFeature(layer.fields())
        feat.setAttribute("ID", int(field_info["field_id"]))
        feat.setAttribute("Name", curve.get("Name", "AB Curve"))
        feat.setAttribute("Segment", None)
        feat.setGeometry(qgs_geom)
        layer.dataProvider().addFeatures([feat])

    # -------------------------------------------------
    # Flags importieren
    # Punkt oder Fläche je nach GJSON-Geometrie
    # befahrbar = 1 wenn AlertPreferences false
    # befahrbar = 0 wenn AlertPreferences true
    # -------------------------------------------------
    for flag in root.findall(".//jd:Flag", ns):
        tagged_entity = flag.get("TaggedEntity")
        field_info = field_info_by_guid.get(tagged_entity)
        if not field_info:
            continue

        farm_guid = field_info.get("farm_guid")
        if not farm_guid:
            continue

        category_guid = flag.get("FlagCategory")
        alert_active = flag_category_active_by_guid.get(category_guid, True)
        befahrbar = 0 if alert_active else 1

        geom_el = flag.find("jd:Geometry", ns)
        if geom_el is None:
            continue

        fn_el = geom_el.find("jd:FilenameWithExtension", ns)
        if fn_el is None or not fn_el.text:
            continue

        gj_path = os.path.join(spatial_dir, fn_el.text.strip())
        if not os.path.exists(gj_path):
            continue

        try:
            data = _read_json(gj_path)
        except Exception:
            continue

        # Flag-GJSON ist manchmal direkt Feature, manchmal FeatureCollection
        if data.get("type") == "Feature":
            geom_json = data.get("geometry")
        else:
            features = data.get("features", [])
            geom_json = features[0].get("geometry") if features else None

        if not geom_json:
            continue

        layers = _ensure_farm_layers(farm_guid)

        gtype = geom_json.get("type")
        if gtype == "Point":
            qgs_geom = _point_from_geojson_geometry(geom_json)
            if qgs_geom is None:
                continue

            layer = layers["Punkthindernis"]
            feat = QgsFeature(layer.fields())
            feat.setAttribute("ID", int(field_info["field_id"]))
            feat.setAttribute("Name", flag.get("Name", "Punkthindernis"))
            feat.setAttribute("befahrbar", befahrbar)
            feat.setGeometry(qgs_geom)
            layer.dataProvider().addFeatures([feat])

        elif gtype in ("Polygon", "MultiPolygon"):
            qgs_geom, _ = _polygon_from_geojson_geometry(geom_json)
            if qgs_geom is None:
                continue

            layer = layers["Flaechenhindernis"]
            feat = QgsFeature(layer.fields())
            feat.setAttribute("ID", int(field_info["field_id"]))
            feat.setAttribute("befahrbar", befahrbar)
            feat.setGeometry(qgs_geom)
            layer.dataProvider().addFeatures([feat])

    # -------------------------------------------------
    # Layer aktualisieren
    # -------------------------------------------------
    for layers in per_farm_layers.values():
        for lyr in layers.values():
            lyr.updateExtents()

    # -------------------------------------------------
    # NEU: Felder-Katalog (Felder.csv) je Betrieb schreiben
    # -------------------------------------------------
    try:
        try:
            from .lk_technik_path_planner import (
                _felder_csv_path_in_dir, _read_felder_csv, _write_felder_csv,
                _load_felder_layer, FELDER_LAYER_NAME
            )
        except Exception:
            from lk_technik_path_planner import (
                _felder_csv_path_in_dir, _read_felder_csv, _write_felder_csv,
                _load_felder_layer, FELDER_LAYER_NAME
            )

        # Felder je (ctr_name, frm_name) sammeln
        felder_by_key = {}
        for info in field_info_by_guid.values():
            farm_guid = info.get("farm_guid")
            farm_info = farm_info_by_guid.get(farm_guid, {})
            frm_name = farm_info.get("name", "Unbenannter Betrieb")
            ctr_guid = farm_info.get("client_guid")
            ctr_name = client_name_by_guid.get(ctr_guid, "Unbenannter Kunde")
            key = (ctr_name, frm_name)
            try:
                fid = int(info["field_id"])
            except Exception:
                continue
            felder_by_key.setdefault(key, {})[fid] = info.get("name", "")

        for key, rows in felder_by_key.items():
            frm_group = per_farm_groups.get(key)
            if frm_group is None:
                continue  # für dieses Feld wurde keine Gruppe angelegt
            ctr_name, frm_name = key

            if out_dir:
                base = os.path.join(out_dir, _safe(ctr_name), _safe(frm_name))
                csv_path = _felder_csv_path_in_dir(base)
                merged = _read_felder_csv(csv_path)
                for fid, nm in rows.items():
                    if fid not in merged or (not merged.get(fid) and nm):
                        merged[fid] = nm
                _write_felder_csv(csv_path, merged)
                # vorhandenen Felder-Layer neu laden oder neu anlegen
                existing = None
                for node in frm_group.children():
                    try:
                        lyr = node.layer()
                    except Exception:
                        lyr = None
                    if isinstance(lyr, QgsVectorLayer) and lyr.name() == FELDER_LAYER_NAME:
                        existing = lyr
                        break
                if existing is not None:
                    existing.reload()
                else:
                    felder_layer = _load_felder_layer(csv_path)
                    if felder_layer is not None:
                        project.addMapLayer(felder_layer, False)
                        frm_group.insertLayer(0, felder_layer)
            else:
                # Memory-Import: geometrieloser Felder-Layer
                mem_felder = QgsVectorLayer("None", FELDER_LAYER_NAME, "memory")
                dpf = mem_felder.dataProvider()
                dpf.addAttributes([QgsField("id", QVariant.Int), QgsField("Name", QVariant.String)])
                mem_felder.updateFields()
                feats = []
                for fid in sorted(rows.keys()):
                    f = QgsFeature(mem_felder.fields())
                    f.setAttribute("id", int(fid))
                    f.setAttribute("Name", rows.get(fid, ""))
                    feats.append(f)
                if feats:
                    dpf.addFeatures(feats)
                mem_felder.updateExtents()
                project.addMapLayer(mem_felder, False)
                frm_group.insertLayer(0, mem_felder)

        for frm_group in per_farm_groups.values():
            try:
                plugin._reorder_frm_group_layers(frm_group)
            except Exception:
                pass
    except Exception:
        pass

    plugin.iface.messageBar().pushMessage(
        "Erfolgreich",
        "John Deere Gen4 Daten wurden importiert.",
        level=Qgis.Success,
        duration=5
    )
    return True
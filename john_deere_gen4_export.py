# -*- coding: utf-8 -*-

import os
import math
import json
import uuid
import datetime
import xml.etree.ElementTree as ET
import xml.dom.minidom

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsLayerTreeGroup,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY
)


def export_john_deere_gen4(plugin, out_dir, selected):
    """
    John Deere Gen4 Grundexport:
    - Gen4/
    - Gen4/SpatialFiles/
    - MasterData.xml im Deere SetupFile-Format
    - Boundary{UUID}.gjson für Feldgrenzen
    """

    def _field_map(layer: QgsVectorLayer) -> dict:
        return {f.name().lower(): f.name() for f in layer.fields()}

    def _pick_field(fmap: dict, *candidates: str):
        for c in candidates:
            n = fmap.get(c.lower())
            if n:
                return n
        return None

    def _find_child_layer_by_name(group: QgsLayerTreeGroup, name: str):
        for node in group.children():
            try:
                lyr = node.layer()
            except Exception:
                lyr = None
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == name:
                return lyr
        return None

    def _iter_ctr_groups():
        root = QgsProject.instance().layerTreeRoot()
        for node in root.children():
            if isinstance(node, QgsLayerTreeGroup):
                yield node

    def _iter_frm_groups(ctr_group: QgsLayerTreeGroup):
        for node in ctr_group.children():
            if isinstance(node, QgsLayerTreeGroup):
                yield node

    def _new_guid() -> str:
        return str(uuid.uuid4())

    def _utc_now_iso() -> str:
        # Format wie im Deere-Beispiel: 2026-04-02T09:27:02.633Z
        now = datetime.datetime.utcnow()
        return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"

    def _to_wgs84_transform(layer):
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if layer and layer.crs().isValid() and layer.crs() != wgs84:
            return QgsCoordinateTransform(layer.crs(), wgs84, QgsProject.instance())
        return None

    def _pt_to_lonlat(pt, ct):
        if ct:
            p = ct.transform(QgsPointXY(pt.x(), pt.y()))
            return [p.x(), p.y()]   # lon, lat
        return [pt.x(), pt.y()]     # lon, lat

    def _polygon_feature_to_geojson_geometry(feature, ct):
        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            return None

        multi = geom.asMultiPolygon()
        if multi:
            coords = []
            for poly in multi:
                poly_coords = []
                for ring in poly:
                    ring_coords = [_pt_to_lonlat(pt, ct) for pt in ring]
                    if ring_coords and ring_coords[0] != ring_coords[-1]:
                        ring_coords.append(ring_coords[0])
                    if len(ring_coords) >= 4:
                        poly_coords.append(ring_coords)
                if poly_coords:
                    coords.append(poly_coords)

            if not coords:
                return None

            if len(coords) == 1:
                return {
                    "type": "Polygon",
                    "coordinates": coords[0]
                }

            return {
                "type": "MultiPolygon",
                "coordinates": coords
            }

        single = geom.asPolygon()
        if single:
            poly_coords = []
            for ring in single:
                ring_coords = [_pt_to_lonlat(pt, ct) for pt in ring]
                if ring_coords and ring_coords[0] != ring_coords[-1]:
                    ring_coords.append(ring_coords[0])
                if len(ring_coords) >= 4:
                    poly_coords.append(ring_coords)

            if not poly_coords:
                return None

            return {
                "type": "Polygon",
                "coordinates": poly_coords
            }

        return None
    
    def _heading_from_points(a_lon, a_lat, b_lon, b_lat):
        """
        Deere-Heading in Grad (0-360), aus A -> B.
        Einfacher geografischer Richtungswinkel auf Basis lon/lat.
        """
        dx = b_lon - a_lon
        dy = b_lat - a_lat
        heading = math.degrees(math.atan2(dx, dy))
        if heading < 0:
            heading += 360.0
        return heading

    def _write_boundary_geojson(path, geometry):
        data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": 0,
                    "type": "Feature",
                    "properties": {
                        "boundarytype": "Exterior",
                        "creationMethod": 501898,
                        "isactive": True,
                        "ispassable": False,
                        "name": None,
                        "offsetid": None,
                        "parent": None,
                        "signaltype": 0
                    },
                    "geometry": geometry
                }
            ]
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _write_abcurve_geojson(path, line_points_lonlat):
        coords = []
        for pt in line_points_lonlat:
            lon = pt[0]
            lat = pt[1]
            coords.append([lon, lat, 0, 0])

        data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "curvetype": "Track0"
                    },
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [coords]
                    }
                }
            ]
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    
    def _write_flag_geojson(path, lon, lat):
        data = {
            "geometry": {
                "coordinates": [lon, lat],
                "type": "Point"
            },
            "type": "Feature"
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    
    def _write_area_flag_geojson(path, geometry):
        data = {
            "type": "Feature",
            "geometry": geometry
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    
    def _ensure_flag_category(name, active):
        key = name.strip().lower()
        if key not in flag_category_defs:
            flag_category_defs[key] = {
                "CreationDate": timestamp,
                "SourceNode": source_node_guid,
                "LastModifiedDate": timestamp,
                "Archived": "false",
                "StringGuid": _new_guid(),
                "Name": name.strip(),
                "IsReferenceData": "true",
                "BackgroundColor": "#FFFFFF",
                "Active": "true" if active else "false"
            }
        return flag_category_defs[key]["StringGuid"]

    # -------------------------------------------------
    # Ordnerstruktur
    # -------------------------------------------------
    gen4_dir = os.path.join(out_dir, "Gen4")
    spatial_dir = os.path.join(gen4_dir, "SpatialFiles")
    os.makedirs(spatial_dir, exist_ok=True)

    # -------------------------------------------------
    # Metadaten
    # -------------------------------------------------
    source_node_guid = _new_guid()
    timestamp = _utc_now_iso()

    ET.register_namespace("", "urn:schemas-johndeere-com:Setup")
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")
    ET.register_namespace("sc", "urn:schemas-johndeere-com:SetupCore")

    root_xml = ET.Element(
        "{urn:schemas-johndeere-com:Setup}SetupFile"
    )

    ET.SubElement(root_xml, "{urn:schemas-johndeere-com:Setup}SourceApp", {
        "minor": "0",
        "major": "1",
        "build": "0",
        "revision": "0",
        "nameSourceApp": "LK-Technik Path Planner",
        "SourceAppClientId": "lk-technik-path-planner"
    })

    fsv = ET.SubElement(root_xml, "{urn:schemas-johndeere-com:Setup}FileSchemaVersion", {
        "nonProductionCode": "0"
    })
    ET.SubElement(fsv, "{urn:schemas-johndeere-com:Setup}FileSchemaContentVersion", {
        "major": "2",
        "minor": "54"
    })
    ET.SubElement(fsv, "{urn:schemas-johndeere-com:Setup}UnitOfMeasureVersion", {
        "major": "1",
        "minor": "12"
    })
    ET.SubElement(fsv, "{urn:schemas-johndeere-com:Setup}RepresentationSystemVersion", {
        "major": "3",
        "minor": "25"
    })

    setup_el = ET.SubElement(root_xml, "{urn:schemas-johndeere-com:Setup}Setup")

    exported_any = False
    abcurve_defs = []
    abline_defs = []
    flag_category_defs = {}
    flag_defs = []
    boundary_defs = []   # OperationalBoundary erst am Ende schreiben (Reihenfolge!)

    flag_category_guid_befahrbar = _ensure_flag_category("Hindernis befahrbar", False)
    flag_category_guid_nicht_befahrbar = _ensure_flag_category("Hindernis nicht befahrbar", True)

    for ctr_group in _iter_ctr_groups():
        ctr_name = ctr_group.name()
        if ctr_name not in selected:
            continue

        client_guid = _new_guid()

        ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}Client", {
            "CreationDate": timestamp,
            "SourceNode": source_node_guid,
            "LastModifiedDate": timestamp,
            "Archived": "false",
            "StringGuid": client_guid,
            "Name": ctr_name
        })

        for frm_group in _iter_frm_groups(ctr_group):
            frm_name = frm_group.name()
            if frm_name not in selected[ctr_name]:
                continue

            farm_guid = _new_guid()

            ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}Farm", {
                "CreationDate": timestamp,
                "SourceNode": source_node_guid,
                "LastModifiedDate": timestamp,
                "Archived": "false",
                "StringGuid": farm_guid,
                "Name": frm_name,
                "Client": client_guid
            })

            
            polygon_layer = _find_child_layer_by_name(frm_group, "Feldgrenzen")

            # Katalog-getrieben exportieren (Felder.csv): ein Feld je Feld-ID,
            # mehrere Feldgrenzen pro Feld, und Felder ohne Grenze (nur Fahrspuren).
            try:
                try:
                    from .lk_technik_path_planner import _field_catalog_for_frm
                except Exception:
                    from lk_technik_path_planner import _field_catalog_for_frm
                catalog = _field_catalog_for_frm(frm_group)  # [(id, name), ...]
            except Exception:
                catalog = []

            # Fallback: kein Katalog vorhanden -> aus Feldgrenzen ableiten
            if not catalog and polygon_layer:
                _pf = _field_map(polygon_layer)
                _idf = _pick_field(_pf, "ID")
                _nmf = _pick_field(_pf, "Name")
                seen = {}
                for f in polygon_layer.getFeatures():
                    try:
                        _fid = int(f[_idf]) if _idf else int(f.id())
                    except Exception:
                        continue
                    if _fid not in seen:
                        seen[_fid] = str(f[_nmf]) if _nmf else "Feld_%s" % _fid
                catalog = sorted(seen.items())

            if not catalog:
                continue

            ct_poly = _to_wgs84_transform(polygon_layer) if polygon_layer else None
            poly_fmap = _field_map(polygon_layer) if polygon_layer else {}
            id_field = _pick_field(poly_fmap, "ID")
            name_field = _pick_field(poly_fmap, "Name")

            # Feldgrenzen nach Feld-ID gruppieren (0..n Grenzen je Feld)
            boundary_by_id = {}
            if polygon_layer is not None:
                for bf in polygon_layer.getFeatures():
                    try:
                        _bid = int(bf[id_field]) if id_field else int(bf.id())
                    except Exception:
                        continue
                    boundary_by_id.setdefault(_bid, []).append(bf)

            field_ids_filter = selected[ctr_name][frm_name]
            field_guid_map = {}

            for field_id, cat_name in catalog:
                if (field_ids_filter is not None) and (field_id not in field_ids_filter):
                    continue

                boundaries = boundary_by_id.get(field_id, [])

                # Feldname: Feldgrenze bevorzugt, sonst Katalogname
                field_name = cat_name or ("Feld_%s" % field_id)
                if boundaries and name_field:
                    try:
                        _bn = boundaries[0][name_field]
                        if _bn not in (None, ""):
                            field_name = str(_bn)
                    except Exception:
                        pass

                # GENAU EIN Feld je Feld-ID
                field_guid = _new_guid()
                field_guid_map[field_id] = field_guid

                field_el = ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}Field", {
                    "CreationDate": timestamp,
                    "SourceNode": source_node_guid,
                    "LastModifiedDate": timestamp,
                    "Archived": "false",
                    "StringGuid": field_guid,
                    "Name": field_name
                })

                farm_ref_el = ET.SubElement(field_el, "{urn:schemas-johndeere-com:Setup}Farm")
                farm_ref_el.text = farm_guid

                # je vorhandener Feldgrenze eine eigene OperationalBoundary,
                # aber alle mit demselben Feld (TaggedEntity = field_guid) verknuepft.
                # WICHTIG: Die OperationalBoundary-Elemente werden NICHT hier,
                # sondern erst am Ende geschrieben (nach Guidance) – die John-Deere-
                # Struktur verlangt: erst alle Felder, dann Guidance, dann alle
                # OperationalBoundaries. Interleaving macht die Datei am Terminal ungueltig.
                for bf in boundaries:
                    geometry = _polygon_feature_to_geojson_geometry(bf, ct_poly)
                    if geometry is None:
                        continue

                    boundary_guid = _new_guid()
                    boundary_filename = f"Boundary{boundary_guid}.gjson"
                    boundary_path = os.path.join(spatial_dir, boundary_filename)
                    _write_boundary_geojson(boundary_path, geometry)

                    # Name der Grenze: eigener Name der Feldgrenze, sonst Feldname
                    b_name = field_name
                    if name_field:
                        try:
                            _bn = bf[name_field]
                            if _bn not in (None, ""):
                                b_name = str(_bn)
                        except Exception:
                            pass

                    boundary_defs.append({
                        "StringGuid": boundary_guid,
                        "TaggedEntity": field_guid,
                        "Name": b_name,
                        "FilenameWithExtension": boundary_filename,
                    })

                exported_any = True

            point_layer = _find_child_layer_by_name(frm_group, "Punkthindernis")
            if point_layer:
                ct_point = _to_wgs84_transform(point_layer)
                point_fmap = _field_map(point_layer)

                point_id_field = _pick_field(point_fmap, "ID")
                point_name_field = _pick_field(point_fmap, "Name")
                point_befahrbar_field = _pick_field(point_fmap, "befahrbar")

                for point_feature in point_layer.getFeatures():
                    if point_id_field is None:
                        continue

                    raw_point_id = point_feature[point_id_field]
                    try:
                        point_field_id = int(raw_point_id)
                    except Exception:
                        continue

                    field_guid_for_flag = field_guid_map.get(point_field_id)
                    if not field_guid_for_flag:
                        continue

                    flag_name = (
                        str(point_feature[point_name_field]).strip()
                        if point_name_field else "Punkthindernis"
                    )
                    if not flag_name:
                        flag_name = "Punkthindernis"

                    befahrbar_val = 0
                    if point_befahrbar_field:
                        try:
                            befahrbar_val = int(point_feature[point_befahrbar_field])
                        except Exception:
                            befahrbar_val = 0

                    # Kategorie je nach Befahrbarkeit
                    if befahrbar_val == 1:
                        category_guid = flag_category_guid_befahrbar
                    else:
                        category_guid = flag_category_guid_nicht_befahrbar

                    geom = point_feature.geometry()
                    if geom is None or geom.isEmpty():
                        continue

                    pt = geom.asPoint()
                    lon, lat = _pt_to_lonlat(pt, ct_point)

                    flag_guid = _new_guid()
                    flag_filename = f"Flag{flag_guid}.gjson"
                    flag_path = os.path.join(spatial_dir, flag_filename)
                    _write_flag_geojson(flag_path, lon, lat)

                    flag_defs.append({
                        "CreationDate": timestamp,
                        "SourceNode": source_node_guid,
                        "LastModifiedDate": timestamp,
                        "Archived": "false",
                        "StringGuid": flag_guid,
                        "TaggedEntity": field_guid_for_flag,
                        "FlagCategory": category_guid,
                        "Name": flag_name,
                        "FilenameWithExtension": flag_filename,
                        "Path": "./SpatialFiles/"
                    })
            area_layer = _find_child_layer_by_name(frm_group, "Flaechenhindernis")
            if area_layer:
                ct_area = _to_wgs84_transform(area_layer)
                area_fmap = _field_map(area_layer)

                area_id_field = _pick_field(area_fmap, "ID")
                area_befahrbar_field = _pick_field(area_fmap, "befahrbar")

                for area_feature in area_layer.getFeatures():
                    if area_id_field is None:
                        continue

                    raw_area_id = area_feature[area_id_field]
                    try:
                        area_field_id = int(raw_area_id)
                    except Exception:
                        continue

                    field_guid_for_flag = field_guid_map.get(area_field_id)
                    if not field_guid_for_flag:
                        continue

                    befahrbar_val = 0
                    if area_befahrbar_field:
                        try:
                            befahrbar_val = int(area_feature[area_befahrbar_field])
                        except Exception:
                            befahrbar_val = 0

                    if befahrbar_val == 1:
                        category_guid = flag_category_guid_befahrbar
                        flag_name = "befahrbares Hindernis"
                    else:
                        category_guid = flag_category_guid_nicht_befahrbar
                        flag_name = "nicht befahrbares Hindernis"

                    area_geom = _polygon_feature_to_geojson_geometry(area_feature, ct_area)
                    if area_geom is None:
                        continue

                    flag_guid = _new_guid()
                    flag_filename = f"Flag{flag_guid}.gjson"
                    flag_path = os.path.join(spatial_dir, flag_filename)
                    _write_area_flag_geojson(flag_path, area_geom)

                    flag_defs.append({
                        "CreationDate": timestamp,
                        "SourceNode": source_node_guid,
                        "LastModifiedDate": timestamp,
                        "Archived": "false",
                        "StringGuid": flag_guid,
                        "TaggedEntity": field_guid_for_flag,
                        "FlagCategory": category_guid,
                        "Name": flag_name,
                        "FilenameWithExtension": flag_filename,
                        "Path": "./SpatialFiles/"
                    })

            line_layer = _find_child_layer_by_name(frm_group, "Fahrspuren")
            if line_layer:
                ct_line = _to_wgs84_transform(line_layer)
                line_fmap = _field_map(line_layer)
                line_id_field = _pick_field(line_fmap, "ID")
                line_name_field = _pick_field(line_fmap, "Name")

                for track_feature in line_layer.getFeatures():
                    if line_id_field is None:
                        continue

                    raw_track_id = track_feature[line_id_field]
                    try:
                        track_field_id = int(raw_track_id)
                    except Exception:
                        continue

                    # nur exportierte Felder berücksichtigen
                    field_guid_for_track = field_guid_map.get(track_field_id)
                    if not field_guid_for_track:
                        continue

                    track_name = (
                        str(track_feature[line_name_field]).strip()
                        if line_name_field else "AB Line"
                    )
                    if not track_name:
                        track_name = "AB Line"

                    geom = track_feature.geometry()
                    if geom is None or geom.isEmpty():
                        continue

                    lines = geom.asMultiPolyline() or []
                    if not lines:
                        single = geom.asPolyline()
                        if single:
                            lines = [single]

                    if len(lines) != 1:
                        continue

                    line = lines[0]
                    if len(line) < 2:
                        continue

                    # Alle Punkte in WGS84 umrechnen
                    line_lonlat = [_pt_to_lonlat(pt, ct_line) for pt in line]

                    a_lon, a_lat = line_lonlat[0]
                    b_lon, b_lat = line_lonlat[1]
                    heading = _heading_from_points(a_lon, a_lat, b_lon, b_lat)
                    track_guid = _new_guid()

                    # -------------------------------------------------
                    # ABLine = genau 2 Punkte
                    # -------------------------------------------------
                    if len(line) == 2:
                        abline_defs.append({
                            "type": "ABLine",
                            "CreationDate": timestamp,
                            "SourceNode": source_node_guid,
                            "LastModifiedDate": timestamp,
                            "Archived": "false",
                            "StringGuid": track_guid,
                            "Name": track_name,
                            "TaggedEntity": field_guid_for_track,
                            "APoint": {"Latitude": str(a_lat), "Longitude": str(a_lon), "Slope": "0"},
                            "BPoint": {"Latitude": str(b_lat), "Longitude": str(b_lon), "Slope": "0"},
                            "Heading": str(heading)
                        })

                    # -------------------------------------------------
                    # ABCurve = mehr als 2 Punkte
                    # -------------------------------------------------
                    else:
                        curve_filename = f"AbCurve{track_guid}.gjson"
                        curve_path = os.path.join(spatial_dir, curve_filename)
                        _write_abcurve_geojson(curve_path, line_lonlat)

                        abcurve_defs.append({
                            "type": "ABCurve",
                            "CreationDate": timestamp,
                            "SourceNode": source_node_guid,
                            "LastModifiedDate": timestamp,
                            "Archived": "false",
                            "StringGuid": track_guid,
                            "Name": track_name,
                            "TaggedEntity": field_guid_for_track,
                            "FilenameWithExtension": curve_filename,
                            "Path": "./SpatialFiles/",
                            "APoint": {"Latitude": str(a_lat), "Longitude": str(a_lon), "Slope": "0"},
                            "BPoint": {"Latitude": str(b_lat), "Longitude": str(b_lon), "Slope": "0"},
                            "Heading": str(heading)
                        })
    # -------------------------------------------------
    # FlagCategories schreiben
    # -------------------------------------------------
    for fc in flag_category_defs.values():
        fc_el = ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}FlagCategory", {
            "CreationDate": fc["CreationDate"],
            "SourceNode": fc["SourceNode"],
            "LastModifiedDate": fc["LastModifiedDate"],
            "Archived": fc["Archived"],
            "StringGuid": fc["StringGuid"],
            "Name": fc["Name"],
            "IsReferenceData": fc["IsReferenceData"],
            "BackgroundColor": fc["BackgroundColor"]
        })

        alert_el = ET.SubElement(fc_el, "{urn:schemas-johndeere-com:Setup}AlertPreferences")
        ET.SubElement(alert_el, "{urn:schemas-johndeere-com:Setup}Active").text = fc["Active"]

    # -------------------------------------------------
    # Flags schreiben
    # -------------------------------------------------
    for fd in flag_defs:
        flag_el = ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}Flag", {
            "CreationDate": fd["CreationDate"],
            "SourceNode": fd["SourceNode"],
            "LastModifiedDate": fd["LastModifiedDate"],
            "Archived": fd["Archived"],
            "StringGuid": fd["StringGuid"],
            "TaggedEntity": fd["TaggedEntity"],
            "FlagCategory": fd["FlagCategory"],
            "Name": fd["Name"]
        })

        geometry_el = ET.SubElement(flag_el, "{urn:schemas-johndeere-com:Setup}Geometry")
        ET.SubElement(
            geometry_el,
            "{urn:schemas-johndeere-com:Setup}FilenameWithExtension"
        ).text = fd["FilenameWithExtension"]
        ET.SubElement(
            geometry_el,
            "{urn:schemas-johndeere-com:Setup}Path"
        ).text = fd["Path"]
    # -------------------------------------------------
    # Guidance erst am Ende schreiben
    # -------------------------------------------------
    if abcurve_defs or abline_defs:
        guidance_el = ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}Guidance")
        tracks_el = ET.SubElement(guidance_el, "{urn:schemas-johndeere-com:Setup}Tracks")

        # ZUERST ABCurves
        for td in abcurve_defs:
            curve_el = ET.SubElement(tracks_el, "{urn:schemas-johndeere-com:Setup}ABCurve", {
                "CreationDate": td["CreationDate"],
                "SourceNode": td["SourceNode"],
                "LastModifiedDate": td["LastModifiedDate"],
                "Archived": td["Archived"],
                "StringGuid": td["StringGuid"],
                "Name": td["Name"],
                "TaggedEntity": td["TaggedEntity"]
            })

            ET.SubElement(curve_el, "{urn:schemas-johndeere-com:Setup}SignalType", {
                "Representation": "dtSignalType",
                "Value": "dtiSignalTypeUnknown"
            })

            geometry_el = ET.SubElement(curve_el, "{urn:schemas-johndeere-com:Setup}Geometry")
            ET.SubElement(
                geometry_el,
                "{urn:schemas-johndeere-com:Setup}FilenameWithExtension"
            ).text = td["FilenameWithExtension"]
            ET.SubElement(
                geometry_el,
                "{urn:schemas-johndeere-com:Setup}Path"
            ).text = td["Path"]

            tram_el = ET.SubElement(curve_el, "{urn:schemas-johndeere-com:Setup}TramLineAttributes")
            ET.SubElement(tram_el, "{urn:schemas-johndeere-com:Setup}TrackOffset").text = "0"
            ET.SubElement(tram_el, "{urn:schemas-johndeere-com:Setup}Spacing").text = "1"

            proj_el = ET.SubElement(curve_el, "{urn:schemas-johndeere-com:Setup}SpatialProjection")
            ET.SubElement(proj_el, "{urn:schemas-johndeere-com:Setup}ProjectionType", {
                "Representation": "dtProjectionType",
                "Value": "dtiProjectionDeere"
            })
            ET.SubElement(proj_el, "{urn:schemas-johndeere-com:Setup}ElevationReferencePoint", {
                "Representation": "vrElevation",
                "Value": "0",
                "SourceUnit": "m"
            })

            ET.SubElement(curve_el, "{urn:schemas-johndeere-com:Setup}APoint", td["APoint"])
            ET.SubElement(curve_el, "{urn:schemas-johndeere-com:Setup}BPoint", td["BPoint"])

            ET.SubElement(curve_el, "{urn:schemas-johndeere-com:Setup}Heading", {
                "Representation": "vrHeading",
                "Value": td["Heading"],
                "SourceUnit": "arcdeg"
            })

        # DANACH ABLines
        for td in abline_defs:
            ab_el = ET.SubElement(tracks_el, "{urn:schemas-johndeere-com:Setup}ABLine", {
                "CreationDate": td["CreationDate"],
                "SourceNode": td["SourceNode"],
                "LastModifiedDate": td["LastModifiedDate"],
                "Archived": td["Archived"],
                "StringGuid": td["StringGuid"],
                "Name": td["Name"],
                "TaggedEntity": td["TaggedEntity"]
            })

            tram_el = ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}TramLineAttributes")
            ET.SubElement(tram_el, "{urn:schemas-johndeere-com:Setup}TrackOffset").text = "0"
            ET.SubElement(tram_el, "{urn:schemas-johndeere-com:Setup}Spacing").text = "0"

            proj_el = ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}SpatialProjection")
            ET.SubElement(proj_el, "{urn:schemas-johndeere-com:Setup}ProjectionType", {
                "Representation": "dtProjectionType",
                "Value": "dtiProjectionDeere"
            })
            ET.SubElement(proj_el, "{urn:schemas-johndeere-com:Setup}ElevationReferencePoint", {
                "Representation": "vrElevation",
                "Value": "0",
                "SourceUnit": "m"
            })

            ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}APoint", td["APoint"])
            ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}BPoint", td["BPoint"])

            ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}SaveMethod", {
                "Representation": "dtABLineSaveMethod",
                "Value": "dtiABLineMethodAutomaticB"
            })

            ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}Heading", {
                "Representation": "vrABLineHeading",
                "Value": td["Heading"],
                "SourceUnit": "arcdeg"
            })

    # -------------------------------------------------
    # OperationalBoundaries schreiben – NACH Guidance (JD-Reihenfolge!)
    # inkl. SignalType und SetupCore-Versionsmarken wie im Operations-Center-Export
    # -------------------------------------------------
    for bd in boundary_defs:
        boundary_el = ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}OperationalBoundary", {
            "CreationDate": _utc_now_iso(),
            "SourceNode": source_node_guid,
            "LastModifiedDate": _utc_now_iso(),
            "Archived": "false",
            "StringGuid": bd["StringGuid"],
            "TaggedEntity": bd["TaggedEntity"],
            "Name": bd["Name"]
        })

        ET.SubElement(boundary_el, "{urn:schemas-johndeere-com:Setup}SignalType", {
            "Representation": "dtSignalType",
            "Value": "dtiSignalTypeUnknown"
        })

        geometry_el = ET.SubElement(boundary_el, "{urn:schemas-johndeere-com:Setup}Geometry")
        ET.SubElement(geometry_el, "{urn:schemas-johndeere-com:Setup}FilenameWithExtension").text = bd["FilenameWithExtension"]
        ET.SubElement(geometry_el, "{urn:schemas-johndeere-com:Setup}Path").text = "./SpatialFiles/"

        ET.SubElement(boundary_el, "{urn:schemas-johndeere-com:SetupCore}VersionDelimiter").text = "Version 2.54"
        ET.SubElement(boundary_el, "{urn:schemas-johndeere-com:SetupCore}VersionsEnd").text = "End"

    # -------------------------------------------------
    # SourceInformation + CreatedDateTime (Abschluss von <Setup>)
    # -------------------------------------------------
    src_info_el = ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}SourceInformation")
    ET.SubElement(src_info_el, "{urn:schemas-johndeere-com:Setup}DisplayName").text = "GS4_4600"
    ET.SubElement(src_info_el, "{urn:schemas-johndeere-com:Setup}Version").text = "1.0.0.0"
    ET.SubElement(src_info_el, "{urn:schemas-johndeere-com:Setup}SourceApplication").text = "Setup Builder"
    ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}CreatedDateTime").text = _utc_now_iso()

    # -------------------------------------------------
    # XML schreiben
    # -------------------------------------------------
    xml_bytes = ET.tostring(root_xml, encoding="utf-8")
    dom = xml.dom.minidom.parseString(xml_bytes)
    pretty_xml = dom.toprettyxml(indent="  ")

    masterdata_path = os.path.join(gen4_dir, "MasterData.xml")
    with open(masterdata_path, "w", encoding="utf-8") as f:
        f.write(pretty_xml)

    if not exported_any:
        plugin.iface.messageBar().pushMessage(
            "Hinweis",
            "Kein John Deere Gen4 Inhalt exportiert. MasterData.xml wurde trotzdem erzeugt.",
            level=1,
            duration=5
        )
        return False

    plugin.iface.messageBar().pushMessage(
        "Erfolgreich",
        f"John Deere Gen4 Grundexport erstellt: {gen4_dir}",
        level=0,
        duration=5
    )
    return True
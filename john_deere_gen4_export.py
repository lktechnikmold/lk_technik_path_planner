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

    root_xml = ET.Element(
        "{urn:schemas-johndeere-com:Setup}SetupFile",
        {
            "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation":
                "urn:schemas-johndeere-com:Setup"
        }
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
    guidance_el = ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}Guidance")
    tracks_el = ET.SubElement(guidance_el, "{urn:schemas-johndeere-com:Setup}Tracks")

    exported_any = False

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
            if not polygon_layer:
                continue

            ct_poly = _to_wgs84_transform(polygon_layer)

            poly_fmap = _field_map(polygon_layer)
            id_field = _pick_field(poly_fmap, "ID")
            name_field = _pick_field(poly_fmap, "Name")

            field_ids_filter = selected[ctr_name][frm_name]
            field_guid_map = {}

            for field_feature in polygon_layer.getFeatures():
                raw_id = field_feature[id_field] if id_field else field_feature.id()
                try:
                    field_id = int(raw_id)
                except Exception:
                    field_id = int(field_feature.id())

                if (field_ids_filter is not None) and (field_id not in field_ids_filter):
                    continue

                field_name = str(field_feature[name_field]) if name_field else f"Feld_{field_id}"

                geometry = _polygon_feature_to_geojson_geometry(field_feature, ct_poly)
                if geometry is None:
                    continue

                boundary_guid = _new_guid()
                field_guid = _new_guid()
                field_guid_map[field_id] = field_guid

                boundary_filename = f"Boundary{boundary_guid}.gjson"
                boundary_path = os.path.join(spatial_dir, boundary_filename)
                _write_boundary_geojson(boundary_path, geometry)

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

                # Feldgrenze direkt unter <Setup> anlegen und mit dem Feld verknüpfen
                boundary_el = ET.SubElement(setup_el, "{urn:schemas-johndeere-com:Setup}OperationalBoundary", {
                    "CreationDate": "1970-01-01T00:00:00Z",
                    "SourceNode": source_node_guid,
                    "LastModifiedDate": timestamp,
                    "Archived": "false",
                    "StringGuid": boundary_guid,
                    "TaggedEntity": field_guid,
                    "Name": timestamp
                })

                geometry_el = ET.SubElement(boundary_el, "{urn:schemas-johndeere-com:Setup}Geometry")
                fname_el = ET.SubElement(geometry_el, "{urn:schemas-johndeere-com:Setup}FilenameWithExtension")
                fname_el.text = boundary_filename

                path_el = ET.SubElement(geometry_el, "{urn:schemas-johndeere-com:Setup}Path")
                path_el.text = "./SpatialFiles/"

                exported_any = True
            
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

                    # Für den ersten Schritt nur echte AB-Linien:
                    # genau eine Linie mit genau 2 Punkten
                    if len(lines) != 1:
                        continue

                    line = lines[0]
                    if len(line) != 2:
                        continue

                    a_lon, a_lat = _pt_to_lonlat(line[0], ct_line)
                    b_lon, b_lat = _pt_to_lonlat(line[1], ct_line)

                    heading = _heading_from_points(a_lon, a_lat, b_lon, b_lat)
                    track_guid = _new_guid()

                    ab_el = ET.SubElement(tracks_el, "{urn:schemas-johndeere-com:Setup}ABLine", {
                        "CreationDate": timestamp,
                        "SourceNode": source_node_guid,
                        "LastModifiedDate": timestamp,
                        "Archived": "false",
                        "StringGuid": track_guid,
                        "Name": track_name,
                        "TaggedEntity": field_guid_for_track
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

                    ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}APoint", {
                        "Latitude": str(a_lat),
                        "Longitude": str(a_lon),
                        "Slope": "0"
                    })

                    ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}BPoint", {
                        "Latitude": str(b_lat),
                        "Longitude": str(b_lon),
                        "Slope": "0"
                    })

                    ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}SaveMethod", {
                        "Representation": "dtABLineSaveMethod",
                        "Value": "dtiABLineMethodBPoint"
                    })

                    ET.SubElement(ab_el, "{urn:schemas-johndeere-com:Setup}Heading", {
                        "Representation": "vrABLineHeading",
                        "Value": str(heading),
                        "SourceUnit": "arcdeg"
                    })

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
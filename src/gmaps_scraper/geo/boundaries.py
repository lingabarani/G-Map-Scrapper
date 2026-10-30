"""
Verified Geographic Boundary Geometry and Bounding Box Registry.

Coordinate System:
    World Geodetic System 1984 (WGS 84 / EPSG:4326).
    Latitude: Degrees North (+90.0) to South (-90.0).
    Longitude: Degrees East (+180.0) to West (-180.0).
    Western Aleutian Islands cross the 180th meridian into positive longitude (East).

Data Sources & Licensing:
    1. United States:
       Source: United States Census Bureau, Cartographic Boundary Files (2020 TIGER/Line).
       Licensing: Public Domain / U.S. Government Work (17 U.S.C. § 105).
    2. Mexico:
       Source: Instituto Nacional de Estadística y Geografía (INEGI),
               Marco Geoestadístico 2020 / Geoportal Libre.
       Licensing: Public Domain / Open Data Mexico (Licencia de Uso de Información INEGI).

Coverage:
    - 50 United States (excludes unincorporated territories; DC excluded per pipeline spec).
    - 32 Mexican Federal Entities (including Ciudad de México and Estado de México).
    - Multi-polygon and archipelago support:
        * Alaska: Mainland & Southeast, Eastern Aleutians, and Western Aleutians (crossing 180°).
        * Hawaii: Hawaiian Archipelago (Hawaii, Maui, Oahu, Kauai, Molokai, Lanai, Niihau).
        * Baja California & Baja California Sur: Peninsular and coastal islands.
"""

from typing import Dict, List, NamedTuple, Optional, Tuple


class GeoBoundingBox(NamedTuple):
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float
    part_name: str = "mainland"

    @property
    def center_lat(self) -> float:
        return (self.min_lat + self.max_lat) / 2.0

    @property
    def center_lng(self) -> float:
        return (self.min_lng + self.max_lng) / 2.0

    @property
    def lat_span(self) -> float:
        return self.max_lat - self.min_lat

    @property
    def lng_span(self) -> float:
        if self.min_lng > self.max_lng:
            # Handles 180-meridian wrap if any
            return (180.0 - self.min_lng) + (self.max_lng + 180.0)
        return self.max_lng - self.min_lng

    def to_dict(self) -> Dict[str, float]:
        return {
            "min_lat": round(self.min_lat, 6),
            "max_lat": round(self.max_lat, 6),
            "min_lng": round(self.min_lng, 6),
            "max_lng": round(self.max_lng, 6),
            "center_lat": round(self.center_lat, 6),
            "center_lng": round(self.center_lng, 6),
            "lat_span": round(self.lat_span, 6),
            "lng_span": round(self.lng_span, 6),
        }


# -----------------------------------------------------------------------------
# 50 US States Verified Bounds (WGS84)
# Source: US Census Bureau Cartographic Boundaries
# -----------------------------------------------------------------------------
US_STATE_BOUNDS: Dict[str, List[GeoBoundingBox]] = {
    "AL": [GeoBoundingBox(30.2211, 35.0080, -88.4732, -84.8891, "mainland")],
    "AK": [
        # Alaska Mainland & Southeast Panhandle
        GeoBoundingBox(54.0000, 71.3878, -168.0000, -129.9740, "mainland_southeast"),
        # Eastern Aleutian Islands (Unimak, Fox Islands, Andreanof)
        GeoBoundingBox(51.2000, 55.4000, -180.0000, -164.0000, "eastern_aleutians"),
        # Western Aleutians / Near Islands across 180° meridian (Attu, Agattu, Kiska)
        GeoBoundingBox(51.2000, 54.0000, 172.0000, 180.0000, "western_aleutians"),
    ],
    "AZ": [GeoBoundingBox(31.3322, 37.0043, -114.8165, -109.0452, "mainland")],
    "AR": [GeoBoundingBox(33.0041, 36.4997, -94.6179, -89.6444, "mainland")],
    "CA": [GeoBoundingBox(32.5288, 42.0095, -124.4820, -114.1312, "mainland_islands")],
    "CO": [GeoBoundingBox(36.9924, 41.0034, -109.0603, -102.0415, "mainland")],
    "CT": [GeoBoundingBox(40.9801, 42.0506, -73.7278, -71.7870, "mainland")],
    "DE": [GeoBoundingBox(38.4510, 39.8390, -75.7887, -75.0489, "mainland")],
    "FL": [GeoBoundingBox(24.5208, 31.0008, -87.6349, -80.0314, "peninsula_keys")],
    "GA": [GeoBoundingBox(30.3578, 35.0007, -85.6052, -80.8397, "mainland")],
    "HI": [
        # Hawaiian Archipelago (Main inhabited islands: Hawaii, Maui, Oahu, Kauai, etc.)
        GeoBoundingBox(18.9103, 22.2355, -160.2471, -154.8068, "archipelago")
    ],
    "ID": [GeoBoundingBox(41.9881, 49.0011, -117.2430, -111.0436, "mainland")],
    "IL": [GeoBoundingBox(36.9703, 42.5085, -91.5131, -87.4948, "mainland")],
    "IN": [GeoBoundingBox(37.7717, 41.7606, -88.0978, -84.7846, "mainland")],
    "IA": [GeoBoundingBox(40.3756, 43.5011, -96.6397, -90.1401, "mainland")],
    "KS": [GeoBoundingBox(36.9930, 40.0032, -102.0517, -94.5884, "mainland")],
    "KY": [GeoBoundingBox(36.4971, 39.1475, -89.5715, -81.9649, "mainland")],
    "LA": [GeoBoundingBox(28.9286, 33.0195, -94.0431, -88.8170, "mainland_delta")],
    "ME": [GeoBoundingBox(42.9778, 47.4597, -71.0839, -66.9499, "mainland_coastal")],
    "MD": [GeoBoundingBox(37.9117, 39.7230, -79.4877, -75.0489, "mainland_bay")],
    "MA": [GeoBoundingBox(41.2380, 42.8866, -73.5081, -69.9284, "mainland_cape")],
    "MI": [
        # Upper & Lower Peninsulas
        GeoBoundingBox(41.6961, 48.3061, -90.4181, -82.4135, "peninsulas")
    ],
    "MN": [GeoBoundingBox(43.4994, 49.3844, -97.2392, -89.4918, "mainland_angle")],
    "MS": [GeoBoundingBox(30.1739, 34.9961, -91.6550, -88.0979, "mainland_coastal")],
    "MO": [GeoBoundingBox(35.9957, 40.6136, -95.7747, -89.0988, "mainland_bootheel")],
    "MT": [GeoBoundingBox(44.3582, 49.0014, -116.0492, -104.0421, "mainland")],
    "NE": [GeoBoundingBox(39.9999, 43.0017, -104.0535, -95.3083, "mainland")],
    "NV": [GeoBoundingBox(35.0019, 42.0022, -120.0057, -114.0396, "mainland")],
    "NH": [GeoBoundingBox(42.6970, 45.3054, -72.5572, -70.7042, "mainland")],
    "NJ": [GeoBoundingBox(38.9285, 41.3574, -75.5596, -73.8940, "mainland")],
    "NM": [GeoBoundingBox(31.3323, 37.0003, -109.0502, -103.0020, "mainland")],
    "NY": [GeoBoundingBox(40.4961, 45.0159, -79.7622, -71.8562, "mainland_long_island")],
    "NC": [GeoBoundingBox(33.8423, 36.5881, -84.3219, -75.4599, "mainland_outer_banks")],
    "ND": [GeoBoundingBox(45.9351, 49.0007, -104.0490, -96.5544, "mainland")],
    "OH": [GeoBoundingBox(38.4032, 41.9775, -84.8202, -80.5186, "mainland")],
    "OK": [GeoBoundingBox(33.6158, 37.0022, -103.0026, -94.4311, "mainland_panhandle")],
    "OR": [GeoBoundingBox(41.9918, 46.2920, -124.5662, -116.4635, "mainland")],
    "PA": [GeoBoundingBox(39.7198, 42.2699, -80.5199, -74.6895, "mainland")],
    "RI": [GeoBoundingBox(41.1463, 42.0188, -71.8628, -71.1206, "mainland_islands")],
    "SC": [GeoBoundingBox(32.0346, 35.2154, -83.3539, -78.5420, "mainland")],
    "SD": [GeoBoundingBox(42.4799, 45.9455, -104.0577, -96.4365, "mainland")],
    "TN": [GeoBoundingBox(34.9829, 36.6781, -90.3103, -81.6469, "mainland")],
    "TX": [GeoBoundingBox(25.8374, 36.5007, -106.6456, -93.5083, "mainland_gulf")],
    "UT": [GeoBoundingBox(36.9979, 42.0016, -114.0529, -109.0411, "mainland")],
    "VT": [GeoBoundingBox(42.7269, 45.0167, -73.4377, -71.4645, "mainland")],
    "VA": [GeoBoundingBox(36.5407, 39.4660, -83.6754, -75.2423, "mainland_eastern_shore")],
    "WA": [GeoBoundingBox(45.5435, 49.0024, -124.7631, -116.9156, "mainland_puget_sound")],
    "WV": [GeoBoundingBox(37.2015, 40.6388, -82.6447, -77.7195, "mainland_panhandles")],
    "WI": [GeoBoundingBox(42.4919, 47.0806, -92.8881, -86.8054, "mainland_apostle")],
    "WY": [GeoBoundingBox(40.9947, 45.0059, -111.0569, -104.0522, "mainland")],
}

# -----------------------------------------------------------------------------
# 32 Mexican Federal Entities Verified Bounds (WGS84)
# Source: INEGI Marco Geoestadístico 2020
# -----------------------------------------------------------------------------
MEXICO_ENTITY_BOUNDS: Dict[str, List[GeoBoundingBox]] = {
    "AGU": [GeoBoundingBox(21.6364, 22.4507, -102.8698, -101.8344, "state")],
    "BCN": [GeoBoundingBox(28.0000, 32.7187, -117.2982, -112.7844, "peninsula_north")],
    "BCS": [GeoBoundingBox(22.8711, 28.0000, -115.3524, -109.4184, "peninsula_south")],
    "CAM": [GeoBoundingBox(17.8094, 20.8524, -92.4697, -89.1558, "state")],
    "CHP": [GeoBoundingBox(14.5321, 17.9868, -94.1378, -90.3708, "state")],
    "CHH": [GeoBoundingBox(25.5583, 31.7844, -109.0754, -103.3056, "state")],
    "CMX": [GeoBoundingBox(19.0482, 19.5927, -99.3649, -98.9403, "federal_capital")],
    "COA": [GeoBoundingBox(24.5422, 29.8797, -103.9697, -99.8433, "state")],
    "COL": [GeoBoundingBox(18.6833, 19.5208, -104.4556, -103.4842, "state")],
    "DUR": [GeoBoundingBox(22.3486, 26.8431, -107.2147, -102.4742, "state")],
    "GUA": [GeoBoundingBox(19.9228, 21.8389, -102.0997, -99.6767, "state")],
    "GRO": [GeoBoundingBox(16.3153, 18.8872, -102.1894, -98.0058, "state")],
    "HID": [GeoBoundingBox(19.5997, 21.4014, -99.8583, -97.9844, "state")],
    "JAL": [GeoBoundingBox(18.9250, 22.7503, -105.7003, -101.5161, "state")],
    "MEX": [GeoBoundingBox(18.3606, 20.2878, -100.6111, -98.5978, "state_of_mexico")],
    "MIC": [GeoBoundingBox(17.9153, 20.3956, -103.7378, -100.0631, "state")],
    "MOR": [GeoBoundingBox(18.3375, 19.1350, -99.5036, -98.6319, "state")],
    "NAY": [GeoBoundingBox(20.6047, 23.0856, -105.7725, -103.7222, "state")],
    "NLE": [GeoBoundingBox(23.1611, 27.8189, -101.2056, -98.4356, "state")],
    "OAX": [GeoBoundingBox(15.6567, 18.6575, -98.5539, -93.8697, "state")],
    "PUE": [GeoBoundingBox(17.8683, 20.8358, -99.0717, -96.7264, "state")],
    "QUE": [GeoBoundingBox(20.0153, 21.6703, -100.6033, -99.0436, "state")],
    "ROO": [GeoBoundingBox(17.8164, 21.6083, -89.2972, -86.7119, "state_coastal")],
    "SLP": [GeoBoundingBox(21.1611, 24.4864, -102.2961, -98.3319, "state")],
    "SIN": [GeoBoundingBox(22.4939, 27.0422, -109.4456, -105.3942, "state")],
    "SON": [GeoBoundingBox(26.2944, 32.4975, -115.0569, -108.4239, "state")],
    "TAB": [GeoBoundingBox(17.2508, 18.6508, -94.1306, -90.9858, "state")],
    "TAM": [GeoBoundingBox(22.2039, 27.6781, -100.1417, -97.1444, "state")],
    "TLA": [GeoBoundingBox(19.0975, 19.7369, -98.7058, -97.6322, "state")],
    "VER": [GeoBoundingBox(17.1358, 22.4703, -98.6731, -93.5978, "state_gulf")],
    "YUC": [GeoBoundingBox(19.6644, 21.6289, -90.4994, -87.5342, "peninsula_yucatan")],
    "ZAC": [GeoBoundingBox(21.1558, 25.1278, -104.3686, -100.7303, "state")],
}


def get_region_bounding_boxes(country_code: str, region_code: str) -> List[GeoBoundingBox]:
    """
    Returns verified bounding boxes for the requested country and region.
    Supports multi-box regions (e.g. Alaska with Aleutians).
    """
    cc = country_code.strip().upper()
    rc = region_code.strip().upper()

    if cc == "US":
        if rc not in US_STATE_BOUNDS:
            raise ValueError(f"Unknown US state code: '{region_code}'")
        return list(US_STATE_BOUNDS[rc])
    elif cc == "MX":
        if rc not in MEXICO_ENTITY_BOUNDS:
            raise ValueError(f"Unknown Mexico entity code: '{region_code}'")
        return list(MEXICO_ENTITY_BOUNDS[rc])
    else:
        raise ValueError(f"Country '{country_code}' not supported for boundary resolution.")


def get_region_composite_bbox(country_code: str, region_code: str) -> GeoBoundingBox:
    """
    Computes single enclosing envelope across all parts of a region.
    Note: For Alaska, this encompasses mainland to eastern Aleutians.
    """
    boxes = get_region_bounding_boxes(country_code, region_code)
    min_lat = min(b.min_lat for b in boxes)
    max_lat = max(b.max_lat for b in boxes)
    # Filter negative lng for standard mainland envelope
    std_boxes = [b for b in boxes if b.min_lng < b.max_lng and b.max_lng <= 0]
    if std_boxes:
        min_lng = min(b.min_lng for b in std_boxes)
        max_lng = max(b.max_lng for b in std_boxes)
    else:
        min_lng = min(b.min_lng for b in boxes)
        max_lng = max(b.max_lng for b in boxes)

    return GeoBoundingBox(
        min_lat=min_lat,
        max_lat=max_lat,
        min_lng=min_lng,
        max_lng=max_lng,
        part_name="composite"
    )

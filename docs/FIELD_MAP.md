# Google Maps Scraper — Field Mapping Contract (FIELD_MAP.md)

This document specifies the **ground truth verified JSON paths and extraction logic** for all entities in the scraper. Every path below was verified against live network responses captured in `tests/fixtures/`.

---

## 1. Discovery & Search RPC (`search_rpc.json`)

**Endpoint**: `https://www.google.com/search?tbm=map&authuser=0&hl={hl}&gl={gl}&pb={pb}&q={query}`  
**Anti-XSSI Prefix**: `)]}'\n` (stripped before `json.loads`)  
**Results Root**: `response[0][1]`  
- `response[0][1][0]`: Viewport & query metadata  
- `response[0][1][1:]`: Individual place entries, where the place entity `p` is at `entry[14]`

| Field Name | JSON Path in `p` | Extraction / Parsing Logic | Verified Example Value |
|---|---|---|---|
| `name` | `p[11]` | `str(p[11])` | `"787 Coffee"` |
| `address` | `p[18]` | `str(p[18])` (Full formatted address) | `"787 Coffee, 66 Pearl St, New York, NY 10004"` |
| `street_address` | `p[39]` | `str(p[39])` | `"66 Pearl St, New York, NY 10004"` |
| `latitude` | `p[9][2]` | `float(p[9][2])` | `40.703485199999996` |
| `longitude` | `p[9][3]` | `float(p[9][3])` | `-74.01083129999999` |
| `website` | `p[7][0]` | `p[7][0] if p[7] else None` | `"http://www.787coffee.com/"` |
| `phone` | `p[178][0][0]` | `p[178][0][0] if p[178] and p[178][0] else None` | `"(646) 449-9200"` |
| `phone_e164` | `p[178][0][3]` | `p[178][0][3] if p[178] and p[178][0] else None` | `"+16464499200"` |
| `category` | `p[13][0]` | `p[13][0] if p[13] else None` | `"Coffee shop"` |
| `rating` | `p[4][7]` | `float(p[4][7]) if p[4] else None` | `4.8` |
| `review_count` | `p[4][8]` | `int(p[4][8]) if p[4] else 0` | `4144` |
| `ftid` | `p[10]` | `str(p[10])` | `"0x89c25bb26cc76c9f:0xa38e11c309a5d41e"` |
| `place_id` | `p[78]` | `str(p[78])` | `"ChIJn2zHbLJbwokRHtSlCcMRjqM"` |
| `cid_hex` | `p[10].split(':')[1]` | Second hex segment of ftid | `"0xa38e11c309a5d41e"` |
| `cid` (decimal) | `int(p[10].split(':')[1], 16)` | 64-bit uint conversion | `11785376804253324318` |

---

## 2. Place Details RPC (`place_details_rpc.json`)

**Endpoint**: `https://www.google.com/search?tbm=map&authuser=0&hl={hl}&gl={gl}&pb={pb_details}&q={place_name}`  
**Entity Root**: `response[0][1][1][14]`

| Field Name | JSON Path in `p` | Extraction / Parsing Logic | Verified Example Value |
|---|---|---|---|
| `hours` | `p[203][0]` | Array of 7 day objects `[day_name, day_idx, [y, m, d], [[formatted_hours, [open_h, close_h]]]]` | `["Tuesday", 2, [2026, 10, 13], [["7 AM–8 PM", [[7], [20]]]]]` |
| `attributes` | `p[100][1]` | Array of attribute category groups: `[group_id, group_name, items_list]` where item is `[attr_id, attr_name, [status_int, display_list]]` | `["accessibility", "Accessibility", [..., "Wheelchair accessible restroom", 1]]` |
| `secondary_categories` | `p[13]` | `list(p[13])` | `["Coffee shop"]` |
| `photo_thumbnail` | `p[157]` | `str(p[157])` | `"https://lh3.googleusercontent.com/-4C9ZM5Apjhk/AAAAAAAAAAI/AAAAAAAAAAA/..."` |
| `timezone` | `p[30]` | `str(p[30])` | `"America/New_York"` |
| `plus_code` | `p[183]` / `p[82]` | Geographic components list | `["Manhattan", "66 Pearl St", "New York"]` |
| `claimed_status` | `p[67]` | 1 if claimed/verified business profile | `1` |

---

## 3. Reviews RPC (`reviews_rpc.json`)

**Endpoint**: `https://www.google.com/httpservice/web/PrivateLocalSearchUiDataService/GetLocalBoqProxy?msc=gwsrpc&reqpld={encoded_json}`  
**Anti-XSSI Prefix**: `)]}'\n`  
**Container Path**: `response[1][10]`  
- `response[1][10][2]`: Array of individual reviews `rev`  
- `response[1][10][6]`: Next page pagination token (`str`)

| Field Name | JSON Path in `rev` | Extraction / Parsing Logic | Verified Example Value |
|---|---|---|---|
| `review_id` | `rev[5]` | `str(rev[5])` | `"Ci9DQUlRQUNvZENodHljRjlvT25ORmJXbGtjbE5UVnpSMlUyMW9SSGhZT1VsVWFsRRAB"` |
| `author_name` | `rev[3][0]` | `str(rev[3][0])` | `"pit & pon"` |
| `author_avatar_url`| `rev[3][1]` | `str(rev[3][1])` | `"https://lh3.googleusercontent.com/a-/ALV-UjWyMGccB4o4wK8nOXW..."` |
| `author_profile_url`| `rev[3][2]` | `str(rev[3][2])` | `"https://www.google.com/maps/contrib/100356873384583317650/reviews"` |
| `rating` | `rev[1]` | `int(rev[1])` (1 to 5) | `4` |
| `relative_date` | `rev[2][0]` | `str(rev[2][0])` | `"7 months ago"` |
| `timestamp_ms` | `rev[2][2]` | `int(rev[2][2])` | `1772391367100` |
| `text` | `rev[27]` or `rev[28]` | `str(rev[27] or rev[28])` | `"I absolutely love the dulce de leche, it's so good!..."` |
| `language` | `rev[26]` | `str(rev[26])` | `"en"` |
| `owner_response_date` | `rev[4][1]` | `rev[4][1] if rev[4] else None` | `"7 months ago"` |
| `owner_response_text` | `rev[4][2]` | `rev[4][2] if rev[4] else None` | `"Hi Yvonne!<br><br>We're so happy you enjoyed the dulce..."` |
| `reaction_count` | `rev[34]` | `int(rev[34]) if rev[34] is not None else 0` | `1` |
| `review_photos` | `rev[14]` | List of photo items: `[photo[0] for photo in rev[14]]` | `["https://lh3.googleusercontent.com/grass-cs/AABkmLfK4vsf..."]` |
| `pagination_token` | `response[1][10][6]` | Used in subsequent request `reqpld[1][9][19]` | `"CjEIARIpCgoAP7_LAEDv____EhD-q9rP2bfP24vB4zcAAAAAGgn92PkCZFCe..."` |

---

## 4. Full Place Page HTML Fallback (`place_page.html`)

**Source**: Place URL HTML document  
**JavaScript Target**: `window.APP_INITIALIZATION_STATE = [...]`  
**Parsing Scanner**: Balanced-bracket scanner counting matching `[` and `{`, accounting for escape sequences and quoted strings.  

| Field Name | Extraction Path | Example Value |
|---|---|---|
| `state_length` | `len(state)` | `14` |
| `place_name` | `state[5][3][2][1]` | `"787 Coffee"` |
| `ftid` | `state[5][3][2][0]` | `"0x89c25bb26cc76c9f:0xa38e11c309a5d41e"` |
| `coordinates` | `state[5][3][2][7]` | `[None, None, 40.7034852, -74.0108313]` |
| `unpacked_rpc_blob` | `state[3][5]` (details) / `state[3][1]` (search) | Starts with `)]}'\n`, unpacks via `json.loads` to match RPC schema above |

---

## 5. Verification Fixtures Manifest

All paths above are tested and confirmed against the following static fixtures stored in `tests/fixtures/`:

1. `tests/fixtures/search_rpc.json` (116,514 bytes) — Small-area coffee discovery tile.
2. `tests/fixtures/place_details_rpc.json` (125,310 bytes) — Rich place record (787 Coffee).
3. `tests/fixtures/reviews_rpc.json` (56,647 bytes) — Paginated reviews batch with photos and owner replies.
4. `tests/fixtures/place_page.html` (207,955 bytes) — Complete HTML containing `window.APP_INITIALIZATION_STATE`.

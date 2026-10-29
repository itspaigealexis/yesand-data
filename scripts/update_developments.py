#!/usr/bin/env python3
"""Publish an auditable JSON snapshot of Ottawa's public development applications.

No external Python packages, secret keys, or AI interpretation are used.
A failed/incomplete fetch exits nonzero, so the last good GitHub Pages site remains live.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LAYER = "https://maps.ottawa.ca/arcgis/rest/services/Development_Applications/MapServer/0"
QUERY = LAYER + "/query"
SEARCH_URL = "https://devapps.ottawa.ca/en/"
FIELDS = ",".join([
    "OBJECTID", "DT_APP_ID", "APPLICATION_NUMBER", "ADDRESS_NUMBER_ROAD_NAME",
    "WARD_NUMBER_EN", "OBJECT_CURRENT_STATUS_EN", "OBJECT_CURRENT_STATUS_DATE",
    "APPLICATION_TYPE_EN"
])
CHUNK_SIZE = 200
USER_AGENT = "YESAND-Civic-Info-Pilot/1.0 (public-source research)"


def arcgis_query(params: dict[str, str], attempts: int = 3) -> dict:
    # POST avoids very long query URLs when asking for 200 object IDs.
    body = urlencode({"f": "json", **params}).encode("utf-8")
    for attempt in range(attempts):
        request = Request(
            QUERY,
            data=body,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                result = json.load(response)
            if not isinstance(result, dict) or "error" in result:
                raise ValueError(f"ArcGIS service returned an error: {str(result)[:500]}")
            return result
        except (URLError, HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError("City of Ottawa data request failed; refusing to replace a good dataset") from exc
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def safe_text(value, default: str) -> str:
    return str(value).strip() if value is not None and str(value).strip() else default


def get_object_ids() -> list[int]:
    response = arcgis_query({"where": "1=1", "returnIdsOnly": "true"})
    raw = response.get("objectIds")
    if not isinstance(raw, list):
        raise ValueError("ArcGIS did not return objectIds; refusing to publish partial records")
    ids = sorted({int(x) for x in raw})
    # This source should contain far more than a handful of applications.
    if len(ids) < 25 or len(ids) > 200_000:
        raise ValueError(f"Unusually small or large inventory ({len(ids)}), refusing to publish")
    return ids


def transform(attributes: dict) -> dict:
    id_value = attributes.get("OBJECTID")
    if id_value is None:
        raise ValueError("A City record is missing its OBJECTID")
    status_date = attributes.get("OBJECT_CURRENT_STATUS_DATE")
    if status_date is not None:
        status_date = float(status_date)
    return {
        "objectID": int(id_value),
        "applicationNumber": safe_text(
            attributes.get("APPLICATION_NUMBER") or attributes.get("DT_APP_ID"), "Not listed"
        ),
        "address": safe_text(attributes.get("ADDRESS_NUMBER_ROAD_NAME"), "Address not provided"),
        "wardLabel": safe_text(attributes.get("WARD_NUMBER_EN"), "Ward not provided"),
        "status": safe_text(attributes.get("OBJECT_CURRENT_STATUS_EN"), "Status not provided"),
        "type": safe_text(attributes.get("APPLICATION_TYPE_EN"), "Development application"),
        "statusDateMilliseconds": status_date,
    }


def fetch_all() -> list[dict]:
    ids = get_object_ids()
    print(f"City source reported {len(ids)} development application records", flush=True)
    records: dict[int, dict] = {}
    for start in range(0, len(ids), CHUNK_SIZE):
        batch = ids[start:start + CHUNK_SIZE]
        response = arcgis_query({
            "objectIds": ",".join(map(str, batch)),
            "outFields": FIELDS,
            "returnGeometry": "false",
        })
        features = response.get("features")
        if not isinstance(features, list):
            raise ValueError("ArcGIS returned no feature list; keeping last published dataset")
        for feature in features:
            if not isinstance(feature, dict) or not isinstance(feature.get("attributes"), dict):
                raise ValueError("Malformed City feature encountered")
            item = transform(feature["attributes"])
            records[item["objectID"]] = item
        print(f"Fetched {min(start + CHUNK_SIZE, len(ids))}/{len(ids)} IDs", flush=True)

    # The source can change while fetching, but it's safer to fail than publish
    # an incomplete snapshot that makes applications seem to disappear.
    if set(records) != set(ids):
        missing = len(set(ids) - set(records))
        unexpected = len(set(records) - set(ids))
        raise ValueError(f"Incomplete snapshot: {missing} missing, {unexpected} unexpected IDs")
    return sorted(records.values(), key=lambda r: (-(r["statusDateMilliseconds"] or 0), r["objectID"]))


def build_snapshot(applications: list[dict], checked: dt.datetime | None = None) -> dict:
    checked = checked or dt.datetime.now(dt.timezone.utc)
    return {
        "schemaVersion": 1,
        "generatedAt": checked.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sourceName": "City of Ottawa — Development Applications",
        "sourceURL": LAYER,
        "applicationSearchURL": SEARCH_URL,
        "licenseURL": "https://ottawa.ca/en/city-hall/open-transparent-and-accountable-government/open-data/open-data-licence-version-20",
        "attribution": "Source: City of Ottawa development applications map service. Verify applicable reuse terms before public release.",
        "recordCount": len(applications),
        "applications": applications,
    }


def write_atomic(destination: Path, payload: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=destination.parent, delete=False, encoding="utf-8") as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        handle.write("\n")
    os.replace(temporary, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="site/development-applications.json")
    args = parser.parse_args()
    applications = fetch_all()
    payload = build_snapshot(applications)
    write_atomic(Path(args.output), payload)
    print(f"Validated and saved {len(applications)} records to {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"YES AND updater FAILED: {error}", file=sys.stderr, flush=True)
        sys.exit(1)

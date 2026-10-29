import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import update_developments as updater

class Tests(unittest.TestCase):
    def test_transform_uses_explicit_city_field_names(self):
        result = updater.transform({
            "OBJECTID": 42, "APPLICATION_NUMBER": "D01-01-0001",
            "ADDRESS_NUMBER_ROAD_NAME": "123 Example Street",
            "WARD_NUMBER_EN": "14", "APPLICATION_TYPE_EN": "Site plan",
            "OBJECT_CURRENT_STATUS_EN": "Under review",
            "OBJECT_CURRENT_STATUS_DATE": 1793188800000,
        })
        self.assertEqual(result["objectID"], 42)
        self.assertEqual(result["wardLabel"], "14")
        self.assertEqual(result["statusDateMilliseconds"], 1793188800000)

    def test_snapshot_shape_matches_swift_model(self):
        ids = list(range(1, 31))
        def fake_query(args):
            if args.get('returnIdsOnly') == 'true':
                return {'objectIds': ids}
            requested = [int(x) for x in args['objectIds'].split(',')]
            return { 'features': [ {'attributes': {
                'OBJECTID': i,
                'APPLICATION_NUMBER': f'D01-{i}',
                'WARD_NUMBER_EN': 'Ward 13',
                'OBJECT_CURRENT_STATUS_DATE': 1793188800000.0,
            }} for i in requested] }
        with patch.object(updater, 'arcgis_query', side_effect=fake_query):
            applications = updater.fetch_all()
        self.assertEqual(len(applications), 30)
        checked=dt.datetime(2026,10,28,20,0,tzinfo=dt.timezone.utc)
        snapshot = updater.build_snapshot(applications,checked)
        self.assertEqual(snapshot['schemaVersion'], 1)
        self.assertEqual(snapshot['recordCount'], 30)
        self.assertEqual(snapshot['generatedAt'], '2026-10-28T20:00:00Z')
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'development-applications.json'
            updater.write_atomic(dest,snapshot)
            import json
            loaded=json.loads(dest.read_text())
            self.assertEqual(len(loaded['applications']), 30)

    def test_missing_records_block_partial_publication(self):
        ids = list(range(1, 31))
        def fake_query(args):
            if args.get('returnIdsOnly') == 'true':
                return {'objectIds': ids}
            return {'features': [{'attributes': {'OBJECTID': i}} for i in range(1, 29)]}
        with patch.object(updater, 'arcgis_query', side_effect=fake_query):
            with self.assertRaisesRegex(ValueError, 'Incomplete snapshot'):
                updater.fetch_all()

if __name__=='__main__':
    unittest.main()

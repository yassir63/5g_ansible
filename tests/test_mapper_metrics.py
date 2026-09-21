import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from jinja2 import Environment
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families
from redis.exceptions import ConnectionError
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'monitoring/sliceawareness/ue_mapper'))
sys.path.insert(0, str(ROOT / 'probes/ue_mapper_metrics'))
from mapper_metrics import MapperMetrics


class FakeRedis:
    def __init__(self, records=None, pages=None):
        self.records = records or {}
        self.pages = pages

    def ping(self):
        return True

    def scan(self, cursor, match, count):
        if self.pages:
            return self.pages[cursor]
        return 0, [key for key in self.records if key.startswith('ran:')]

    def pipeline(self, transaction=False):
        owner = self
        class Pipeline:
            def __init__(self):
                self.keys = []
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def hgetall(self, key):
                self.keys.append(key)
            def execute(self):
                return [owner.records.get(key, {}) for key in self.keys]
        return Pipeline()


def samples(collector):
    registry = CollectorRegistry()
    registry.register(collector)
    output = generate_latest(registry).decode()
    values = {(sample.name, tuple(sorted(sample.labels.items()))): sample.value
              for family in text_string_to_metric_families(output) for sample in family.samples}
    return values, output


class MapperTests(unittest.TestCase):
    def test_empty_success(self):
        values, _ = samples(MapperMetrics(FakeRedis()))
        self.assertEqual(values['ue_mapper_context_records', ()], 0)
        self.assertEqual(values['ue_mapper_collection_success', ()], 1)

    def test_enrichment_deduplication_and_missing_fields(self):
        fake = FakeRedis({
            'ran:1': {'ul_teid': 'a', 'dl_teid': 'b', 'sst': '1', 'sd': 'ffffff'},
            'ran:2': {'ul_teid': 'a', 'sst': '1', 'sd': 'ffffff'},
            'teid:0000000a': {'imsi': '001234', 'ue_ip': '12.1.1.1'},
        }, pages={0: (1, ['ran:1']), 1: (0, ['ran:1', 'ran:2'])})
        values, output = samples(MapperMetrics(fake))
        label = (('slice', '01:ffffff'),)
        self.assertEqual(values['ue_mapper_context_records', ()], 2)
        self.assertEqual(values['ue_mapper_identified_ues_by_slice', label], 1)
        self.assertEqual(values['ue_mapper_paired_contexts_by_slice', label], 1)
        self.assertEqual(values['ue_mapper_context_missing_fields', (('field', 'dl_teid'), *label)], 1)
        self.assertEqual(values['ue_mapper_context_missing_fields', (('field', 'dl_teid_record'), *label)], 1)
        self.assertNotIn('001234', output)
        self.assertNotIn('12.1.1.1', output)

    def test_unknown_slice_not_defaulted(self):
        values, _ = samples(MapperMetrics(FakeRedis({'ran:1': {'sst': 'unknown'}})))
        self.assertEqual(values['ue_mapper_context_records_by_slice', (('slice', 'unknown'),)], 1)

    def test_redis_failure_is_not_empty_inventory(self):
        fake = FakeRedis()
        with patch.object(fake, 'ping', side_effect=ConnectionError('unavailable')):
            values, _ = samples(MapperMetrics(fake))
        self.assertEqual(values['ue_mapper_redis_up', ()], 0)
        self.assertEqual(values['ue_mapper_collection_success', ()], 0)
        self.assertNotIn(('ue_mapper_context_records', ()), values)

    def test_limits_omit_partial_counts(self):
        for kwargs in ({'max_records': 1}, {'max_scan_calls': 0}, {'budget_seconds': 0}):
            values, _ = samples(MapperMetrics(FakeRedis({'ran:1': {}, 'ran:2': {}}), **kwargs))
            self.assertEqual(values['ue_mapper_collection_success', ()], 0)
            self.assertNotIn(('ue_mapper_context_records', ()), values)

    def test_at_limit_complete_scan_is_valid(self):
        values, _ = samples(MapperMetrics(FakeRedis({'ran:1': {'imsi': '001'}}), max_records=1))
        self.assertEqual(values['ue_mapper_collection_success', ()], 1)

    def test_disappeared_slice_is_not_retained(self):
        fake = FakeRedis({'ran:1': {'imsi': '001', 'sst': '1', 'sd': 'ffffff'}})
        collector = MapperMetrics(fake)
        self.assertIn('slice="01:ffffff"', samples(collector)[1])
        fake.records.clear()
        self.assertNotIn('slice="01:ffffff"', samples(collector)[1])

    def test_concurrent_scrape_does_not_start_another_scan(self):
        collector = MapperMetrics(FakeRedis())
        with collector.lock:
            values, _ = samples(collector)
        self.assertEqual(values['ue_mapper_collection_busy', ()], 1)
        self.assertNotIn(('ue_mapper_context_records', ()), values)

    def test_endpoint_and_existing_inventory(self):
        api = importlib.import_module('ue_mapper_api')
        registry = CollectorRegistry()
        registry.register(MapperMetrics(FakeRedis()))
        with patch.object(api, 'metrics_registry', registry), patch.object(api, 'build_inventory_rows', return_value=[]):
            client = api.app.test_client()
            response = client.get('/metrics')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content_type.startswith('text/plain'))
            self.assertIn(b'ue_mapper_collection_success 1.0', response.data)
            self.assertEqual(client.get('/inventory/ues?limit=17').json, {'count': 0, 'limit': 17, 'ues': []})

    def test_scrape_gate_defaults_off_and_service_only(self):
        env = Environment()
        env.filters['bool'] = bool
        template = env.from_string((ROOT / 'roles/monitoring/sliceawareness/templates/ue-mapper.yaml.j2').read_text())
        for enabled in (False, True):
            options = {'sliceawareness_namespace': 'monitoring', 'ue_mapper_api_image': 'test'}
            if enabled:
                options['ue_mapper_metrics_enabled'] = True
            deployment, service = list(yaml.safe_load_all(template.render(**options)))
            self.assertEqual(deployment['spec']['template']['metadata']['annotations']['prometheus.io/scrape'], 'false')
            self.assertEqual(service['metadata']['annotations']['prometheus.io/scrape'], str(enabled).lower())
            self.assertEqual(service['metadata']['annotations']['prometheus.io/path'], '/metrics')

    def test_queries_have_unique_names_and_known_ran_sources(self):
        queries = json.loads((ROOT / 'configs/artifacts/default_prometheus_queries.json').read_text())
        self.assertEqual(len(queries), len({query['name'] for query in queries}))
        dashboard = (ROOT / 'roles/monitoring/prometheus_grafana/files/monitoring-dashboard-srsran.json').read_text()
        for query in queries:
            if query['name'].startswith('srsran_'):
                self.assertIn(query['query'], dashboard)


if __name__ == '__main__':
    unittest.main()

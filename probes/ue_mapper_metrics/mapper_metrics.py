"""Low-cardinality metrics for stored mapper context, not network ground truth."""

from collections import defaultdict
import re
import threading
import time

from prometheus_client.core import GaugeMetricFamily
from redis.exceptions import RedisError


def present(value):
    return str(value or '').strip().lower() not in ('', 'unknown', 'none', 'null')


def hex_field(value, width):
    if not present(value):
        return ''
    value = str(value).strip().lower().removeprefix('0x').replace(':', '')
    if not re.fullmatch(r'[0-9a-f]{1,%d}' % width, value):
        return ''
    return value.zfill(width)


def first_present(records, field):
    return next((record[field] for record in records if present(record.get(field))), '')


class MapperMetrics:
    def __init__(self, redis_client, max_records=5000, max_scan_calls=100, budget_seconds=3):
        self.redis = redis_client
        self.max_records = max_records
        self.max_scan_calls = max_scan_calls
        self.budget_seconds = budget_seconds
        self.lock = threading.Lock()

    def describe(self):
        return []

    def snapshot(self):
        deadline = time.monotonic() + self.budget_seconds
        seen = set()
        groups = defaultdict(lambda: {'records': 0, 'ues': set(), 'pairs': 0,
                                     'missing': defaultdict(int)})
        cursor = 0
        for _ in range(self.max_scan_calls):
            cursor, keys = self.redis.scan(cursor=cursor, match='ran:*', count=100)
            keys = list(dict.fromkeys(key for key in keys if key not in seen))
            if len(seen) + len(keys) > self.max_records or time.monotonic() >= deadline:
                return None
            seen.update(keys)
            if keys:
                with self.redis.pipeline(transaction=False) as pipe:
                    for key in keys:
                        pipe.hgetall(key)
                    records = pipe.execute()
                teids = {hex_field(record.get(field), 8) for record in records
                         for field in ('ul_teid', 'dl_teid')}
                teids.discard('')
                teid_keys = sorted(teids)
                with self.redis.pipeline(transaction=False) as pipe:
                    for teid in teid_keys:
                        pipe.hgetall('teid:' + teid)
                    hashes = dict(zip(teid_keys, pipe.execute()))
                for record in records:
                    if not record:
                        continue
                    ul = hex_field(record.get('ul_teid'), 8)
                    dl = hex_field(record.get('dl_teid'), 8)
                    ul_record, dl_record = hashes.get(ul, {}), hashes.get(dl, {})
                    sources = (record, ul_record, dl_record)
                    sst = hex_field(first_present(sources, 'sst'), 2)
                    sd = hex_field(first_present(sources, 'sd'), 6)
                    sid = sst + ':' + sd if sst and sd else 'unknown'
                    group = groups[sid]
                    group['records'] += 1
                    group['pairs'] += int(bool(ul and dl))
                    imsi = first_present((ul_record, dl_record, record), 'imsi')
                    if imsi:
                        group['ues'].add(imsi)
                    missing = {'imsi': not imsi, 'ue_ip': not first_present(sources, 'ue_ip'),
                               'slice': sid == 'unknown', 'ul_teid': not ul, 'dl_teid': not dl,
                               'ul_teid_record': bool(ul) and not ul_record,
                               'dl_teid_record': bool(dl) and not dl_record}
                    for field, absent in missing.items():
                        group['missing'][field] += int(absent)
            if time.monotonic() >= deadline:
                return None
            if int(cursor) == 0:
                return groups
        return None

    def collect(self):
        started = time.monotonic()
        groups = None
        redis_up = 0
        acquired = self.lock.acquire(blocking=False)
        try:
            if acquired:
                self.redis.ping()
                redis_up = 1
                groups = self.snapshot()
        except RedisError:
            redis_up = 0
        finally:
            if acquired:
                self.lock.release()
        yield GaugeMetricFamily('ue_mapper_collection_success',
                                'One when the bounded context scan completed; not mapping freshness.',
                                value=int(groups is not None))
        yield GaugeMetricFamily('ue_mapper_collection_busy',
                                'One when another metrics collection was already running.', value=int(not acquired))
        yield GaugeMetricFamily('ue_mapper_redis_up',
                                'Redis operations succeeded during this collection; zero also when busy.', value=redis_up)
        yield GaugeMetricFamily('ue_mapper_collection_duration_seconds',
                                'Time spent collecting mapper metrics.', value=time.monotonic() - started)
        if groups is None:
            return
        yield GaugeMetricFamily('ue_mapper_context_records',
                                'Total stored ran:* records; not registered UEs or PDU sessions.',
                                value=sum(group['records'] for group in groups.values()))
        counts = GaugeMetricFamily('ue_mapper_context_records_by_slice',
                                   'Stored ran:* records grouped by observed slice.', labels=['slice'])
        ues = GaugeMetricFamily('ue_mapper_identified_ues_by_slice',
                                'Distinct IMSIs in stored contexts per slice; not verified active UEs.', labels=['slice'])
        pairs = GaugeMetricFamily('ue_mapper_paired_contexts_by_slice',
                                  'Stored contexts with UL and DL TEID values.', labels=['slice'])
        missing = GaugeMetricFamily('ue_mapper_context_missing_fields',
                                    'Stored contexts missing a field or referenced TEID hash; not network failures.',
                                    labels=['slice', 'field'])
        for sid, group in sorted(groups.items()):
            counts.add_metric([sid], group['records'])
            ues.add_metric([sid], len(group['ues']))
            pairs.add_metric([sid], group['pairs'])
            for field, count in sorted(group['missing'].items()):
                missing.add_metric([sid, field], count)
        yield counts
        yield ues
        yield pairs
        yield missing

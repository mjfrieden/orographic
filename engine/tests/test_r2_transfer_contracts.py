"""Independent synthetic review: actual boto3/s3transfer, no external requests."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import tracemalloc
from unittest import mock

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import pytest

from engine.orographic import evidence_store as evidence
from scripts import r2_multipart as multipart
from scripts import upload_research_artifacts_to_r2 as upload
from scripts import restore_research_artifacts_from_r2 as restore

MIB = 1024 * 1024


def sdk():
    return boto3.client('s3', endpoint_url='https://example.invalid', region_name='auto',
                        aws_access_key_id='synthetic-only', aws_secret_access_key='synthetic-only',
                        config=Config(request_checksum_calculation='when_required',
                                      response_checksum_validation='when_required'))


class InterceptedS3:
    """Consume SDK-generated bounded file bodies without network or whole-body buffering."""
    def __init__(self, source, *, fail=None, concurrent=False):
        self.source = source
        self.fail = fail
        self.lock = threading.Lock()
        self.parts = {}
        self.calls = []
        self.active = self.maximum = 0
        self.barrier = threading.Barrier(2) if concurrent else None

    def __call__(self, operation, params):
        with self.lock:
            self.calls.append((operation, {k: v for k, v in params.items() if k != 'Body'}))
        if operation == 'CreateMultipartUpload':
            return {'UploadId': 'synthetic-session'}
        if operation == 'AbortMultipartUpload':
            assert params['UploadId'] == 'synthetic-session'
            return {}
        if operation == 'CompleteMultipartUpload':
            if self.fail == 'complete':
                raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'synthetic'}}, operation)
            numbers = [x['PartNumber'] for x in params['MultipartUpload']['Parts']]
            assert numbers == sorted(self.parts)
            return {'ETag': 'synthetic-whole-object'}
        assert operation == 'UploadPart', operation
        number = params['PartNumber']
        body = params['Body']
        assert not isinstance(body, (bytes, bytearray))
        assert type(body).__name__ == 'ReadFileChunk'
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        try:
            if self.barrier and number <= 2:
                self.barrier.wait(timeout=10)
            if self.fail == 'part' and number == 1:
                raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'synthetic'}}, operation)
            digest = hashlib.sha256()
            count = 0
            while chunk := body.read(64 * 1024):
                digest.update(chunk)
                count += len(chunk)
            self.parts[number] = (count, digest.hexdigest())
            return {'ETag': f'synthetic-etag-{number}'}
        finally:
            with self.lock:
                self.active -= 1


def sparse(path, size):
    with path.open('wb') as f:
        f.truncate(size)
    return path


def test_sdk_431mib_exact_parts_bounded_heap_and_two_workers(tmp_path):
    source = sparse(tmp_path / 'source', 431 * MIB)
    with source.open('r+b') as f:
        for offset, value in [(0,b'FIRST'), (64*MIB-4,b'BOUNDARY'), (430*MIB,b'LAST')]:
            f.seek(offset)
            f.write(value)
    client = sdk()
    service = InterceptedS3(source, concurrent=True)
    tracemalloc.start()
    try:
        with mock.patch.object(multipart, 'large_object_client', return_value=client), \
             mock.patch.object(client, '_make_api_call', side_effect=service):
            multipart.upload_large_object('bucket', 'immutable/key', source)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert service.maximum == 2
    assert len(service.parts) == 7
    assert sum(length for length, _ in service.parts.values()) == source.stat().st_size
    assert peak < 16 * MIB, peak
    with source.open('rb') as f:
        for number in sorted(service.parts):
            expected = hashlib.sha256()
            left = service.parts[number][0]
            while left:
                chunk = f.read(min(64*1024, left))
                expected.update(chunk)
                left -= len(chunk)
            assert expected.hexdigest() == service.parts[number][1]
        assert f.read(1) == b''
    assert [op for op, _ in service.calls].count('CompleteMultipartUpload') == 1
    assert not any(op == 'AbortMultipartUpload' for op, _ in service.calls)
    print(f'431 MiB actual managed transfer: {len(service.parts)} parts, max workers {service.maximum}, Python heap peak {peak} bytes')


@pytest.mark.parametrize('failure', ['part', 'complete'])
def test_sdk_concurrent_failures_abort_session_and_raise(tmp_path, failure):
    source = sparse(tmp_path / 'source', 11 * MIB)
    client = sdk()
    service = InterceptedS3(source, fail=failure, concurrent=True)
    with mock.patch.object(multipart, 'PART_BYTES', 5*MIB), \
         mock.patch.object(multipart, 'large_object_client', return_value=client), \
         mock.patch.object(client, '_make_api_call', side_effect=service):
        with pytest.raises(Exception, match='AccessDenied'):
            multipart.upload_large_object('bucket', 'immutable/key', source)
    assert [op for op, _ in service.calls].count('AbortMultipartUpload') == 1
    if failure == 'part':
        assert not any(op == 'CompleteMultipartUpload' for op, _ in service.calls)
    assert service.active == 0


def make_bundle(path):
    source = path / 'source.json'
    source.write_text(json.dumps({'entries': [{'run_generated_at_utc':'2026-01-01T00:00:00Z',
        'picks': [{'recommendation_id':'synthetic', 'provenance':{'text':'snow ☃'}, 'outcomes':{}}]}]}))
    root = path / 'bundle'
    manifest = evidence.build_canonical_evidence_bundle(source_roots=[],
        current_prospective_ledger=source, current_moonshot_ledger=None,
        payoff_evidence=None, output_dir=root)
    return root, manifest


def test_compact_bundle_exact_hashes_roundtrip_and_preserves_source(tmp_path):
    root, manifest = make_bundle(tmp_path)
    source = (tmp_path/'source.json').read_bytes()
    store = {}
    def put(bucket, key, path):
        store[key] = path.read_bytes()
    def get(bucket, key, path, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(store[key])
    with mock.patch.object(upload, '_put_object', side_effect=put):
        upload._upload_canonical(bucket='bucket', prefix='current', bundle=root)
    publish = json.loads(store['current/evidence_manifest.json'])
    for record in publish['files']:
        raw = store[record['object_key']]
        assert len(raw) == record['bytes']
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        assert raw == (root/record['path']).read_bytes()
    restored = tmp_path/'restored'
    with mock.patch.object(restore, '_list_objects', return_value=[{'key':'current/evidence_manifest.json'}]), \
         mock.patch.object(restore, '_get_object', side_effect=get):
        assert restore.restore_prefix(bucket='bucket', account_id='synthetic', api_token='synthetic',
                                      prefix='current', output_dir=restored) == 5
    assert evidence.validate_canonical_bundle(restored)['bundle_id'] == manifest['bundle_id']
    assert (tmp_path/'source.json').read_bytes() == source


def test_mutation_after_bundle_validation_must_not_publish_bad_hash(tmp_path):
    root, manifest = make_bundle(tmp_path)
    store = {}
    original_validate = upload.validate_canonical_bundle
    def validate_then_race(bundle):
        result = original_validate(bundle)
        path = root/result['files'][0]['path']
        path.write_bytes(path.read_bytes().replace(b'synthetic', b'corrupted'))  # same length
        return result
    def put(bucket, key, path):
        store[key] = path.read_bytes()
    with mock.patch.object(upload, 'validate_canonical_bundle', side_effect=validate_then_race), \
         mock.patch.object(upload, '_put_object', side_effect=put):
        try:
            upload._upload_canonical(bucket='bucket', prefix='current', bundle=root)
        except (ValueError, RuntimeError):
            pass
    if 'current/evidence_manifest.json' in store:
        published = json.loads(store['current/evidence_manifest.json'])
        assert all(hashlib.sha256(store[r['object_key']]).hexdigest() == r['sha256'] for r in published['files']), \
            'Published current manifest referencing bytes changed after initial validation'


@pytest.mark.parametrize('bad_account', ['', '../example.com', 'a'*31, 'g'*32, 'a'*32+'.evil.test'])
def test_bad_account_fails_without_client_or_ambient_credentials(bad_account):
    env = {'CLOUDFLARE_ACCOUNT_ID':bad_account, 'R2_ACCESS_KEY_ID':'synthetic',
           'R2_SECRET_ACCESS_KEY':'synthetic', 'AWS_ACCESS_KEY_ID':'ambient-do-not-use',
           'AWS_SECRET_ACCESS_KEY':'ambient-do-not-use', 'CLOUDFLARE_API_TOKEN':'do-not-derive'}
    with mock.patch.dict(os.environ, env, clear=True), mock.patch('boto3.client') as client:
        with pytest.raises(RuntimeError):
            multipart.large_object_client()
        client.assert_not_called()


def test_concurrent_canonical_publishers_leave_one_complete_consistent_generation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    one,_=make_bundle(a)
    two,_=make_bundle(b)
    manifest=json.loads((two/'evidence_manifest.json').read_bytes())
    manifest['generated_at_utc']='2026-10-22T21:00:00Z'
    (two/'evidence_manifest.json').write_text(json.dumps(manifest))
    store={}
    lock=threading.Lock()
    barrier=threading.Barrier(2)
    def put(bucket,key,path):
        raw=path.read_bytes()
        if key.endswith('/prospective_pick_ledger.json'):
            barrier.wait(timeout=10)
        with lock:
            if key in store and key != 'current/evidence_manifest.json':
                assert store[key] == raw
            store[key]=raw
    with mock.patch.object(upload,'_put_object',side_effect=put):
        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs=[executor.submit(upload._upload_canonical,bucket='synthetic',prefix='current',bundle=bundle)
                  for bundle in (one,two)]
            for job in jobs:
                job.result()
    published=json.loads(store['current/evidence_manifest.json'])
    generations=set()
    for record in published['files']:
        raw=store[record['object_key']]
        assert hashlib.sha256(raw).hexdigest()==record['sha256']
        generations.add(record['object_key'].split('/')[1])
    assert len(generations)==1
    assert len(store)==9  # two intact immutable generations and one pointer


def test_source_mutates_during_transfer_but_private_snapshot_remains_exact(tmp_path):
    root,manifest=make_bundle(tmp_path)
    store={}
    def put(bucket,key,path):
        for record in manifest['files']:
            original=root/record['path']
            original.write_bytes(b'concurrent local rewrite')
        assert root not in path.parents
        store[key]=path.read_bytes()
    with mock.patch.object(upload,'_put_object',side_effect=put):
        upload._upload_canonical(bucket='synthetic',prefix='current',bundle=root)
    published=json.loads(store['current/evidence_manifest.json'])
    assert all(hashlib.sha256(store[r['object_key']]).hexdigest()==r['sha256'] for r in published['files'])


def test_real_botocore_retry_rewinds_identical_part_bytes_without_network(tmp_path):
    from botocore.awsrequest import AWSResponse
    from urllib.parse import urlparse,parse_qs
    source=sparse(tmp_path/'source',6*MIB)
    with source.open('r+b') as handle:
        handle.write(b'SYNTHETIC RETRY DATA')
    client=sdk()
    class RawResponse:
        def __init__(self,data): self.data=data
        def stream(self,*args,**kwargs): yield self.data
    part_digests=[]
    requests=[]
    def send(request):
        parsed=urlparse(request.url)
        query=parse_qs(parsed.query,keep_blank_values=True)
        requests.append((request.method,query))
        if 'uploads' in query:
            data=b'<InitiateMultipartUploadResult><Bucket>bucket</Bucket><Key>key</Key><UploadId>synthetic-retry</UploadId></InitiateMultipartUploadResult>'
            status=200; headers={}
        elif request.method=='PUT' and 'partNumber' in query:
            digest=hashlib.sha256()
            while chunk:=request.body.read(65536): digest.update(chunk)
            number=int(query['partNumber'][0])
            part_digests.append((number,digest.hexdigest()))
            if number==1 and sum(n==1 for n,_ in part_digests)==1:
                status=503; headers={}
                data=b'<Error><Code>SlowDown</Code><Message>synthetic retry</Message></Error>'
            else:
                status=200;headers={'ETag':f'"synthetic-{number}"'};data=b''
        elif request.method=='POST' and 'uploadId' in query:
            status=200;headers={}
            data=b'<CompleteMultipartUploadResult><Bucket>bucket</Bucket><Key>key</Key><ETag>"done"</ETag></CompleteMultipartUploadResult>'
        else:
            raise AssertionError((request.method,query))
        return AWSResponse(request.url,status,headers,RawResponse(data))
    with mock.patch.object(multipart,'PART_BYTES',5*MIB), \
         mock.patch.object(multipart,'MAX_CONCURRENCY',1), \
         mock.patch.object(multipart,'large_object_client',return_value=client), \
         mock.patch.object(client._endpoint.http_session,'send',side_effect=send), \
         mock.patch('botocore.endpoint.time.sleep'):
        multipart.upload_large_object('bucket','key',source)
    first=[digest for n,digest in part_digests if n==1]
    assert len(first)==2 and first[0]==first[1]
    assert len(part_digests)==3
    assert requests[-1][0]=='POST'

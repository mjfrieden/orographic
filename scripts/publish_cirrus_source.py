"""Publish an immutable Actions source pointer on a fast-forward-only data branch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import pyarrow.parquet as pq
import pyarrow.compute as pc
from datetime import datetime, timezone

BRANCH='data/cirrus-source'
REPO='mjfrieden/Orographic'


def source_record(canonical,run_id,artifact_id,head_sha,digest=None):
    path=Path(canonical)/'evidence_manifest.json'
    manifest=json.loads(path.read_text())
    for record in manifest['files']:
        artifact=path.parent/record['path']
        if not artifact.resolve().is_relative_to(path.parent.resolve()):raise ValueError('Unsafe source path')
        if hashlib.sha256(artifact.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Source hash mismatch')
    quotes=path.parent/'live_option_quotes.parquet'
    record=next(r for r in manifest['files'] if r['path']==quotes.name)
    if pq.read_metadata(quotes).num_rows!=record['rows']:raise ValueError('Source row count mismatch')
    end=pc.max(pq.read_table(quotes,columns=['quote_date']).column('quote_date')).as_py()
    if end is None or not 0<=(datetime.now(timezone.utc).date()-end).days<=7:raise ValueError('Source stale or future-dated')
    return dict(repository=REPO,run_id=int(run_id),artifact_id=int(artifact_id),head_sha=head_sha,
        artifact_name='orographic-live-research-data',artifact_digest=digest,
        bundle_id=manifest['bundle_id'],manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        quote_sha256=record['sha256'],quote_rows=record['rows'],source_end=end.isoformat())


def publish(record):
    token=os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
    if not token:token=subprocess.check_output(['gh','auth','token'],text=True).strip()
    def api(path,body=None,method=None):
        data=json.dumps(body).encode() if body is not None else None
        request=urllib.request.Request(f'https://api.github.com/repos/{REPO}/'+path,data=data,
            method=method,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'orographic-source-pointer'})
        with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
    try:
        previous=api('git/ref/heads/'+BRANCH)['object']['sha']
    except urllib.error.HTTPError as error:
        if error.code!=404:raise
        previous=None
    content=json.dumps(record,indent=2)+'\n'
    blob=api('git/blobs',{'content':content,'encoding':'utf-8'})['sha']
    tree=api('git/trees',{'tree':[{'path':'source.json','mode':'100644','type':'blob','sha':blob}]})['sha']
    commit=api('git/commits',{'message':'Publish source for scan '+str(record['run_id']),
                           'tree':tree,'parents':[previous] if previous else []})['sha']
    if previous:api('git/refs/heads/'+BRANCH,{'sha':commit,'force':False},method='PATCH')
    else:api('git/refs',{'ref':'refs/heads/'+BRANCH,'sha':commit})
    print('Published source pointer for run '+str(record['run_id']))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--canonical',type=Path,required=True)
    p.add_argument('--run-id',required=True);p.add_argument('--artifact-id',required=True)
    p.add_argument('--head-sha',required=True);p.add_argument('--digest')
    a=p.parse_args();publish(source_record(a.canonical,a.run_id,a.artifact_id,a.head_sha,a.digest))

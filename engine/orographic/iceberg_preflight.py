"""Bounded, read-only REST/Avro/S3 existence checks for the R2 Iceberg catalog.

No data-file GETs, credential discovery, retries, redirects or write methods.
Only Iceberg v1/v2 client-side planning with vended S3 credentials is supported.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class Blocked(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    metadata_bytes: int = 32 * 1024 * 1024
    document_bytes: int = 4 * 1024 * 1024
    manifests: int = 128
    objects: int = 2048
    requests: int = 4096
    entries: int = 100000
    seconds: int = 120
    request_seconds: int = 5


class Budget:
    def __init__(self, limits=Limits(), clock=time.monotonic):
        self.limits, self.clock, self.started = limits, clock, clock()
        self.used = dict(metadata_bytes=0, manifests=0, objects=0, requests=0, entries=0)

    def check(self):
        if self.clock() - self.started >= self.limits.seconds:
            raise Blocked("limit_seconds")

    def take(self, key, amount=1):
        self.check()
        if self.used[key] + amount > getattr(self.limits, key):
            raise Blocked("limit_" + key)
        self.used[key] += amount


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Blocked("redirect_not_allowed")


class Transport:
    def __init__(self, budget):
        self.budget = budget
        self.opener = build_opener(NoRedirect())

    def request(self, method, url, headers):
        if method not in ("GET", "HEAD"):
            raise Blocked("write_not_allowed")
        self.budget.take("requests")
        timeout = min(self.budget.limits.request_seconds,
                      self.budget.limits.seconds - (self.budget.clock() - self.budget.started))
        try:
            response = self.opener.open(Request(url, headers=headers, method=method), timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.status
            if method == "HEAD":
                return status, dict(response.headers), b""
            cap = min(self.budget.limits.document_bytes,
                      self.budget.limits.metadata_bytes - self.budget.used["metadata_bytes"])
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > cap:
                raise Blocked("limit_document_bytes")
            data = bytearray()
            while True:
                self.budget.check()
                if len(data) >= cap:
                    raise Blocked("limit_document_bytes")
                chunk = response.read(min(65536, cap - len(data)))
                if not chunk:
                    break
                self.budget.take("metadata_bytes", len(chunk))
                data.extend(chunk)
                if len(data) > cap:
                    raise Blocked("limit_document_bytes")
            return status, dict(response.headers), bytes(data)


class R2Reader:
    def __init__(self, uri, warehouse, token, budget, transport=None):
        parsed = urlsplit(uri)
        parts = parsed.path.strip("/").split("/")
        if (parsed.scheme != "https" or parsed.netloc != "catalog.cloudflarestorage.com"
                or len(parts) != 2 or not re.fullmatch(r"[a-f0-9]{32}", parts[0])
                or parsed.query or parsed.fragment):
            raise Blocked("unsupported_catalog_endpoint")
        self.uri, self.token, self.budget = uri.rstrip("/"), token, budget
        self.account, self.bucket = parts
        self.transport = transport or Transport(budget)
        config = self._json(self.uri + "/v1/config?" + urlencode({"warehouse": warehouse}))
        self.config = {**config.get("defaults", {}), **config.get("overrides", {})}
        prefix = self.config.get("prefix", "")
        self.base = self.uri + "/v1/" + (quote(prefix, safe="") + "/" if prefix else "")

    def _json(self, url, absent=False):
        status, _, raw = self.transport.request("GET", url, {
            "Authorization": "Bearer " + self.token,
            "X-Iceberg-Access-Delegation": "vended-credentials", "Accept": "application/json",
        })
        payload = json.loads(raw)
        if absent and status == 404 and payload.get("error", {}).get("type") == "NoSuchTableException":
            return None
        if status != 200:
            raise Blocked("catalog_http_" + str(status))
        return payload

    def load(self, namespace, name):
        return self._json(self.base + "namespaces/" + quote(namespace, safe="")
                          + "/tables/" + quote(name, safe=""), absent=True)

    def object(self, location, table, *, metadata=False):
        from botocore.auth import S3SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials

        parsed = urlsplit(location)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket or parsed.query or parsed.fragment:
            raise Blocked("unsupported_object_location")
        if metadata and not parsed.path.endswith((".json", ".avro")):
            raise Blocked("unsupported_metadata_object")
        props = {**self.config, **table.get("config", {})}
        candidates = [c for c in table.get("storage-credentials", [])
                      if location.startswith(c.get("prefix", "\0"))]
        if candidates:
            props.update(max(candidates, key=lambda c: len(c["prefix"]))["config"])
        if props.get("s3.remote-signing-enabled") == "true" or props.get("scan-planning-mode") == "server":
            raise Blocked("unsupported_access_mode")
        endpoint = "https://" + self.account + ".r2.cloudflarestorage.com"
        if props.get("s3.endpoint", endpoint).rstrip("/") != endpoint:
            raise Blocked("unsupported_s3_endpoint")
        if not props.get("s3.access-key-id") or not props.get("s3.secret-access-key"):
            raise Blocked("missing_vended_credentials")
        credentials = Credentials(props["s3.access-key-id"], props["s3.secret-access-key"], props.get("s3.session-token"))
        url = endpoint + "/" + quote(self.bucket, safe="") + quote(parsed.path, safe="/")
        method = "GET" if metadata else "HEAD"
        request = AWSRequest(method=method, url=url)
        S3SigV4Auth(credentials, "s3", props.get("s3.region", "auto")).add_auth(request)
        status, headers, raw = self.transport.request(method, url, dict(request.headers))
        if status != 200:
            raise Blocked("object_http_" + str(status))
        return raw if metadata else int(next(v for k, v in headers.items() if k.lower() == "content-length"))


def identity(table):
    if table is None:
        return None
    metadata = table["metadata"]
    return (table["metadata-location"], metadata["table-uuid"], metadata.get("current-snapshot-id"))


def inspect_tables(plan, reader, budget):
    """Check current-snapshot live entries, including position/equality deletes."""
    from fastavro import reader as avro_reader

    report = {
        "status": "preflight_passed", "mode": "bounded_object_existence",
        "rebuilt_mart_id": plan["mart_id"], "published_mart_id": None, "served_mart_id": None,
        "remote_writes_started": False, "publication_verified": False, "served_verified": False,
        "limits": asdict(budget.limits), "tables": [], "usage": budget.used,
    }
    loaded = []
    try:
        for name in [*[t["name"] for t in plan["tables"]], "mart_publications"]:
            item = {"target": f"{plan['catalog_name']}.{plan['namespace']}.{name}"}
            report["tables"].append(item)
            table = reader.load(plan["namespace"], name)
            loaded.append((name, identity(table)))
            if table is None:
                item["status"] = "absent"
                continue
            meta = table["metadata"]
            if meta.get("format-version") not in (1, 2) or table.get("read-restrictions"):
                raise Blocked("unsupported_table_semantics")
            item.update(snapshot_id=meta.get("current-snapshot-id"), table_uuid=meta["table-uuid"],
                        metadata_identity=hashlib.sha256(table["metadata-location"].encode()).hexdigest())
            # Verify metadata object is readable and matches the REST metadata.
            raw = reader.object(table["metadata-location"], table, metadata=True)
            if json.loads(raw) != meta:
                raise Blocked("metadata_mismatch")
            snapshots = [s for s in meta.get("snapshots", []) if s["snapshot-id"] == meta.get("current-snapshot-id")]
            if meta.get("current-snapshot-id") in (None, -1):
                if snapshots:
                    raise Blocked("invalid_empty_snapshot")
                item["status"] = "empty"
                continue
            if len(snapshots) != 1 or not snapshots[0].get("manifest-list"):
                raise Blocked("unsupported_snapshot_layout")
            manifest_bytes = reader.object(snapshots[0]["manifest-list"], table, metadata=True)
            objects = {}
            for manifest in avro_reader(io.BytesIO(manifest_bytes)):
                budget.take("manifests")
                if manifest.get("content", 0) not in (0, 1):
                    raise Blocked("unsupported_manifest_content")
                raw = reader.object(manifest["manifest_path"], table, metadata=True)
                if len(raw) != manifest["manifest_length"]:
                    raise Blocked("manifest_size_mismatch")
                for entry in avro_reader(io.BytesIO(raw)):
                    budget.take("entries")
                    if entry["status"] == 2:  # removed from this snapshot, not a live file
                        continue
                    if entry["status"] not in (0, 1):
                        raise Blocked("unsupported_entry_status")
                    data = entry["data_file"]
                    content = data.get("content", 0)
                    if content not in (0, 1, 2):
                        raise Blocked("unsupported_file_content")
                    if (content != 0) != (manifest.get("content", 0) == 1):
                        raise Blocked("manifest_content_mismatch")
                    path, size = data["file_path"], data["file_size_in_bytes"]
                    if size < 0 or (path in objects and objects[path] != (size, content)):
                        raise Blocked("conflicting_object_reference")
                    if path not in objects:
                        budget.take("objects")
                        objects[path] = (size, content)
            item["data_objects"] = sum(c == 0 for _, c in objects.values())
            item["delete_objects"] = sum(c != 0 for _, c in objects.values())
            for path, (size, content) in sorted(objects.items()):
                item["checking_object_sha256"] = hashlib.sha256(path.encode()).hexdigest()
                item["checking_content"] = content
                if reader.object(path, table) != size:
                    raise Blocked("object_size_mismatch")
            item.pop("checking_object_sha256", None)
            item.pop("checking_content", None)
            item["status"] = "objects_present"
        # Catch changes during inspection; a later race remains possible.
        for name, initial in loaded:
            if identity(reader.load(plan["namespace"], name)) != initial:
                raise Blocked("catalog_changed")
    except Exception as exc:
        report["status"] = "blocked"
        report["reason"] = str(exc) if isinstance(exc, Blocked) else "invalid_or_unreadable_metadata"
    return report


def bounded_preflight(plan, *, catalog_uri, warehouse, token):
    budget = Budget()
    try:
        reader = R2Reader(catalog_uri, warehouse, token, budget)
        return inspect_tables(plan, reader, budget)
    except Exception as exc:
        return {"status": "blocked", "reason": str(exc) if isinstance(exc, Blocked) else "catalog_access_failed",
                "rebuilt_mart_id": plan["mart_id"], "published_mart_id": None, "served_mart_id": None,
                "remote_writes_started": False, "publication_verified": False, "served_verified": False,
                "usage": budget.used, "limits": asdict(budget.limits)}

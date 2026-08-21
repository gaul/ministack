#!/usr/bin/env python3
"""Measure DeleteObject If-Match behaviour against an S3 endpoint.

Run it against live S3 and against ministack and diff the two tables:

    python3 scripts/probe_conditional_delete.py --region eu-west-1
    python3 scripts/probe_conditional_delete.py --endpoint-url http://localhost:4566

Every case prints the HTTP status and the error code S3 answered with, so a
disagreement names itself rather than showing up as a failed assertion.
"""
import argparse
import uuid

import boto3
from botocore.exceptions import ClientError


def outcome(fn):
    """The (status, error code) an S3 call answered with, however it ended."""
    try:
        resp = fn()
        return resp["ResponseMetadata"]["HTTPStatusCode"], ""
    except ClientError as exc:
        meta = exc.response["ResponseMetadata"]
        return meta["HTTPStatusCode"], exc.response["Error"]["Code"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-url")
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--bucket-prefix", default="ministack-cond-delete")
    args = parser.parse_args()

    s3 = boto3.client("s3", endpoint_url=args.endpoint_url,
                      region_name=args.region)
    suffix = uuid.uuid4().hex[:12]
    plain = f"{args.bucket_prefix}-plain-{suffix}"
    versioned = f"{args.bucket_prefix}-versioned-{suffix}"
    created = []
    rows = []

    def make(bucket, versioning=False):
        kwargs = {"Bucket": bucket}
        if args.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {
                "LocationConstraint": args.region}
        s3.create_bucket(**kwargs)
        created.append(bucket)
        if versioning:
            s3.put_bucket_versioning(
                Bucket=bucket,
                VersioningConfiguration={"Status": "Enabled"})

    def probe(label, bucket, key, if_match):
        status, code = outcome(
            lambda: s3.delete_object(Bucket=bucket, Key=key, IfMatch=if_match))
        rows.append((label, if_match, status, code))

    try:
        make(plain)

        # A key that was never written, in a bucket without versioning.
        for cond in ("*", "badetag"):
            probe("unversioned / never written", plain, "absent", cond)

        # A key that is there: the compare-and-swap cases.
        etag = s3.put_object(Bucket=plain, Key="live", Body=b"x")["ETag"]
        probe("unversioned / present", plain, "live", "badetag")
        probe("unversioned / present", plain, "live", "*")
        s3.put_object(Bucket=plain, Key="live2", Body=b"x")
        probe("unversioned / present", plain, "live2", etag)

        make(versioned, versioning=True)

        # Never written, versioning on.
        for cond in ("*", "badetag"):
            probe("versioned / never written", versioned, "absent", cond)

        # A delete marker sitting over a version that still exists.  This is
        # the case the PR turns on: does S3 evaluate the condition against the
        # marker (no current object) or against the version underneath it?
        live_etag = s3.put_object(
            Bucket=versioned, Key="marked", Body=b"x")["ETag"]
        s3.delete_object(Bucket=versioned, Key="marked")
        probe("versioned / marker over a real version",
              versioned, "marked", "badetag")
        probe("versioned / marker over a real version",
              versioned, "marked", live_etag)
        probe("versioned / marker over a real version",
              versioned, "marked", "*")

        # Nothing but delete markers: what a delete of an absent key leaves.
        s3.delete_object(Bucket=versioned, Key="markers-only")
        probe("versioned / markers only", versioned, "markers-only", "badetag")
        probe("versioned / markers only", versioned, "markers-only", "*")
    finally:
        width = max(len(r[0]) for r in rows) if rows else 0
        print(f"{'case'.ljust(width)}  {'If-Match':<34} status  error")
        for label, cond, status, code in rows:
            print(f"{label.ljust(width)}  {cond:<34} {status:<7} {code}")

        for bucket in created:
            paginator = s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket):
                for entry in (page.get("Versions", []) +
                              page.get("DeleteMarkers", [])):
                    s3.delete_object(Bucket=bucket, Key=entry["Key"],
                                     VersionId=entry["VersionId"])
            for page in s3.get_paginator("list_objects_v2").paginate(
                    Bucket=bucket):
                for entry in page.get("Contents", []):
                    s3.delete_object(Bucket=bucket, Key=entry["Key"])
            s3.delete_bucket(Bucket=bucket)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Measure whether DeleteObject honours x-amz-if-match-size and
x-amz-if-match-last-modified-time on a general purpose bucket.

The API reference marks both "only supported for directory buckets", so the
question is whether S3 rejects them, enforces them, or ignores them there.
An ignored condition is the dangerous answer: the delete goes through, and
the caller believes it was guarded.

    python3 scripts/probe_delete_size_mtime.py --region eu-west-1
    python3 scripts/probe_delete_size_mtime.py --endpoint-url http://localhost:4566
"""
import argparse
import datetime
import uuid

import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-url")
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--bucket-prefix", default="ministack-cond-delete")
    args = parser.parse_args()

    s3 = boto3.client("s3", endpoint_url=args.endpoint_url,
                      region_name=args.region)
    bucket = f"{args.bucket_prefix}-sizemtime-{uuid.uuid4().hex[:12]}"
    rows = []

    kwargs = {"Bucket": bucket}
    if args.region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {
            "LocationConstraint": args.region}
    s3.create_bucket(**kwargs)

    def probe(label, **conditions):
        """Write a fresh object, try the conditional delete, then report both
        what S3 answered and whether the object actually survived."""
        key = f"obj-{uuid.uuid4().hex[:8]}"
        s3.put_object(Bucket=bucket, Key=key, Body=b"hello")
        try:
            resp = s3.delete_object(Bucket=bucket, Key=key, **conditions)
            status, code = resp["ResponseMetadata"]["HTTPStatusCode"], ""
        except ClientError as exc:
            status = exc.response["ResponseMetadata"]["HTTPStatusCode"]
            code = exc.response["Error"]["Code"]
        try:
            s3.head_object(Bucket=bucket, Key=key)
            survived = "yes"
        except ClientError:
            survived = "no"
        rows.append((label, status, code, survived))
        if survived == "yes":
            s3.delete_object(Bucket=bucket, Key=key)

    try:
        # The object is 5 bytes; name a size that is not 5.
        probe("if-match-size, wrong (99)", IfMatchSize=99)
        probe("if-match-size, right (5)", IfMatchSize=5)

        wrong_time = datetime.datetime(2001, 1, 1,
                                       tzinfo=datetime.timezone.utc)
        probe("if-match-mtime, wrong (2001)",
              IfMatchLastModifiedTime=wrong_time)

        # A matching mtime needs the object's own value, so write it here.
        key = f"obj-{uuid.uuid4().hex[:8]}"
        s3.put_object(Bucket=bucket, Key=key, Body=b"hello")
        mtime = s3.head_object(Bucket=bucket, Key=key)["LastModified"]
        try:
            resp = s3.delete_object(Bucket=bucket, Key=key,
                                    IfMatchLastModifiedTime=mtime)
            status, code = resp["ResponseMetadata"]["HTTPStatusCode"], ""
        except ClientError as exc:
            status = exc.response["ResponseMetadata"]["HTTPStatusCode"]
            code = exc.response["Error"]["Code"]
        try:
            s3.head_object(Bucket=bucket, Key=key)
            survived = "yes"
        except ClientError:
            survived = "no"
        rows.append(("if-match-mtime, right", status, code, survived))

        # Both wrong, alongside a correct ETag: does the ETag alone decide?
        key = f"obj-{uuid.uuid4().hex[:8]}"
        etag = s3.put_object(Bucket=bucket, Key=key, Body=b"hello")["ETag"]
        try:
            resp = s3.delete_object(Bucket=bucket, Key=key, IfMatch=etag,
                                    IfMatchSize=99)
            status, code = resp["ResponseMetadata"]["HTTPStatusCode"], ""
        except ClientError as exc:
            status = exc.response["ResponseMetadata"]["HTTPStatusCode"]
            code = exc.response["Error"]["Code"]
        try:
            s3.head_object(Bucket=bucket, Key=key)
            survived = "yes"
        except ClientError:
            survived = "no"
        rows.append(("right etag + wrong size", status, code, survived))
    finally:
        width = max((len(r[0]) for r in rows), default=0)
        print(f"{'case'.ljust(width)}  status  error                 object survived")
        for label, status, code, survived in rows:
            print(f"{label.ljust(width)}  {status:<7} {code:<21} {survived}")

        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=bucket):
            for entry in page.get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=entry["Key"])
        s3.delete_bucket(Bucket=bucket)


if __name__ == "__main__":
    main()

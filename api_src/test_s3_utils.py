import asyncio
import time
import unittest

import boto3
import pytest
from moto import mock_aws


def test_list_s3_subdirectories_returns_top_level_directories():
    # function scoped imports needed to stop s3 from exploding

    with mock_aws():
        from api_src import s3_utils

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="example-bucket")
        s3.put_object(
            Bucket="example-bucket", Key="my_cohort/resource-a/file1.parquet", Body=b"1"
        )
        s3.put_object(
            Bucket="example-bucket", Key="my_cohort/resource-b/file2.parquet", Body=b"2"
        )
        s3.put_object(Bucket="example-bucket", Key="README.txt", Body=b"3")

        result = s3_utils.list_s3_subdirectories("example-bucket", "my_cohort")

        assert sorted(result) == ["resource-a", "resource-b"]


def test_should_calculate_total_size_of_objects():

    with mock_aws():
        from api_src import s3_utils

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="example-bucket")
        s3.put_object(Bucket="example-bucket", Key="prefix/file1.parquet", Body=b"abc")
        s3.put_object(
            Bucket="example-bucket", Key="prefix/file2.parquet", Body=b"defgh"
        )

        total_size = s3_utils.calculate_object_size_bytes("example-bucket", "prefix/")

        assert total_size == 8


@pytest.mark.asyncio
async def test_should_download_s3_objects_to_local_dir(tmp_path):
    cache_dir = tmp_path / "cache"
    with mock_aws():
        from api_src import s3_utils

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="example-bucket")
        s3.put_object(Bucket="example-bucket", Key="prefix/file1.parquet", Body=b"abc")
        s3.put_object(
            Bucket="example-bucket", Key="prefix/file2.parquet", Body=b"defgh"
        )

        await s3_utils.download_s3_parquets(
            "example-bucket",
            "prefix/",
            cache_dir.with_suffix(s3_utils.STAGING_SUFFIX),
            cache_dir,
        )

        assert (cache_dir / "prefix/file1.parquet").read_bytes() == b"abc"
        assert (cache_dir / "prefix/file2.parquet").read_bytes() == b"defgh"


def with_delay(real_fn, delay):
    def wrapper(*args, **kwargs):
        print("delaying 1 sec")
        time.sleep(delay)
        return real_fn(*args, **kwargs)

    return wrapper


@pytest.mark.asyncio
async def test_should_only_download_once(tmp_path, monkeypatch):
    cache_dir = tmp_path
    with mock_aws():
        from api_src import s3_utils

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="example-bucket")
        s3.put_object(Bucket="example-bucket", Key="prefix/file1.parquet", Body=b"abc")
        with unittest.mock.patch.object(
            s3_utils.s3,
            "download_file",
            side_effect=with_delay(s3_utils.s3.download_file, 1),
        ) as mock_s3_download:
            monkeypatch.setattr(s3_utils.env, "local_root", cache_dir)
            first_call = asyncio.create_task(
                asyncio.to_thread(
                    s3_utils.prepare_local_data_dir, "example-bucket", "prefix/"
                )
            )
            await asyncio.sleep(0.5)
            second_call = asyncio.create_task(
                asyncio.to_thread(
                    s3_utils.prepare_local_data_dir, "example-bucket", "prefix/"
                )
            )
            first_success = await first_call
            second_success = await second_call

            assert first_success
            assert second_success
            assert (cache_dir / "prefix/prefix/file1.parquet").read_bytes() == b"abc"
            # Assert that we only download the file once. This shows that the second call is waiting
            # for the cache to run, rather than returning prematurely
            assert mock_s3_download.call_count == 1

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

    with mock_aws():
        from api_src import s3_utils

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="example-bucket")
        s3.put_object(Bucket="example-bucket", Key="prefix/file1.parquet", Body=b"abc")
        s3.put_object(
            Bucket="example-bucket", Key="prefix/file2.parquet", Body=b"defgh"
        )

        await s3_utils.download_s3_parquets("example-bucket", "prefix/", tmp_path)
        print(tmp_path)

        assert (tmp_path / "prefix/file1.parquet").read_bytes() == b"abc"
        assert (tmp_path / "prefix/file2.parquet").read_bytes() == b"defgh"

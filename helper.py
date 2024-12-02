import boto3
import botocore

ENDPOINT_URL = "http://minio:9000"
S3_PROJECT = "project-name.example.se"
S3_ACCESS = "minio"
S3_SECRET = "minioPassword"


def s3connect():

    # Start s3 session
    session = boto3.session.Session()

    # Connect to S3
    resource = session.resource(
        service_name="s3",
        endpoint_url=(ENDPOINT_URL),
        aws_access_key_id=(S3_ACCESS),
        aws_secret_access_key=(S3_SECRET),
    )
    return resource

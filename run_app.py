# installed
from flask import Flask, jsonify, request
from flask_swagger_ui import get_swaggerui_blueprint
import flask
import pandas as pd
import boto3
import botocore
import clickhouse_connect

# own
from helper import s3connect

app = Flask(__name__)

##### Data Ingestion ########


@app.route("/bucket", methods=["POST"])
def create_bucket():
    """
    Create a new S3 bucket.
    This endpoint expects a JSON payload with a "bucket_name" key. If the bucket
    name is not provided, it returns a 400 error. If the bucket already exists,
    it returns a 400 error. Otherwise, it creates the bucket and returns a 201
    status with a success message.

    Returns:
        Response: JSON response indicating success or failure.x
    """

    from botocore.client import ClientError

    bucket_name = flask.request.json.get("bucket_name")
    if not bucket_name:
        return jsonify({"error": "Missing bucket_name"}), 400

    resource = s3connect()

    # Check if bucket exists
    try:
        resource.meta.client.head_bucket(Bucket=bucket_name)
    except ClientError:
        resource.create_bucket(Bucket=bucket_name)
        return jsonify({"message": "Bucket created successfully"}), 201
    else:
        return jsonify({"error": "Bucket already exists"}), 400


@app.route("/ingest/sales", methods=["POST"])
def ingest_sales():
    """
    Handles the ingestion of sales data by uploading CSV file(s) to an S3 bucket.
    This function expects a 'bucket_name' parameter in the request arguments and a file
    in the request files. The file must be a CSV file. The function uploads the file to
    the specified S3 bucket.
    Returns:
        Response: A JSON response indicating the success or failure of the upload operation.
        - On success: {"message": "File(s) uploaded successfully"}, HTTP status code 201.
        - On failure: {"error": "Missing bucket_name"}, HTTP status code 400.
        - On failure: {"error": "S3 upload of file failed: <error_message>"}, HTTP status code 500.
    Raises:
        botocore.client.ClientError: If there is an error during the S3 upload.
        boto3.exceptions.Boto3Error: If there is a Boto3-specific error.
        botocore.exceptions.BotoCoreError: If there is a BotoCore-specific error.
    """
    bucket_name = flask.request.args.get("bucket_name")
    if not bucket_name:
        return jsonify({"error": "Missing bucket_name"}), 400

    file = flask.request.files.getlist("file")[0]
    if file and file.filename.endswith(".csv"):

        resource = s3connect()
        try:
            # Upload file
            resource.meta.client.upload_fileobj(
                file,
                bucket_name,
                file.filename,
                ExtraArgs={
                    "ACL": "private",
                    "ContentType": file.content_type,  # Set appropriate content type as per the file
                },
            )
        except (
            botocore.client.ClientError,
            boto3.exceptions.Boto3Error,
            botocore.exceptions.BotoCoreError,
        ) as err:
            error = f"S3 upload of file failed: {err}"
            return jsonify({"error": error}), 500
        return jsonify({"message": "File(s) uploaded successfully"}), 201
    else:
        return jsonify({"error": "Invalid file format. Please upload a CSV file"}), 400


### Data Transformation ###


@app.route("/transform/sales", methods=["POST"])
def transform_sales():
    """
    Transforms sales data from a CSV file stored in an S3 bucket, processes it, 
    and stores the results in a ClickHouse database.

    The function performs the following steps:
    1. Retrieves the S3 bucket name and file name from the request arguments.
    2. Downloads the CSV file from the specified S3 bucket.
    3. Loads the CSV data into a pandas DataFrame.
    4. Validates the columns of the CSV file.
    5. Ensures that 'quantity' and 'price' columns are numeric and handles missing data.
    6. Calculates the 'total_sales' for each row.
    7. Groups the data by 'product_id' and calculates metrics such as total sales sum, 
       average quantity, total quantity, and number of sales.
    8. Creates a table in ClickHouse if it does not exist and inserts the processed data.
    9. Returns the grouped metrics as a JSON response.

    Returns:
        A JSON response containing the grouped metrics or an error message with the 
        appropriate HTTP status code.
    """

    bucket_name = flask.request.args.get("bucket_name")
    file_name = flask.request.args.get("file_name")
    if not bucket_name:
        return jsonify({"error": "Missing bucket_name"}), 400

    resource = s3connect()
    try:
        # Download file
        resource.meta.client.download_file(bucket_name, file_name, "/tmp/sales.csv")
    except (
        botocore.client.ClientError,
        boto3.exceptions.Boto3Error,
        botocore.exceptions.BotoCoreError,
    ) as err:
        error = f"S3 download of file failed: {err}"
        return jsonify({"error": error}), 500

    # Load the data
    df = pd.read_csv("/tmp/sales.csv")

    # check columns
    if set(df.columns) != set(["sale_id", "product_id", "quantity", "price", "date"]):
        return jsonify({"error": "Invalid columns in the CSV file"}), 400

    # Ensure that quantity and price are numeric and that missing data is handled
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    # Drop rows with missing values in essential columns
    df.dropna(subset=["quantity", "price"], inplace=True)

    # Calculate the 'total_sales' per row (quantity * price)
    df["total_sales"] = df["quantity"] * df["price"]

    # Group by 'product_id' and calculate the metrics
    product_metrics = (
        df.groupby("product_id")
        .agg(
            total_sales_sum=("total_sales", "sum"),
            average_quantity=("quantity", "mean"),
            total_quantity=("quantity", "sum"),
            number_of_sales=("sale_id", "nunique"),
        )
        .reset_index()
    )

    # store in clickhouse
    client = clickhouse_connect.get_client(host='clickhouse', username='default', password='')
    client.command(
        "CREATE TABLE IF NOT EXISTS sales (product_id UInt32, total_sales_sum Float64, average_quantity Float64, total_quantity UInt32, number_of_sales UInt32) ENGINE MergeTree ORDER BY product_id"
    )
    client.insert_df(df=product_metrics, table="sales")

    # Return the grouped metrics as JSON
    return jsonify(product_metrics.to_dict(orient="records")), 200


## HELPER ENDPONTS ##


@app.route("/buckets", methods=["GET"])
def list_buckets():
    """
    List all S3 buckets.
    Returns:
        Response: A JSON response containing a list of all S3 buckets.
    """
    resource = s3connect()
    buckets = resource.buckets.all()

    return jsonify([bucket.name for bucket in buckets]), 200


@app.route("/objects", methods=["GET"])
def list_objects():
    """
    List all objects in an S3 bucket.
    Returns:
        Response: A JSON response containing a list of all objects in the specified S3 bucket.
    """
    bucket_name = flask.request.args.get("bucket_name")
    if not bucket_name:
        return jsonify({"error": "Missing bucket_name"}), 400

    resource = s3connect()
    bucket = resource.Bucket(bucket_name)
    objects = [obj.key for obj in bucket.objects.all()]

    return jsonify(objects), 200

# Swagger documentation route
@app.route('/swagger')
def get_swagger():
    swag = swagger(app)
    swag['info']['version'] = "1.0"
    swag['info']['title'] = "My API"
    return jsonify(swag)

# Swagger UI route
SWAGGER_URL = '/swagger-ui'
API_URL = '/static/swagger.yaml'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
                config={
                    "app_name": "API Documentation",
                    "defaultModelsExpandDepth": -1,
                    "layout": "BaseLayout",
                },
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)


if __name__ == "__main__":
    app.run(host="0.0.0.0")

import json
import boto3
import base64
import time
import io
from datetime import datetime, timedelta
from botocore.exceptions import ClientError
import subprocess
import tempfile
import cv2
from decimal import Decimal
import random
from urllib.parse import quote


# Initialize AWS clients
rekognition = boto3.client('rekognition')
kinesis_video = boto3.client('kinesisvideo')
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

# Constants
VISITORS_TABLE = 'visitors'
PASSCODES_TABLE = 'passcodes'
STREAM_NAME = "smart-door-video-stream-live"
BUCKET_NAME = 'my-smartdoor-visitor-images'
FACE_COLLECTION_ID = 'smartdoor-collection'
webpageLink = 'https://smartdoor-frontend-bucket.s3.us-east-1.amazonaws.com/index.html'

#Application UI
otp_site = "https://smartdoor-frontend-bucket.s3.us-east-1.amazonaws.com/virtualdoor.html"
registration_site = "https://smartdoor-frontend-bucket.s3.us-east-1.amazonaws.com/index.html"

#dynamodb tables
visitor_table = dynamodb.Table(VISITORS_TABLE)
passcode_table = dynamodb.Table(PASSCODES_TABLE)
table = dynamodb.Table('ExecutionControl')
last_execution_time = 0

#email ids and SES
ses = boto3.client('ses', region_name='us-east-1')
sender = "ayshag@umich.edu"
recipient = "ayshathasneemg@gmail.com"


def lambda_handler(event, context):
    # Fetches the last recorded execution time.
    response = table.get_item(Key={'PK': 'last_execution'})
    last_execution_time = float(response.get('Item', {}).get('timestamp', 0))

    #  Controls execution frequency
    current_time = time.time()
    if current_time - last_execution_time < 10:
        print("Skipping execution to control trigger frequency.")
        return
    
    # Stores the new timestamp.
    table.put_item(Item={'PK': 'last_execution', 'timestamp': Decimal(str(current_time))})

    # Process each face detected in the Kinesis stream and Decode the Kinesis data
    record = event['Records'][0]
    
    payload = json.loads(base64.b64decode(record['kinesis']['data']).decode('utf-8'))
    face_search_response = payload.get("FaceSearchResponse", [])

    for face_data in face_search_response:
        # Check if this face matches any known faces in the Rekognition collection
        matched_faces = face_data.get("MatchedFaces", [])
        
        if matched_faces:
            print("Known visitor face")
            # Known visitor face detected
            face_id = matched_faces[0].get("Face", {}).get("FaceId")
            if face_id:
                image_key = extract_and_store_visitor_image(payload)
                # Fetch image and then upload to S3, and append to photos array for known face
                if image_key:
                    insert_visitor_record(image_key, face_id)
                    otp = insert_access_otp(face_id)
                    subject = "VISITOR ACCESS OTP"
                    body_text = "Visitor Access OTP = {}".format(otp) + "\n\nPlease find the visitor Access Portal link: {}".format(otp_site)
                    send_email_ses(sender, recipient, subject, body_text)

        else:
            # Unknown visitor face detected
            print("Unknown visitor face")
            detected_face = face_data.get("DetectedFace", {})
            if detected_face:
                image_key = extract_and_store_visitor_image(payload)
                presigned_url = generate_presigned_url(image_key, expiration=3600)
                if image_key:
                    face_id = insert_visitor_record(image_key)
                    subject = "VISITOR REGISTRATION LINK"
                    body_text = "\n\Please find the visitor Registration Portal link: {}?imageUrl={}&faceId={}".format(registration_site, presigned_url, face_id)
                    send_email_ses(sender, recipient, subject, body_text)
        break
    return {'statusCode': 200, 'body': json.dumps('Lambda executed successfully')}


def insert_visitor_record(image_key, face_id=None):
    # Generate a unique face ID using UUID
    if not face_id:
        face_id = str(uuid.uuid4())
        print("Generated new temp faceid: ", face_id)

    # Add item to DynamoDB with timestamp
    # name lookup
    if face_id == "4810c074-69da-45ef-80a5-e9eb123dc63b":
        name = "Aysha Gulshan"
    else:
        name = "Guest"
        
    item = {
        "FaceId": face_id,
        "Name": name,
        "photos": [
            {
                "objectKey": image_key
            }
        ]
    }
    
    # Insert the item into DynamoDB
    visitor_table.put_item(Item=item)
    return face_id

#generating the image url with image key
def generate_presigned_url(image_key, expiration=3600):
    print("image key",image_key)
    url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': BUCKET_NAME, 'Key': image_key},
        ExpiresIn=expiration
    )
    encoded_url = quote(url, safe='')
    return encoded_url

#insert the otp to passcodes tables
def insert_access_otp(face_id):
    # Generate OTP and store in "passcodes" table
    otp = random.randint(100000, 999999)
    # 5-minute expiration
    expiration_time = int(time.time() + 5 * 60)  

    passcode_table.put_item(
        Item={
            'FaceId': face_id,
            'OTP': otp,
            'ExpirationTime': expiration_time
        }
    )
    return otp

#method to extract the visitor image and store it
def extract_and_store_visitor_image(payload):
    stream_arn = payload['InputInformation']['KinesisVideo']['StreamArn']
    fragment_number = payload['InputInformation']['KinesisVideo']['FragmentNumber']
    """Extract an image from Kinesis Video Streams and store it in S3."""
    # Get the endpoint for Kinesis Video Streams
    response = kinesis_video.get_data_endpoint(
        StreamARN=stream_arn,
        APIName='GET_MEDIA_FOR_FRAGMENT_LIST'
    )
    endpoint = response['DataEndpoint']

    kinesis_media_client = boto3.client('kinesis-video-archived-media', endpoint_url=endpoint)
    
    # Replace with actual fragment numbers
    fragment_list = [fragment_number]  

    media_response = kinesis_media_client.get_media_for_fragment_list(
            StreamARN=stream_arn,
            Fragments=fragment_list
        )

    media_stream = media_response['Payload']

    with tempfile.NamedTemporaryFile(delete=True) as temp_video_file:
        # Write the media stream to the temporary file
        temp_video_file.write(media_stream.read())
        temp_video_file.flush()

        # Use OpenCV to read the video file and extract a frame
        cap = cv2.VideoCapture(temp_video_file.name)
        
        # Read the first frame
        ret, frame = cap.read()
        if ret:
            # Encode the frame as JPEG
            _, image_data = cv2.imencode('.jpg', frame)

            # Convert to bytes
            image_bytes = image_data.tobytes()

            # Store the image in S3
            # Replace with your S3 bucket name
            s3_bucket_name = 'my-smartdoor-visitor-images'
            # Unique key for the image
            s3_object_key = f'user_images/{time.time()}.jpg'  
            s3.put_object(
                Bucket=s3_bucket_name,
                Key=s3_object_key,
                Body=image_bytes,
                ContentType='image/jpeg'  # Set appropriate content type
            )

            return s3_object_key  # Return the S3 URL
        else:
            print("Failed to read frame from video.")
            return None

    cap.release()

# sending email through ses
def send_email_ses(sender, recipient, subject, body_text):

    try:
        response = ses.send_email(
            Source=sender,
            Destination={
                'ToAddresses': [recipient]
            },
            Message={
                'Subject': {
                    'Data': subject,
                    'Charset': 'UTF-8'
                },
                'Body': {
                    'Text': {
                        'Data': body_text,
                        'Charset': 'UTF-8'
                    }
                }
            }
        )
        print(f"Email sent! Message ID: {response['MessageId']}")
    except Exception as e:
        print(f"Error sending email: {e}")
import uuid

#Generate a unique identifier for an unknown face
def generate_temporary_face_id():
    return str(uuid.uuid4())

#Check if visitor face exists in the visitors table (DB2)
def is_known_face(face_id):
    response = dynamodb.get_item(TableName=VISITORS_TABLE, Key={'faceId': {'S': face_id}})
    return 'Item' in response


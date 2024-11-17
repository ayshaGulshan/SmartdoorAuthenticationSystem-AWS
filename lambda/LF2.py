import boto3
import random
import time

#dynamodb tables
dynamodb = boto3.resource('dynamodb')
visitors_table = dynamodb.Table('visitors')
passcodes_table = dynamodb.Table('passcodes')

#email details
ses = boto3.client('ses', region_name='us-east-1')
sender = "ayshag@umich.edu"
recipient = "ayshathasneemg@gmail.com"

#web URL
otp_site = "https://smartdoor-frontend-bucket.s3.us-east-1.amazonaws.com/virtualdoor.html"

#Lambda that handle the OTP generation and storage
def lambda_handler(event, context):
    print("event")
    print(event)
    face_id = event['faceId']
    name = event['name']
    email = event['email']

    # Store visitor information in the "visitors" table
    visitors_table.put_item(
        Item={
            'FaceId': face_id,
            'Name': name,
            'Email': email
        }
    )

    # Generate OTP and store in "passcodes" table
    otp = random.randint(100000, 999999)
    expiration_time = int(time.time() + 5 * 60)  # 5-minute expiration

    passcodes_table.put_item(
        Item={
            'FaceId': face_id,
            'OTP': otp,
            'ExpirationTime': expiration_time
        }
    )

    subject = "VISITOR ACCESS OTP"
    body_text = "Visitor Access OTP = {}".format(otp) + "\n\nPlease find the visitor Access Portal link: {}".format(otp_site)
    send_email(sender, recipient, subject, body_text)
    return {
        'statusCode': 200,
        'body': 'Visitor is approved and Access OTP is generated.'
    }

#send email to the via ses
def send_email(sender, recipient, subject, body_text):

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
    except Exception as e:
        print(f"Error sending email: {e}")
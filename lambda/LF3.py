import boto3
import time

#dynamodb tables
dynamodb = boto3.resource('dynamodb')
visitors_table = dynamodb.Table('visitors')
otp_table = dynamodb.Table('passcodes')


def lambda_handler(event, context):
    # Retriving the OTP from request
    input_otp = event['otp']
    print(input_otp)
    
    # Fetch the record by OTP from the passcodes table
    response = otp_table.scan(
        FilterExpression="OTP = :otp",
        ExpressionAttributeValues={":otp": int(input_otp)}
    )
    items = response.get('Items', [])

    # Check if access OTP is found and is a valid OTP
    if not items:
        return {
            'statusCode': 403,
            'body': {"valid": False, "message": "Invalid OTP is provided."}
        }

    otp_entry = items[0]
    otp_expiration_time = otp_entry['ExpirationTime']
    visitor_face_id = otp_entry['FaceId']
    current_time = int(time.time())

    # Validate the access OTP expiration
    otp_remaining_time = otp_expiration_time - current_time
    if otp_remaining_time <= 0:
        return {
            'statusCode': 403,
            'body': {"valid": False, "message": "Expired OTP."}
        }

    # Fetching the visitor's name from the visitors table
    visitor_reply = visitors_table.get_item(Key={'FaceId': visitor_face_id})
    visitor_name = visitor_reply['Item']['Name'] if 'Item' in visitor_reply else "Visitor"

    # success message is  returned with remaining time
    return {
        'statusCode': 200,
        'body': {"valid": True, "name": visitor_name, "remainingTime": otp_remaining_time}
}
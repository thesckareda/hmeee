import os

from twilio.rest import Client

account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
client = Client(account_sid, auth_token)

message = client.messages.create(
    from_=os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886"),
    content_sid=os.environ.get("TWILIO_CONTENT_SID", ""),
    content_variables='{"1":"12/1 hfkjhwekfhsdak","2":"3pm"}',
    to=os.environ.get("TWILIO_TEST_TO", "whatsapp:+916300632510"),
)

print(message.sid)
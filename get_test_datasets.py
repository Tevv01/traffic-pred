import httpx
import uuid
from datetime import datetime
import os
from dotenv import load_dotenv

env = load_dotenv(".env")

client_name = os.environ["CLIENT_NAME"]
url = os.environ["ENTUR_TIMETABLE_URL"]
requestor_id = os.environ["UUID_REQUESTORID"]

params = {"datasetId":"OST", "requestorId" : requestor_id}
headers = {"ET-Client-Name" : client_name}


response = httpx.get(url, headers=headers, params=params, timeout=None)
response.raise_for_status()


print(f"GET RESPONSE STATUS CODE: {response.status_code}\n")

print(f"GET RESPONSE LENGTH: {len(response.content)}\n")

print(f"GET RESPONSE CONTENT TYPE HEADER: {response.headers.get("Content-Type")}\n")



with (open(f"data/ost-{datetime.now().strftime("%d_%m_%H_%M_%S")}.xml", "wb")) as file:
    file.write(response.content)
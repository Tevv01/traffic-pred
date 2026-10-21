import httpx
import uuid
import time



url = "https://api.entur.io/realtime/v1/rest/et"
params = {"datasetId":"OST"}
headers = {"ET-Client-Name":"vetle_personal"}


response = httpx.get(url, headers=headers, params=params, timeout=None)
response.raise_for_status()


print(f"GET RESPONSE STATUS CODE: {response.status_code}\n")

print(f"GET RESPONSE LENGTH: {len(response.content)}\n")

print(f"GET RESPONSE CONTENT TYPE HEADER: {response.headers.get("Content-Type")}\n")



with (open(f"data/ostfold_data.xml", "wb")) as file:
    file.write(response.content)
    file.close()
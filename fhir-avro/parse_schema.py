import requests
import json

r = requests.get(
    "http://10.10.1.141:8000/StructureDefinition/Patient", auth=("username", "pass")
)

schema = r.json()


print(json.dumps(schema))

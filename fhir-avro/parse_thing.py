import requests
import json

r = requests.get(
    "http://10.10.1.141:8000/Patient/Patient.SD-PREASA7S.17536",
    auth=("username", "pass"),
)

response = r.json()

us_core_ethnicity = response["extension"][0]["extension"][1]["valueString"]
print("core ethnicty: ", us_core_ethnicity)

us_core_race = response["extension"][1]["extension"][1]["valueString"]
print("core_race: ", us_core_race)

gender = response["gender"]
print("gender: ", gender)

fhir_id = response["id"]
print("id: ", fhir_id)

identifier = response["identifier"][0]["value"]
print("value: ", identifier)

record = {}
record["identifier"] = identifier
record["us-core-ethnicity"] = us_core_ethnicity
record["us-core-race"] = us_core_race
record["gender"] = gender

print(json.dumps(record))

new = requests.get("http://10.10.1.141:8000/Patient/", auth=("username", "pass"))

new_response = new.json()

print("patients!!!: ", len(new_response["entry"]))

# print(response["id"])

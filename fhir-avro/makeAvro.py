from fastavro import writer, reader, parse_schema
import requests
import json

schema = {}

with open("avro_patient.json", "r") as thing:
    schema = json.loads(thing.read())

p_schema = parse_schema(schema)

r = requests.get("http://10.10.1.141:8000/Patient/", auth=("username", "pass"))

records = r.json()["entry"]

parsed_records = []

for record in records:
    thing = {}

    # print(json.dumps(record))
    thing["us-core-ethnicity"] = record["resource"]["extension"][0]["extension"][1][
        "valueString"
    ]
    thing["us-core-race"] = record["resource"]["extension"][1]["extension"][1][
        "valueString"
    ]
    thing["gender"] = record["resource"]["gender"]
    thing["identifier"] = record["resource"]["identifier"][0]["value"]
    parsed_records.append(thing)


# records = [{"identifier": "17536", "us-core-ethnicity": "Not Hispanic or Latino", "us-core-race": "White", "gender": "male", "active": True}]

with open("patient.avro", "wb") as out:
    writer(out, p_schema, parsed_records)

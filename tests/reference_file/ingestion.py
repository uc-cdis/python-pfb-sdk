import os
import json
from pfb.writer import PFBWriter
from pfb.importers.gen3dict import _from_dict
from pfb.reader import PFBReader
from pfb.importers.json import _convert_json, _from_json
from fastavro import reader as avro_reader
import glob
import itertools
import sys


def test_example_bdc_schema(runner):
    """
    mimics
    invoke("from", "-o", "minimal_schema.avro", "dict", "<url>")
    """
    with runner.isolated_filesystem():
        url = "https://s3.amazonaws.com/dictionary-artifacts/gtexdictionary/4.4.3/schema.json"
        file_path = "schema_example.avro"
        try:
            writer_pre = PFBWriter(file_path)
            with writer_pre as writer:
                _from_dict(writer, url)
                with open(file_path, "rb") as output_file:
                    r = avro_reader(output_file)
                    entity = r.writer_schema["entity"]
                    # test_schema(r)
                    assert entity == "Entity"
        except Exception as e:
            print("Exception: ", str(e))


def test_creating_file():
    """
    mimics
    result = invoke(
        "from", "-o",
        "example_reference_file.avro",
        "json",
        "-s",
        "minimal_schema.avro",
        "--program",
        "DEV",
        "--project",
        "test",
        "./json")
    """
    output_location = "./output/example_reference_file.avro"
    schema_location = "minimal_schema.avro"
    location_of_json_data_to_import = "./pfb-data/ref_file/json"

    try:
        writer_pre = PFBWriter(output_location)
        with writer_pre as writer:
            with PFBReader(schema_location) as reader_e:
                writer.copy_schema(reader_e)
            writer.write(_from_json(writer.metadata, location_of_json_data_to_import, "DEV", "test"))
            with PFBReader(output_location) as output_reader:
                # todo: what am i testing here?
                print(output_reader)
    except Exception as e:
        print("Exception: ", str(e))


def from_json(metadata, path, program, project):
    """
    stolen from elsewhere
    """
    link_dests = {
        node["name"]: {link["name"]: link["dst"] for link in node["links"]}
        for node in metadata["nodes"]
    }
    order = glob.glob(os.path.join(path, "*.json"))
    total = len(order)
    final_json = []
    enumerated_order = enumerate(order)
    for i, o in enumerated_order:
        o = os.path.basename(o).replace(".json", "").strip()

        with open(os.path.join(path, o + ".json"), "r") as f:
            json_data = json.load(f)

        node_name = o

        if isinstance(json_data, dict):
            json_data = [json_data]
        json_records = []
        for json_record in json_data:
            record = _convert_json(node_name, json_record, program, project, link_dests)
            json_records.append(record)
        final_json.append(json_records)
    return final_json


def test_pfb_import(runner, invoke, path_join):
    """
    mimics
    invoke(
        "from",
        "-o",
        "./output/example_reference_file.avro",
        "json",
        "-s",
        "./minimal_schema.avro",
        "--program",
        "NSRR",
        "--project",
        "CFS",
        "json/")
    """
    try:
        with PFBWriter("minimal_data.avro") as writer:
            with PFBReader("minimal_schema.avro") as s_reader:
                writer.copy_schema(s_reader)
            data_from_json = from_json(writer.metadata, "json/", "NSRR", "CFS")
            for entry in data_from_json:
                writer.write(entry)
            with PFBReader("minimal_data.avro") as d_reader:
                for r in itertools.islice(d_reader, None):
                    json.dump(r, sys.stdout)
                    sys.stdout.write("\n")
    except Exception as e:
        print("Failed! -> ", e)
        raise


def test_reference_file_nodes(runner, invoke, path_join):
    """
    mimics the command
    invoke("show", "-i", "./minimal_data.avro.old", "nodes")
    """
    schema_location = "./minimal_data.avro"
    try:
        with PFBReader(schema_location) as d_reader:
            for node in d_reader.schema:
                print(node["name"])
    except FileNotFoundError:
        print("File not found!")
    except StopIteration:
        print("\nIteration exhausted!")
    except Exception as e:
        print("Unrecognized exception: ", e)

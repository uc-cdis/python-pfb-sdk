import itertools
import os
import random
import shutil
import string
import sys
from functools import partial, reduce
from typing import List

import requests
import csv
import json
from gen3.auth import Gen3Auth
from gen3.index import Gen3Index

from pfb.reader import PFBReader
from pfb.writer import PFBWriter
from tests.reference_file.test_ingestion import from_json_v2
from ref_file_helper import generate_unique_submitter_ids_v2, create_reference_file_node
from typing import List, Dict, Any

LEFT = 0
RIGHT = 1


def tsv_to_json(tsv_file_path) -> List[Dict[Any, Any]]:
    data = []
    with open(tsv_file_path, 'r', newline='', encoding='utf-8') as tsv_file:
        reader = csv.DictReader(tsv_file, delimiter='\t')
        for row in reader:
            data.append(row)
    return data


def test_tsv_ingestion():
    outcome = tsv_to_json("tsv/dest-bucket-manifest.tsv")
    print(outcome)


from datetime import datetime, timezone


def generate_random_string():
    characters = string.ascii_letters + string.digits
    random_string = ''.join(random.choice(characters) for _ in range(12))
    return random_string


def create_ref_file_node(indexd_data):
    object_id = indexd_data["did"]
    drs_uri = f"drs://{object_id}"

    reference_file = {
        "data_category": "Clinical Data",
        "data_format": indexd_data["bucket_path"].split(".")[-1],
        "data_type": "Other",
        "file_name": indexd_data["file_name"],
        "file_size": indexd_data["size"],
        "md5sum": indexd_data["hashes"]["md5"],
        "submitter_id": indexd_data["submitter_id"],
        "type": "reference_file",
        "ga4gh_drs_uri": drs_uri,
        "object_id": object_id,
    }
    pfb_data = {
        "program": indexd_data["program"],
        "project": indexd_data["project"],
        "reference_file": reference_file
    }
    return pfb_data


def clear_directory(directory_path):
    # Check if the directory exists
    if not os.path.exists(directory_path):
        print(f"The directory {directory_path} does not exist.")
        return

    # Iterate over all the files and directories in the specified directory
    for filename in os.listdir(directory_path):
        file_path = os.path.join(directory_path, filename)
        try:
            # Check if it is a file and remove it
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.remove(file_path)
            # Check if it is a directory and remove it
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Failed to delete {file_path}. Reason: {e}')


def write_dicts_to_json_files(directory, index_with_node_dict):
    """
    Writes each dictionary in the list to a separate JSON file.

    :param dict_list: List of dictionaries to write to files.
    :param directory: The directory where the files will be saved.
    """

    file_path = os.path.join(directory, f"entry_{index_with_node_dict[0] + 1}.json")
    with open(file_path, 'w') as json_file:
        json.dump(index_with_node_dict[1], json_file, indent=4)


def for_each(iterable, run_side_effect):
    for item in iterable:
        run_side_effect(item)


def ingest_json_files_into_pfb(program, project, reference_file_nodes):
    try:
        # todo: figure out where to get ref_file schema from
        # right now we get it from that manifest file in github iirc
        path = "avro/" + project + ".avro"
        with PFBReader("avro/minimal/minimal_schema.avro") as s_reader:
            data_from_json = []
            for reference_file_node in reference_file_nodes:
                node_info = {
                    "program": program,
                    "project": project,
                    "reference_file": reference_file_node
                }
                data_from_json.append(from_json_v2(s_reader.metadata, node_info))
            with PFBWriter(path) as d_writer:
                d_writer.copy_schema(s_reader)
                d_writer.write(data_from_json)
                # if I get the "a+" error it's because i'm trying to write one entry in at a time
                # in contrast to just writing it in all at once, as is done in the line above
                # for json_data in data_from_json:
                #     d_writer.write([json_data])
            with PFBReader(path) as d_reader:
                for r in itertools.islice(d_reader, None):
                    json.dump(r, sys.stdout)
                    sys.stdout.write("\n")
    except Exception as e:
        print("Failed! -> ", e)
        raise


def add_program_and_project_old(dest):
    def get_program_and_project(ref_file_info):
        dataset = dest.get(ref_file_info["study_with_consent"], None)
        assert dataset is not None
        program_project = dataset["dataset_identifier"].split('-', 1)
        ref_file_info["program"] = program_project[0]
        ref_file_info["project"] = program_project[1]
        return ref_file_info

    return get_program_and_project


def add_program_and_project_to_indexd_closure(guid_to_updated_nodes):
    def add_program_and_project_to_indexd(indexd_data):
        dataset = guid_to_updated_nodes.get(indexd_data["did"], None)
        assert dataset is not None
        indexd_data["program"] = dataset["program"]
        indexd_data["project"] = dataset["project"]
        return indexd_data

    return add_program_and_project_to_indexd


def insert_or_append(graph, key, value):
    # Get the list for the key, creating it if necessary
    graph.setdefault(key, []).append(value)
    return graph


def group_by(group_identifier, accessor=lambda gid, e: e[gid], inserter=insert_or_append):
    def add_to_graph(graph, data_entry):
        group_id = accessor(group_identifier, data_entry)
        graph = inserter(graph, group_id, data_entry)
        return graph

    return add_to_graph


def add_or_insert(phs_to_file_data, file):
    field_value = file["study_with_consent"]
    field_exists = field_value in phs_to_file_data
    if field_exists:
        phs_to_file_data[field_value].append(file)
    else:
        phs_to_file_data[field_value] = [file]
    return phs_to_file_data


def map_from(key, dictionaries):
    def map_from_key_in_dictionary(dictionary):
        value_at_key = dictionary[key]
        return value_at_key, dictionary

    return dict(map(map_from_key_in_dictionary, dictionaries))


def map_values(mutator, dictionary):
    def map_value(kv_pair):
        return kv_pair[LEFT], mutator(kv_pair[RIGHT])

    return dict(map(map_value, dictionary.items()))


def derive_acl_to_program_project_from_destination_manifest(manifest_location):
    dest_data = tsv_to_json(manifest_location)
    acl_to_dest_data = map_from("acl", dest_data)

    def get_program_project_from_dest_entry(dest_entry):
        program_project_pair = dest_entry["dataset_identifier"].split('-', 1)
        return {"program": program_project_pair[LEFT],
                "project": program_project_pair[RIGHT]}

    acl_to_program_project = map_values(get_program_project_from_dest_entry, acl_to_dest_data)
    return acl_to_program_project


def get_and_save_indexd_records_to_file(reference_file_guids, index):
    cred_path = os.environ.get("PP_CREDS")
    auth = Gen3Auth(refresh_file=cred_path)
    index = Gen3Index(auth_provider=auth)

    def chunk(lst, size):
        return [lst[i:i + size] for i in range(0, len(lst), size)]

    chunked_guids = chunk(reference_file_guids, 2500)

    def get_and_save_chunked_records():
        def get_chunked_records(indexd_chunks, guid_chunk):
            indexd_data_chunk = index.get_records(guid_chunk)
            indexd_chunks += indexd_data_chunk
            return indexd_chunks

        indexd_data = reduce(get_chunked_records, chunked_guids, [])
        with open("json/indexd/ref_file_indexd_records.json", 'w') as file:
            json.dump(indexd_data, file, indent=4)


def read_json(file_location):
    with open(file_location, 'r') as file:
        file_contents = json.load(file)
    return file_contents


def read_tsv(file_location):
    with open(file_location, 'r') as file:
        reader = list(csv.DictReader(file, delimiter='\t'))
    return reader

def map_guid_to_release_data():
    release_files = tsv_to_json("tsv/release_manifest_release-27.tsv")
    fields_with_accession_number = list(filter(lambda entry: bool(entry.get("study_accession_with_consent")),
                                               release_files))
    guid_to_release_data = map_from("guid", fields_with_accession_number)
    return guid_to_release_data


def derive_guid_to_program_project(guid_to_release_data):
    acl_to_program_project = derive_acl_to_program_project_from_destination_manifest("tsv/dest-bucket-manifest.tsv")
    guid_to_acl = map_values(lambda field: field["study_with_consent"], guid_to_release_data)
    guid_to_program_project = map_values(lambda acl: acl_to_program_project[acl], guid_to_acl)
    return guid_to_program_project


def handle_querying_indexd_for_ref_file_records(guid_to_release_data):
    # derive_guid_to_program_project()
    # reference_file_guids = list(guid_to_release_data.keys())
    guid_to_accession = map_values(lambda field: field["study_with_consent"], guid_to_release_data)
    # get_and_save_indexd_records_to_file("...")


def handle_directory_stuff():
    # output_directory_for_ref_file_json_files = "json/output_ref_files/"
    # if not os.path.exists(output_directory_for_ref_file_json_files):
    #     try:
    #         os.makedirs(output_directory_for_ref_file_json_files)
    #     except Exception as e:
    #         print(e)
    # if len(os.listdir(output_directory_for_ref_file_json_files)) > 0:
    #     clear_directory(output_directory_for_ref_file_json_files)
    # for_each(list(enumerate(output)), partial(write_dicts_to_json_files, output_directory_for_ref_file_json_files))
    pass


def get_indexd_data_and_add_program_project(guid_to_program_project):
    indexd_data = read_json("json/indexd/ref_file_indexd_records.json")

    def add_program_and_project(indexd_entry):
        program_project = guid_to_program_project.get(indexd_entry["did"])
        assert program_project is not None
        indexd_entry["program"] = program_project["program"]
        indexd_entry["project"] = program_project["project"]
        return indexd_entry

    return list(map(add_program_and_project, indexd_data))


def try_next(iterator_instance):
    try:
        next_element = next(iterator_instance)
        return next_element
    except StopIteration:
        return None


def mint_new_submitter_id(existing_ids, bucket_url):
    path_components = bucket_url[5:].split("/")
    reversed_components = iter(path_components[::-1])

    def generate_submitter_id(previous_submitter_id, id_components):
        next_element = try_next(id_components)
        if next_element is None:
            return None
        else:
            return previous_submitter_id + "_" + next_element

    new_submitter_id = try_next(reversed_components)
    id_already_used = new_submitter_id in existing_ids
    exhausted_possible_submitter_ids = new_submitter_id is None
    while id_already_used and not exhausted_possible_submitter_ids:
        new_submitter_id = generate_submitter_id(new_submitter_id, reversed_components)
        id_already_used = new_submitter_id in existing_ids
        exhausted_possible_submitter_ids = new_submitter_id is None
    assert new_submitter_id is not None  # come up with a definitive way to create unique submitter IDs
    existing_ids.add(new_submitter_id)
    return new_submitter_id


def generate_submitter_ids(guid_to_bucket_urls):
    existing_ids = set()
    guid_to_submitter_ids = map_values(partial(mint_new_submitter_id, existing_ids), guid_to_bucket_urls)
    return guid_to_submitter_ids


def add_submitter_ids(indexd_contexts):
    def map_guid_to_bucket_path(mapping, indexd_context):
        mapping[indexd_context["did"]] = indexd_context["bucket_path"]
        return mapping
    guid_to_bucket_urls = reduce(map_guid_to_bucket_path, indexd_contexts, {})
    guid_to_submitter_ids = generate_submitter_ids(guid_to_bucket_urls)
    add_submitter_id = lambda d: insert(d, ("submitter_id", guid_to_submitter_ids[d["did"]]))
    reference_file_context = list(map(add_submitter_id, indexd_contexts))
    return reference_file_context


def upsert(identifier, graph, value):
    identifier_exists = identifier in graph
    if identifier_exists:
        graph[identifier].append(value)
    else:
        graph[identifier] = [value]
    return graph


def graph_to(key, dictionary_list):
    def add_to_graph(graph, dictionary):
        value_at_key = dictionary[key]
        graph = upsert(value_at_key, graph, dictionary)
        return graph

    return reduce(add_to_graph, dictionary_list, {})


def insert(dictionary, pair):
    dictionary.update([pair])
    return dictionary


def test_full_ingestion_process():
    guid_to_release_data = map_guid_to_release_data()
    guid_to_program_project = derive_guid_to_program_project(guid_to_release_data)
    guid_to_urls = map_values(lambda d: d["urls"], guid_to_release_data)
    # guid_to_accession = map_values(lambda field: field["study_accession_with_consent"], guid_to_release_data)
    indexd_data_with_program_and_project = get_indexd_data_and_add_program_project(guid_to_program_project)
    # indexd_with_needed_context = list(map(lambda d: insert(d, ("urls", guid_to_urls[d["did"]])),
    #                                   indexd_data_with_program_and_project))
    bad_indexd_data = list(filter(lambda context: len(context["urls"]) == 0, indexd_data_with_program_and_project))
    indexd_data_in_google = list(filter(lambda context: len(context["urls"]) == 1, indexd_data_with_program_and_project))
    indexd_data_in_amazon = list(filter(lambda context: len(context["urls"]) == 2, indexd_data_with_program_and_project))
    indexd_google_data_with_bucket_path = list(map(lambda d:insert(d, ("bucket_path", d["urls"][0])),
                                                   indexd_data_in_google))
    indexd_amazon_data_with_bucket_path = list(map(lambda d: insert(d, ("bucket_path", d["urls"][1])),
                                                   indexd_data_in_amazon))
    regrouped_indexd_data = indexd_google_data_with_bucket_path + indexd_amazon_data_with_bucket_path
    indexd_data_with_submitter_id = add_submitter_ids(regrouped_indexd_data)
    reference_file_nodes = list(map(create_ref_file_node, indexd_data_with_submitter_id))

    def organize_by_program(program_to_ref_file_context, ref_file_context):
        program = ref_file_context["program"]
        program_exists = program in program_to_ref_file_context
        program_context = {"project": ref_file_context["project"],
                           "reference_file": ref_file_context["reference_file"]}
        if program_exists:
            program_to_ref_file_context[program].append(program_context)
        else:
            program_to_ref_file_context[program] = [program_context]
        return program_to_ref_file_context

    program_to_reference_file_nodes = reduce(organize_by_program, reference_file_nodes, {})

    def collect_to_project(program_with_reference_files_under_program):
        def build_project_contexts(project_to_ref_file_context, project_context):
            project = project_context["project"]
            project_already_added = project in project_to_ref_file_context
            if project_already_added:
                project_to_ref_file_context[project].append(project_context["reference_file"])
            else:
                project_to_ref_file_context[project] = [project_context["reference_file"]]
            return project_to_ref_file_context

        reference_files_under_program = program_with_reference_files_under_program[1]
        project_to_reference_file_nodes = reduce(build_project_contexts, reference_files_under_program, {})
        return program_with_reference_files_under_program[0], project_to_reference_file_nodes

    program_to_project_context = dict(map(collect_to_project, program_to_reference_file_nodes.items()))

    for program, project_context in program_to_project_context.items():
        for project, reference_files in project_context.items():
            ingest_json_files_into_pfb(program, project, reference_files)
    print("done!")
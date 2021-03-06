import os
import copy
from pymongo import MongoClient
from flask import Response, send_file
from bson.json_util import dumps
import logging
import time
import pdsdpimockfhir.cache as cache
import requests
import sys
from math import ceil
from urllib.parse import urlsplit
from tx.fhir.utils import bundle, unbundle
from tx.functional.either import Left, Right
import tx.functional.maybe as maybe
from tx.functional.utils import identity
from tx.logging.utils import getLogger
import multiprocessing
from multiprocessing import Process, Manager
from multiprocessing.pool import Pool
from tempfile import mkdtemp
from functools import partial
import json
import os.path
from pathvalidate import validate_filename
import shutil

logger = getLogger(__name__, logging.INFO)


fhir_server_url_base = os.environ.get("FHIR_SERVER_URL_BASE")


def _get_patient(patient_id):
    resc = cache.get_patient(patient_id)

    if resc is not None:
        return resc
    elif fhir_server_url_base is not None and fhir_server_url_base != "":
        resp = requests.get(f"{fhir_server_url_base}/Patient/{patient_id}")
        if resp.status_code == 404:
            return None
        else:
            resc = resp.json()
            cache.update_patient(resc)
            return resc
    else:
        return None
        

def get_patient(patient_id):
    resc = _get_patient(patient_id)

    if resc is not None:
        return resc
    else:
        return "not found", 404
        

def _get_resource(resc_type, patient_id):
    resources = cache.get_resource(resc_type, patient_id)

    if resources is not None and resources["entry"] != []:
        return resources
    elif fhir_server_url_base is not None and fhir_server_url_base != "":
        resp = requests.get(f"{fhir_server_url_base}/{resc_type}?patient={patient_id}")
        logger.debug(f"{fhir_server_url_base}/{resc_type}?patient={patient_id} => {resp.status_code}")
        if resp.status_code == 404:
            return None
        else:
            resources = resp.json()
            cache.update_resource(resc_type, patient_id, resources)
            return resources
    else:
        return bundle([])

    

output_dir = os.environ.get("OUTPUT_DIR", "/tmp")

def post_resources(resc_types, patient_ids, output_name):
    patients = []

    n_jobs = maybe.from_python(os.environ.get("N_JOBS")).bind(int).rec(identity, multiprocessing.cpu_count())

    def proc(output_dir, resc_types, patient_ids):
        for patient_id in patient_ids:
            logger.info(f"processing patient {patient_id}")
            requests = []
            for resc_type in resc_types:
                if resc_type == "Patient":
                    requests.append({
                        "url": f"/Patient/{patient_id}",
                        "method": "GET"
                    })
                else:
                    requests.append({
                        "url": f"/{resc_type}?patient={patient_id}",
                        "method": "GET"
                    })
            batch = bundle(requests, "batch")
            patient = _post_batch(batch).value
            with open(os.path.join(output_dir, patient_id + ".json"), "w") as out:
                json.dump(patient, out)
        
    def merge_files(input_dir, patient_ids):
        data = []
        for patient_id in patient_ids:
            input_file = os.path.join(input_dir, patient_id+".json")
            with open(input_file) as inp:
                logger.info(f"merging {input_file}")
                data.append(json.load(inp))
        return data

    with Manager() as m:
        processes = []
        ids = list(patient_ids)
        ids_len = len(ids)
        chunk_size = int(ceil(ids_len / n_jobs))
        
        if output_name is None:
            tmpdir = mkdtemp()
        else:
            validate_filename(output_name)
            tmpdir = os.path.join(output_dir, output_name)
            os.makedirs(tmpdir, exist_ok=True)
                
        try:
            for i in range(n_jobs):
                p = Process(target=proc, args=(tmpdir, resc_types, ids[min(ids_len, i * chunk_size): min(ids_len, (i + 1) * chunk_size)]))
                p.start()
                processes.append(p)
                
            for p in processes:
                p.join()
                
                
            logger.info(f"finished processing patients")

            if output_name is None:
                return merge_files(tmpdir, patient_ids)
            else:
                return {
                    "$ref": output_name
                }
        finally:
            if output_name is None:
                shutil.rmtree(tmpdir)


def get_resource(resource_name, patient_id):
    bundle = _get_resource(resource_name, patient_id)
    if bundle is None:
        return "not found", 404
    else:
        return bundle

                    
def post_patient(resource):
    cache.update_patient(resource)
    return "success", 200
    

def post_resource(resource):
    cache.post_resource(resource)
    return "success", 200


def post_batch(batch):
    return _post_batch(batch).rec(lambda x: (x, 500), lambda x: x)


def _post_batch(batch):
    def handle_requests(requests):
        rescs = []
        for request in requests:
            logger.info(f"processing request {request}")
            method = request["method"]
            url = request["url"]
            result = urlsplit(url)
            pcs = result.path.split("/")
            qcs = map(lambda x: x.split("="), result.query.split("&"))
            if pcs[1] == "Patient":
                rescs.append(_get_patient(pcs[2]))
            else:
                patient_id = None
                for qc in qcs:
                    if qc[0] == "patient":
                        patient_id = qc[1]
                rescs.append(_get_resource(pcs[1], patient_id))
        return Right(bundle(rescs, "batch-response"))

    return unbundle(batch).bind(handle_requests)


def post_bundle(bundle):
    cache.post_bundle(bundle)
    return "success", 200


def delete_resource():
    cache.delete_resource()
    return "success", 200



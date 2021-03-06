import argparse
import os
import json
import requests
import logging
from tx.fhir.utils import bundle, unbundle
from tx.logging.utils import getLogger
from convert import mapping_pcornet_to_fhir
from joblib import Parallel, delayed
import contextlib
import joblib
from tqdm import tqdm
import time


@contextlib.contextmanager
def tqdm_joblib(tqdm_object):
    """Context manager to patch joblib to report into tqdm progress bar given as argument"""

    def tqdm_print_progress(self):
        if self.n_completed_tasks > tqdm_object.n:
            n_completed = self.n_completed_tasks - tqdm_object.n
            tqdm_object.update(n=n_completed)

    original_print_progress = joblib.parallel.Parallel.print_progress
    joblib.parallel.Parallel.print_progress = tqdm_print_progress

    try:
        yield tqdm_object
    finally:
        joblib.parallel.Parallel.print_progress = original_print_progress
        tqdm_object.close()
        

parser = argparse.ArgumentParser(description='Process arguments.')
parser.add_argument('--nthreads', type=int, default=4, help='number of threads')
parser.add_argument('--base_url', type=str, required=True, help='base url of fhir plugin')
parser.add_argument('--input_dir', type=str, required=True, help='input dir of the files containing data to be ingested')
parser.add_argument('--input_data_format', type=str, default='fhir', help='input data format. Only fhir and pcori '
                                                                          'are supported. The default is fhir bundles.')
parser.add_argument('--output_dir', type=str, default='output', help='Output directory for converted fhir bundles for '
                                                                     'input data format other than fhir, e.g., pcori')
parser.add_argument('--dry_run', action='store_true', default=False, help='dry run without actually ingesting data')

args = parser.parse_args()

threads = args.nthreads
base_url = args.base_url
input_dir = args.input_dir
input_data_format = args.input_data_format
output_dir = args.output_dir
dry_run = args.dry_run

num_threads = int(threads)

if input_data_format and input_data_format.lower() == 'pcori':
    # input data is pcori data. Need to convert input data to fhir bundles first before ingestion
    mapping_pcornet_to_fhir(input_dir, output_dir, 10000)
    input_dir = output_dir

paths = [f"{root}/{file}" for root, _, files in os.walk(input_dir, followlinks=True) for file in files]


logger = getLogger(f"{__name__}{os.getpid()}", logging.INFO)

def timeit(method):
    def timed(*args, **kwargs):
        logger = getLogger(f"{__name__}{os.getpid()}", logging.INFO)
        ts = time.time()
        result = method(*args, **kwargs)
        te = time.time()
        logger.info(f"{method.__name__} args = {args} kwargs = {kwargs} {te - ts}s")
        return result
    return timed

@timeit
def handle_path(path):
    logger = getLogger(f"{__name__}{os.getpid()}", logging.INFO)

    logger.debug(f"loading {path}")
    if not dry_run:
        try:
            with open(path) as input_stream:
                obj = json.load(input_stream)
        except:
            with open(path, encoding="latin-1") as input_stream:
                obj = json.load(input_stream)


        rescs = unbundle(obj).value
        nrescs = len(rescs)
        logger.debug(f"{nrescs} resources loaded")
        maxlen = 1024
        for i in range(0, nrescs, maxlen):
            subrescs = rescs[i: min(i+maxlen, nrescs)]
            subobj = bundle(subrescs)
            logger.debug(f"ingesting {path} {i}")
            requests.post(f"{base_url}/Bundle", json=subobj)
    else:
        logger.debug(f"post {base_url}/Bundle")

with tqdm_joblib(tqdm(total=len(paths))) as progress_bar:
    Parallel(n_jobs=num_threads)(delayed(handle_path)(path) for path in paths)

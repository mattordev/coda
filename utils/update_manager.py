import requests
from tqdm import tqdm
import zipfile
import os
import shutil
import json
import semantic_version

# Updates CODA to the latest version, if available, directly from the GitHub repository. Only runs manually OR if a new version is detected
# in the start up process.


def update_program():
    repo_url = "https://github.com/mattordev/coda/archive/main.zip"

    # To be used when a release is available
    # latest_release = requests.get(project_url + "/releases/latest")
    r = requests.get(repo_url, stream=True)

    with open("coda.zip", "wb") as handle:
        for data in tqdm(r.iter_content(chunk_size=1024)):  # Use a larger chunk size
            handle.write(data)


def extract_download():
    with zipfile.ZipFile("coda.zip", 'r') as zip_ref:
        zip_ref.extractall("coda")


# needs elevated permissions to move files: https://stackoverflow.com/a/19719292
def setup_updated_program():
    # https://stackoverflow.com/a/8858026
    # Sort out file structure. main files are in coda/coda-main and need to be moved out
    os.rename('coda/coda-main', 'coda-new')
    # Move the new coda folder to the correct location
    os.replace('coda-new', 'coda')
    # Move the old coda folder to a backup location, give it a the version number.
    shutil.move('coda', 'coda-backup')
    # read current version from version.json
    with open("version.json", "r") as json_file:
        json_data = json.load(json_file)
        saved_version = semantic_version.Version(json_data['version'])
        json_file.close()
    os.rename('coda-backup', 'coda-' + saved_version)
    # Move the new coda folder to the correct location
    pass


update_program()
extract_download()
setup_updated_program()

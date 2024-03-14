import requests
from tqdm import tqdm
import zipfile

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


def setup_updated_program():
    # https://stackoverflow.com/a/8858026
    # Sort out file structure. main files are in coda/coda-main and need to be moved out
    # Move the old coda folder to a backup location
    # Move the new coda folder to the correct location
    pass

import requests
from tqdm import tqdm
import zipfile
import os
import shutil
import json
from colorama import Fore, init
import pyuac

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
    src = "coda/coda-main"
    dst = "coda-version-new"
    if not os.path.exists(dst):
        os.makedirs(dst)  # Create the directory if it doesn't exist

    for filename in os.listdir(src):
        shutil.move(os.path.join(src, filename), dst)  # Move each file

    shutil.rmtree("coda")  # delete the extracted folder
    os.remove("coda.zip")  # delete the downloaded zip file
    # Move the API key to the new version folder
    shutil.move("ELapi_key.txt", "coda-version-new/ELapi_key.txt")


def fix_version_names():
    # Get new version number from the version.json file
    with open("coda-version-new/version.json", "r") as json_file:
        json_data = json.load(json_file)
        new_version = json_data['version']
        print(f"New version: {new_version}")
    # Rename the new version folder to the new version number
    os.rename("coda-version-new", f"coda-{new_version}")
    # Get the old version number from the version.json file
    with open("version.json", "r") as json_file:
        json_data = json.load(json_file)
        old_version = json_data['version']
        print(f"Old version: {old_version}")
    # Rename the old version folder to the old version number
    cwd = os.getcwd()
    os.rename(cwd, f'coda-{old_version}')


def fix_folder_structure():
    # Define full paths
    root_path = os.getcwd()

    # Get new version number from the version.json file
    with open("coda-version-new/version.json", "r") as json_file:
        json_data = json.load(json_file)
        new_version = json_data['version']
    new_version_path = os.path.join(
        root_path, f'coda/coda-new-version-{new_version}')
    # Move contents of new version out of old version directory
    for filename in os.listdir(new_version_path):
        src = os.path.join(new_version_path, filename)
        dst = os.path.join(root_path, filename)
        shutil.move(src, dst)
    # Delete the now empty new version directory
    os.rmdir(new_version_path)


def main():
    update_program()
    extract_download()
    try:
        setup_updated_program()
    except Exception as e:
        print(f"Error setting up the updated program: {e}")
        return
    fix_version_names()
    # fix_folder_structure() # needs more work. Not currently working.
    # Automatically reset the color to the default after each print statement
    init(autoreset=True)
    print(Fore.GREEN + "Update complete!")
    print(Fore.BLUE + "Cleaning up the old version...")
    # call cleanup.py with the arg "-update"
    


if __name__ == "__main__":
    if not pyuac.isUserAdmin():
        print("Admin permissions NOT FOUND")
        print("Re-launching as admin!")
        pyuac.runAsAdmin()
    else:
        main()  # Already an admin here.

import os
import time
import re
import requests
import tarfile
import shutil
from packaging import version
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# Configuration
LOCAL_VERSION_FILE = os.path.expanduser("~/webos/Antigravity/version.txt")
INSTALL_DIR = os.path.expanduser("~/webos") # ./Antigravity
DOWNLOAD_PAGE = "https://antigravity.google/download/linux"

def get_local_version():
    """Reads the local version from the version file."""
    if os.path.exists(LOCAL_VERSION_FILE):
        try:
            with open(LOCAL_VERSION_FILE, 'r') as f:
                return f.read().strip()
        except Exception as e:
            print(f"Error reading local version file: {e}")
    return "0.0.0"

def get_remote_info():
    """
    Fetches the download page using Selenium (to handle SPA) and finds the tarball link.
    Returns (version_string, download_url).
    """
    print(f"Fetching {DOWNLOAD_PAGE}...")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(DOWNLOAD_PAGE)
        # Wait for SPA to render.
        # Ideally we wait for a specific element, but a sleep is safer if we don't know the ID.
        time.sleep(5)

        page_source = driver.page_source

        # Strategy 1: Look for <a> tags with .tar.gz
        links = driver.find_elements(By.TAG_NAME, "a")
        tarball_url = None
        for link in links:
            href = link.get_attribute("href")
            if href and ".tar.gz" in href:
                tarball_url = href
                break

        # Strategy 2: Regex search in page source if link element not found
        if not tarball_url:
            print("Link not found in <a> tags, searching page source...")
            # Look for http(s)://... .tar.gz
            match = re.search(r'(https?://[^\s"\'<>]+?\.tar\.gz)', page_source)
            if match:
                tarball_url = match.group(1)

        if not tarball_url:
            raise Exception("Could not find a .tar.gz download link on the page.")

        print(f"Found download URL: {tarball_url}")

        # Extract version from URL
        # URL example: https://edgedl.me.gvt1.com/edgedl/release2/j0qc3/antigravity/stable/1.11.17-6639170008514560/linux-x64/Antigravity.tar.gz
        # We look for a version pattern like X.Y.Z
        version_match = re.search(r'(\d+\.\d+\.\d+)', tarball_url)
        if version_match:
            remote_version = version_match.group(1)
        else:
            # Fallback: try to find version in the text of the link or nearby
            # This is hard without a specific selector.
            # Let's assume if we found the URL, we can parse the version or we fail.
            raise Exception(f"Could not extract version number from URL: {tarball_url}")

        return remote_version, tarball_url

    finally:
        driver.quit()

def download_and_extract(url, target_dir):
    """Downloads the tarball and extracts it to the target directory."""
    filename = url.split('/')[-1]
    local_tar_path = os.path.join("/tmp", filename)

    print(f"Downloading {url} to {local_tar_path}...")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(local_tar_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    print(f"Extracting to {target_dir}...")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    # Extract
    with tarfile.open(local_tar_path, "r:gz") as tar:
        tar.extractall(path=target_dir)

    print("Extraction complete.")
    os.remove(local_tar_path)

def update_version_file(new_version):
    """Updates the local version file."""
    # Ensure the directory exists (it should after extraction)
    os.makedirs(os.path.dirname(LOCAL_VERSION_FILE), exist_ok=True)
    with open(LOCAL_VERSION_FILE, 'w') as f:
        f.write(new_version)
    print(f"Updated local version file to {new_version}")

def main():
    print("--- Antigravity IDE Update Script ---")

    current_ver_str = get_local_version()
    print(f"Current local version: {current_ver_str}")

    try:
        remote_ver_str, download_url = get_remote_info()
        print(f"Latest remote version: {remote_ver_str}")

        if version.parse(remote_ver_str) > version.parse(current_ver_str):
            print("Update available. Starting update process...")
            download_and_extract(download_url, INSTALL_DIR)
            update_version_file(remote_ver_str)
            print("Update successful!")
        else:
            print("You are already on the latest version.")

    except Exception as e:
        print(f"Update failed: {e}")

if __name__ == "__main__":
    main()

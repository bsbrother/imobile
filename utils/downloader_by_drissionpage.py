import os
import sys
import re
import glob
from DrissionPage import Chromium, ChromiumOptions

def find_newest_file(pattern):
    """Return the newest file matching the pattern by modification time, or None if no files found."""

    # Get all matching files (excluding directories)
    files = [f for f in glob.glob(pattern) if os.path.isfile(f)]
    if not files:  # No matches found
        return None
    # Return file with latest modification time
    return max(files, key=os.path.getmtime)

def keep_only_newest(pattern):
    """
    Finds the newest file matching the pattern, deletes all other matches,
    and returns the newest file path. Returns None if no matches found.
    """

    newest = find_newest_file(pattern)
    for file in [f for f in glob.glob(pattern) if os.path.isfile(f)]:
        if file != newest:
            try:
                os.remove(file)
                print(f"Deleted: {file}", file=sys.stderr)
            except Exception as e:
                print(f"Error deleting {file}: {str(e)}", file=sys.stderr)
    
    return newest


def parse_apk_version(path_or_link):
    """Parse the filename & version from a path or link.

    Expected formats:
    - path: ~/Downloads/yyz_9.19.1620250524005127.19.16-25052321_gtja.apk
    - link: https://dl2.app.gtja.com/package/yyz/android/normal/9.19.16/yyz_9.19.1620250524005127.19.16-25052321_gtja.apk
    
    Returns a tuple (filename, version) where:
    - filename is the full filename (without path)
    - version is the version part (X.Y.Z format)
    Raises ValueError if the path or link does not match the expected format.
    """
    if not path_or_link:
        raise ValueError("Invalid path or link format: empty input!")
    # filename format always as yyz_n1.n2.n3.*_gtja.apk. n1, n2, n3 are 1-2 digits, 
    # ignore any other digits or characters after n3, end with _gtja.apk.
    pattern = r'(yyz_(\d{1,2}\.\d{1,2}\.\d{1,2}).*_gtja\.apk)'
    match = re.search(pattern, path_or_link)
    if not match or len(match.groups()) < 2:
        raise ValueError(f"Invalid filename or link format: {path_or_link}, must match {pattern}!")
    return match.group(1), match.group(2)  # Return full filename and version


def compare_versions(version1, version2):
    """Compare two version strings (X.Y.Z format).
    
    Returns:
        1 if version1 > version2
        0 if version1 == version2
        -1 if version1 < version2
    """
    if not version1 or not version2:
        return 0
    
    try:
        v1_parts = [int(x) for x in version1.split('.')]
        v2_parts = [int(x) for x in version2.split('.')]
        
        # Pad with zeros if needed
        while len(v1_parts) < 3:
            v1_parts.append(0)
        while len(v2_parts) < 3:
            v2_parts.append(0)
        
        for i in range(3):
            if v1_parts[i] > v2_parts[i]:
                return 1
            elif v1_parts[i] < v2_parts[i]:
                return -1
        
        return 0
    except (ValueError, IndexError):
        return 0

def insecure_legacy_download_by_drissionpae(url, save_path, output_file):
    """Download content from a URL with insecure SSL handling.  drissionpage version.

    When curl/wget url # cause error:
    OpenSSL: error:0A000152:SSL routines::unsafe legacy renegotiation disabled
    Unable to establish SSL connection.
    """
    
    pattern = os.path.join(save_path, '*yyz*_gtja.apk')
    newest_file = keep_only_newest(pattern)
    
    # Handle case where no existing files are found
    if newest_file is None:
        exist_fn, exist_version = None, None
    else:
        try:
            (exist_fn, exist_version) = parse_apk_version(newest_file)
        except ValueError:
            exist_fn, exist_version = None, None
    
    try:
        co = ChromiumOptions()
        co.ignore_certificate_errors()  # Bypass SSL errors, with insecure SSL handling
        co.set_argument('--ignore-ssl-errors')
        co.set_argument('--disable-gpu')
        co.set_argument('--no-sandbox')  # Important for headless mode
        co.headless(False)  # Set to True if running on server without display

        tab = Chromium(addr_or_opts=co).latest_tab
        tab.set.window.size(500, 300)
        tab.set.when_download_file_exists('overwrite') # rename, skip
        tab.get(url)
        # Find the download button and click it
        ele = tab('立即下载')
        ele.wait.has_rect() # ele.wait.clickable()
        tab.wait(5)
        (newest_fn, newest_version) = parse_apk_version(ele.link)
        if exist_version and compare_versions(newest_version, exist_version) <= 0:
            print(f"Current version {newest_version} is not newer than existing version {exist_version}, skipping download.")
            return True
        mission = ele.click.to_download(save_path=save_path, rename=output_file)
        mission.wait()

        print(f"Updated APK file: {os.path.join(save_path, newest_fn)}, version: {newest_version}")
        return True
    except Exception as e:
        print(f"Error during download: {e}")
        return False
    finally:
        if 'tab' in locals():
            tab.close()

if __name__ == "__main__":
    target_url = "https://app.gtht.com/jh-download/"
    save_path = os.path.expanduser('~/Downloads')  # Expand user directory
    if not os.path.exists(save_path):
        os.makedirs(save_path)
    output_file = None  # Use default filename from server

    print(f"Waiting for download {target_url} ...")

    if insecure_legacy_download_by_drissionpae(target_url, save_path, output_file):
        print("Download completed successfully")
    else:
        print("Download failed")

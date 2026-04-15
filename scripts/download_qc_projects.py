import os
import sys
import pathlib
from typing import Any, Dict, List

import requests

# Adjust these based on the exact URLs and JSON from your QC API docs:
BASE_URL = "https://www.quantconnect.com/api/v2"  # change if docs show v3, etc.

AUTH_AS_QUERY_PARAMS = True  # set False if QC docs say "use basic auth"

USER_ID_PARAM_NAME = "userId"
API_KEY_PARAM_NAME = "apiToken"

LIST_PROJECTS_PATH = "projects/read"
LIST_PROJECT_FILES_PATH = "projects/read"

PROJECTS_ARRAY_KEY = "projects"
PROJECT_ID_KEY = "projectId"
PROJECT_NAME_KEY = "name"

FILES_ARRAY_KEY = "files"
FILE_PATH_KEY = "path"
FILE_CONTENT_KEY = "content"


def get_env_or_fail(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def qc_request(method: str, path: str, user_id: str, api_key: str, *, params=None) -> Any:
    if params is None:
        params = {}

    url = f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}"

    if AUTH_AS_QUERY_PARAMS:
        params = {**params, USER_ID_PARAM_NAME: user_id, API_KEY_PARAM_NAME: api_key}
        auth = None
    else:
        auth = (user_id, api_key)

    print(f"[QC REQUEST] {method} {url} params={params}")
    resp = requests.request(method, url, params=params, auth=auth)
    if not resp.ok:
        print(f"[QC ERROR] Status {resp.status_code}: {resp.text}", file=sys.stderr)
        resp.raise_for_status()
    return resp.json()


def list_projects(user_id: str, api_key: str) -> List[Dict[str, Any]]:
    data = qc_request("GET", LIST_PROJECTS_PATH, user_id, api_key)
    projects = data.get(PROJECTS_ARRAY_KEY, [])
    print(f"Found {len(projects)} projects.")
    return projects


def get_project_files(user_id: str, api_key: str, project_id: Any) -> List[Dict[str, Any]]:
    params = {"projectId": project_id}
    data = qc_request("GET", LIST_PROJECT_FILES_PATH, user_id, api_key, params=params)
    files = data.get(FILES_ARRAY_KEY, [])
    print(f"  Found {len(files)} files for project {project_id}")
    return files


def write_project_files(project_name: str, files: List[Dict[str, Any]]) -> None:
    base_dir = pathlib.Path("quantconnect-projects") / str(project_name)
    base_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        rel_path = f.get(FILE_PATH_KEY) or f.get("name") or "unnamed"
        content = f.get(FILE_CONTENT_KEY, "")

        dest = base_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        print(f"    Wrote {dest}")


def main():
    user_id = get_env_or_fail("QC_USER_ID")
    api_key = get_env_or_fail("QC_API_KEY")

    print("Using QuantConnect credentials from Actions secrets")
    print(f"User ID: {user_id}")

    projects = list_projects(user_id, api_key)
    if not projects:
        print("No cloud projects returned by QuantConnect API.")
        return

    for proj in projects:
        project_id = proj.get(PROJECT_ID_KEY) or proj.get("id")
        project_name = proj.get(PROJECT_NAME_KEY) or f"project_{project_id}"

        if project_id is None:
            print(f"Skipping project with missing ID: {proj}", file=sys.stderr)
            continue

        print(f"Downloading project '{project_name}' (id={project_id})")
        files = get_project_files(user_id, api_key, project_id)
        write_project_files(project_name, files)

    print("Finished syncing QuantConnect cloud projects.")


if __name__ == "__main__":
    main()

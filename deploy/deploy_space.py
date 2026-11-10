"""Deploy the Streamlit app to a Hugging Face Space and wait until it is live.

Usage:
    python deploy/deploy_space.py              # deploy (needs HF_TOKEN or a cached `hf auth login`)
    python deploy/deploy_space.py --dry-run    # only stage the files and list them; no network calls

Environment:
    HF_TOKEN      Hugging Face token with write access (optional if logged in with `hf auth login`)
    HF_SPACE_ID   Target Space, e.g. "your-name/kyvo" (default: <token owner>/kyvo)

The Space gets the app files tracked in git plus deploy/space/Dockerfile and deploy/space/README.md
(the README carries the Space config header). Docs, tests, CI and the GitHub README are not uploaded.
"""
import argparse, os, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPACE_FILES = ROOT / "deploy" / "space"
EXCLUDE_PREFIXES = ("docs/", "tests/", ".github/", "deploy/")
EXCLUDE_FILES = {"README.md", ".gitignore"}
FAIL_STAGES = {"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE", "DELETING", "STOPPED", "PAUSED"}
BUILD_TIMEOUT_S = 20 * 60


def log(msg):
    print(msg, flush=True)


def stage_files(dest: Path) -> list:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split("\n")
    files = [f for f in tracked if f and not f.startswith(EXCLUDE_PREFIXES) and f not in EXCLUDE_FILES]
    for f in files:
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dest / f)
    for f in SPACE_FILES.iterdir():
        shutil.copy2(f, dest / f.name)
        files.append(f.name)
    return sorted(files)


def git_sha() -> str:
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() or "local"


def wait_until_running(api, space_id: str):
    start, seen_build, last = time.time(), False, None
    while time.time() - start < BUILD_TIMEOUT_S:
        stage = api.get_space_runtime(space_id).stage
        stage = getattr(stage, "value", stage)
        if stage != last:
            log(f"  stage: {stage}  ({int(time.time() - start)}s)")
            last = stage
        if stage in FAIL_STAGES:
            raise SystemExit(f"Space is in {stage}. Open the Space's Logs tab for details: https://huggingface.co/spaces/{space_id}")
        if stage in ("BUILDING", "APP_STARTING", "RUNNING_BUILDING", "RUNNING_APP_STARTING"):
            seen_build = True
        # Right after an upload the old build can still report RUNNING, so only trust RUNNING once a
        # rebuild has been observed, or after 90 s if none ever showed up.
        if stage == "RUNNING" and (seen_build or time.time() - start > 90):
            return
        time.sleep(10)
    raise SystemExit(f"Timed out after {BUILD_TIMEOUT_S // 60} minutes waiting for the Space to run.")


def health_check(url: str):
    for attempt in range(12):
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/_stcore/health", timeout=15) as r:
                body = r.read().decode().strip()
                if r.status == 200 and body == "ok":
                    log(f"  health check: ok")
                    return
        except Exception as e:  # app may still be warming up
            body = str(e)
        log(f"  health check attempt {attempt + 1}: {body[:80]}")
        time.sleep(10)
    raise SystemExit("The Space reports RUNNING but the Streamlit health endpoint did not answer 'ok'.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="stage files and list them without contacting Hugging Face")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="kyvo-space-") as tmp:
        files = stage_files(Path(tmp))
        log(f"Staged {len(files)} files for the Space:")
        for f in files:
            log(f"  {f}")
        if args.dry_run:
            log("Dry run: nothing uploaded.")
            return

        from huggingface_hub import HfApi
        api = HfApi(token=os.environ.get("HF_TOKEN") or None)
        try:
            user = api.whoami()["name"]
        except Exception:
            raise SystemExit("Not authenticated with Hugging Face. Set HF_TOKEN (a write token) or run `hf auth login`.")
        space_id = os.environ.get("HF_SPACE_ID") or f"{user}/kyvo"
        log(f"Deploying to Space {space_id} as {user}")

        api.create_repo(space_id, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
        commit = api.upload_folder(repo_id=space_id, repo_type="space", folder_path=tmp,
                                   delete_patterns="*", commit_message=f"Deploy {git_sha()} from GitHub")
        log(f"Uploaded: {commit.commit_url}")

    log("Waiting for the Space to build and start...")
    wait_until_running(api, space_id)
    info = api.space_info(space_id)
    url = info.host or f"https://{info.subdomain}.hf.space"
    health_check(url)

    log(f"\nLive demo: {url}\nSpace page: https://huggingface.co/spaces/{space_id}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Deployed to Hugging Face\n\n- Live demo: {url}\n- Space: https://huggingface.co/spaces/{space_id}\n")


if __name__ == "__main__":
    sys.exit(main())

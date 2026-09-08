"""
Times a single large-file import from upload to COMPLETED status
(assignment section 26: "processing of a large transaction file").

Usage:
    python load_test/time_large_import.py --file load_test/large_transactions.csv \
        --host http://localhost:8000 --api-key <key>
"""

import argparse
import time

import httpx


def main(host: str, api_key: str, file_path: str) -> None:
    headers = {"X-API-Key": api_key}

    t0 = time.perf_counter()
    with open(file_path, "rb") as f:
        resp = httpx.post(f"{host}/api/v1/imports", headers=headers, files={"file": (file_path, f, "text/csv")}, timeout=120)
    resp.raise_for_status()
    upload_elapsed = time.perf_counter() - t0
    import_id = resp.json()["import_id"]
    print(f"Upload accepted in {upload_elapsed:.2f}s, import_id={import_id}")

    t_process_start = time.perf_counter()
    while True:
        status_resp = httpx.get(f"{host}/api/v1/imports/{import_id}", headers=headers, timeout=30)
        body = status_resp.json()
        print(
            f"\r{body['status']} processed={body['processed_rows']} "
            f"success={body['successful_rows']} failed={body['failed_rows']}   ",
            end="",
            flush=True,
        )
        if body["status"] in ("COMPLETED", "FAILED"):
            print()
            break
        time.sleep(1)

    processing_elapsed = time.perf_counter() - t_process_start
    total_rows = body["total_rows"] or 0
    print(f"Processing took {processing_elapsed:.2f}s for {total_rows} rows "
          f"({total_rows / processing_elapsed:.0f} rows/sec)" if processing_elapsed > 0 else "")
    print(f"Total (upload + processing): {upload_elapsed + processing_elapsed:.2f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--api-key", required=True)
    args = parser.parse_args()
    main(args.host, args.api_key, args.file)

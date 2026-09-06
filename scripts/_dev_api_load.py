"""Closed-loop load driver for the API (work/work_API_standby.md, Step 4 verification).

Sends /acquire as fast as the server answers - each request goes out only once the
previous response has come back - which is the sending model the paired client uses
(方針11). That model is what makes the UI-lock debounce work out safely: the gap
between requests is the client's own turnaround plus the network round trip, so it
does not grow with exposure time and the unlock timer never fires mid-burst.

Reports the achieved rate and the latency distribution. Read the rate as a *relative*
number: compare it against the GUI's own continuous measurement at the same exposure
and accumulation count, and the difference is what the API layer costs. The absolute
figure says more about the camera than about this code.

Usage (with the app already running in Standby or Locked mode):

    python scripts/_dev_api_load.py --key <API key> [--port 8765] [--seconds 60]
"""
import argparse
import json
import statistics
import time
import urllib.error
import urllib.request


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--key", required=True, help="X-API-Key of an authorised client")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--exposure", type=float, default=None)
    parser.add_argument("--accumulations", type=int, default=None)
    return parser.parse_args()


def post_acquire(url, key, body, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST"
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("X-API-Key", key)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            return response.status, time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, time.perf_counter() - started
    except urllib.error.URLError as exc:
        print(f"connection error: {exc}")
        return -1, time.perf_counter() - started


def main():
    args = parse_args()
    url = f"http://{args.host}:{args.port}/acquire"
    body = {}
    if args.exposure is not None:
        body["exposure_time_s"] = args.exposure
    if args.accumulations is not None:
        body["accumulations"] = args.accumulations

    durations = []
    statuses = {}
    deadline = time.perf_counter() + args.seconds
    started = time.perf_counter()
    while time.perf_counter() < deadline:
        status, elapsed = post_acquire(url, args.key, body, timeout=args.seconds)
        durations.append(elapsed)
        statuses[status] = statuses.get(status, 0) + 1
        if status == -1:
            break
    total = time.perf_counter() - started

    print(f"\nrequests: {len(durations)} in {total:.1f} s "
          f"-> {len(durations) / total:.2f} req/s")
    print(f"statuses: {statuses}")
    if durations:
        ordered = sorted(durations)
        def percentile(fraction):
            return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]
        print(f"latency  : min {ordered[0] * 1000:.0f} ms | "
              f"median {statistics.median(ordered) * 1000:.0f} ms | "
              f"p90 {percentile(0.9) * 1000:.0f} ms | "
              f"max {ordered[-1] * 1000:.0f} ms")
    # Closed-loop cannot collide with itself, so anything other than 200 is a real
    # finding rather than a client-side scheduling artefact.
    if set(statuses) - {200}:
        print("NOT OK: some requests did not return 200")
    else:
        print("OK: every request returned 200")


if __name__ == "__main__":
    main()

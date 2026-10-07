#!/usr/bin/env python3
"""
ICAI batch seat watcher
-----------------------
Checks https://www.icaionlineregistration.org/launchbatchdetail.aspx every
~5 minutes for the batches you care about and alerts you the moment any batch
shows Available Seats > 0.

It only WATCHES and NOTIFIES. You still book the seat yourself (log in to your
ICAI student dashboard in advance so you can register in seconds).

Setup (once):
    pip install playwright requests
    playwright install chromium

Test run (opens a visible browser so you can see it working):
    python icai_batch_watcher.py --show --once

Normal run (leave the terminal open):
    python icai_batch_watcher.py

Phone alerts (optional, free): install the "ntfy" app, subscribe to a private
topic name of your choice, then set it before running:
    Windows PowerShell:  $env:NTFY_TOPIC="my-private-icai-topic"
    Windows cmd:         set NTFY_TOPIC=my-private-icai-topic
    Mac/Linux:           export NTFY_TOPIC=my-private-icai-topic

Examples:
    python icai_batch_watcher.py --pou CHENNAI
    python icai_batch_watcher.py --pou "CHENNAI,COIMBATORE" --course MCS
    python icai_batch_watcher.py --course ITT        # Advanced ITT instead
"""

import argparse
import os
import random
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

URL = "https://www.icaionlineregistration.org/launchbatchdetail.aspx"


def log(msg):
    print(f"[{datetime.now():%d-%b %H:%M:%S}] {msg}", flush=True)


def notify(title, msg):
    """Beep in the terminal and, if NTFY_TOPIC is set, push to your phone."""
    print("\a", end="", flush=True)
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=msg.encode("utf-8"),
            headers={"Title": title, "Priority": "urgent", "Tags": "rotating_light"},
            timeout=15,
        )
    except Exception as exc:  # never let a failed push stop the watcher
        log(f"Phone notification failed: {exc}")


def pick(select, text):
    """Select the first option whose label contains `text` (case-insensitive)."""
    options = select.locator("option")
    for i in range(options.count()):
        label = options.nth(i).inner_text().strip()
        if text.lower() in label.lower():
            select.select_option(index=i)
            return True
    return False


def fetch_batches(browser, region, pou, course):
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(URL, wait_until="networkidle", timeout=60000)

        # Dropdowns are in this order on the page: Region, Pou, Course.
        # Later dropdowns may fill in only after the earlier one is chosen,
        # so wait/retry until the wanted option appears.
        for idx, wanted in ((0, region), (1, pou), (2, course)):
            deadline = time.time() + 20
            while not pick(page.locator("select").nth(idx), wanted):
                if time.time() > deadline:
                    raise RuntimeError(f"Option '{wanted}' not found in dropdown #{idx + 1}")
                page.wait_for_timeout(500)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)

        page.locator("input[value*='Get List' i], button:has-text('Get List')").first.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)

        rows = []
        for tr in page.locator("tr").all():
            cells = [c.strip() for c in tr.locator("td").all_inner_texts()]
            if len(cells) >= 8 and cells[1].isdigit():
                rows.append(
                    {
                        "batch": cells[0],
                        "seats": int(cells[1]),
                        "start": cells[2],
                        "end": cells[3],
                        "time": " ".join(cells[4].split()),
                        "pou": cells[5],
                        "course": " ".join(cells[6].split()),
                        "open_for": cells[7],
                    }
                )
        if not rows:
            page.screenshot(path="no_rows.png", full_page=True)
        return rows
    except Exception:
        try:
            page.screenshot(path="error.png", full_page=True)
        except Exception:
            pass
        raise
    finally:
        context.close()


def main():
    ap = argparse.ArgumentParser(description="Watch ICAI batch seats")
    ap.add_argument("--region", default="Southern")
    ap.add_argument("--pou", default="CHENNAI", help="one or more, comma separated")
    ap.add_argument("--course", default="MCS", help="text in course name: MCS or ITT")
    ap.add_argument("--interval", type=int, default=300, help="seconds between checks")
    ap.add_argument("--show", action="store_true", help="show the browser window")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--test", action="store_true", help="send a test alert and exit")
    args = ap.parse_args()

    if args.test:
        notify("ICAI watcher test", "Test alert: your phone notifications work.")
        log("Test alert sent.")
        return

    pous = [p.strip() for p in args.pou.split(",") if p.strip()]
    last_seats = {}  # batch -> seats seen last time (to avoid repeat alerts)

    log(f"Watching {args.course} in {', '.join(pous)} ({args.region}) every {args.interval}s. Ctrl+C to stop.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        while True:
            for pou in pous:
                try:
                    rows = fetch_batches(browser, args.region, pou, args.course)
                except Exception as exc:
                    log(f"[{pou}] check failed: {exc}")
                    continue

                if not rows:
                    log(f"[{pou}] no batches listed (see no_rows.png).")
                    continue

                open_batches = [r for r in rows if r["seats"] > 0]
                log(f"[{pou}] {len(rows)} batches, {len(open_batches)} with seats.")

                for r in open_batches:
                    if last_seats.get(r["batch"]) != r["seats"]:
                        msg = (
                            f"{r['seats']} seat(s) open: {r['batch']} | "
                            f"{r['start']} to {r['end']} | {r['time']} | {r['open_for']}"
                        )
                        log("*** SEATS AVAILABLE *** " + msg)
                        notify("ICAI seats open - book now!", msg)
                for r in rows:
                    last_seats[r["batch"]] = r["seats"]

            if args.once:
                break
            time.sleep(args.interval + random.randint(0, 20))
        browser.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")

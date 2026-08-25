import sys, os, time, ssl
from dotenv import load_dotenv
load_dotenv()

import json, jq
from dataclasses import dataclass, asdict
import logging
from rich.logging import RichHandler
from rich import print

FORMAT = "%(message)s"
logging.basicConfig(
    level="NOTSET", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()]
)

@dataclass
class Device:
    id: str
    name: str
    connectedToControl: bool
    notified: bool = False
    checksUntilNotify: int = int(os.getenv("REPORT_IF_MISSING_AFTER")) if os.getenv("REPORT_IF_MISSING_AFTER") else 5

import http.client
ts_headers: dict[str, str] = {
  "Authorization": f"Bearer {os.getenv("TAILSCALE_API_KEY")}"
}

def tailscale_get_status() -> list[Device]:
    tailscale = http.client.HTTPSConnection("api.tailscale.com")
    try:
        tailscale.request(
        "GET",
        "/api/v2/tailnet/-/devices",
        headers=ts_headers,
        )      

        response: http.client.HTTPResponse = tailscale.getresponse()
        body = response.read().decode()
    finally:
        tailscale.close()

    if response.status != 200:
        raise RuntimeError(f"Tailscale API returned {response.status}: {body[:200]}")

    rule = jq.compile('.devices[] | select(.tags != null and (.tags| any(. == "tag:\\($tagname)"))) | {id, name, connectedToControl}', args={"tagname": os.getenv("TAILSCALE_TAG")})
    data = json.loads(body)
    result = [Device(**item) for item in rule.input_value(data).all()]

    return result

ntfy_server: str = (os.getenv("NTFY_SERVER") or "ntfy.sh").removeprefix("https://").removesuffix("/")
ntfy_headers: dict[str, str] = {
      "Authorization": f"Bearer {os.getenv("NTFY_API_KEY")}",
      "Title": "Camera Offline!",
      "Priority": "High"
}
if os.getenv("NTFY_INSECURE") == "true":
    ntfy_context = ssl.create_default_context()
    ntfy_context.check_hostname = False
    ntfy_context.verify_mode = ssl.CERT_NONE
else:
    ntfy_context = ssl.create_default_context()

def notifyOfDeadDevice(name: str) -> None:
    ntfy = http.client.HTTPSConnection(ntfy_server, context=ntfy_context)
    try:
        ntfy.request(
        "POST",
        f"/{os.getenv("NTFY_CHANNEL")}",
        headers=ntfy_headers,
        body=f"{name} has been offline for several check cycles."
        )      

        response: http.client.HTTPResponse = ntfy.getresponse()
        response.read()
    except Exception:
        logging.exception(f"Failed to send notification for {name}")
        return
    finally:
        ntfy.close()

    if response.status == 200:
        logging.info(f"Notification sent successfully! Node is {name}")
    else:
        logging.error(f"Failed to send notification: {response.status} {response.reason}")

def main() -> None:
    logging.info("Fetching initial status")
    last_state = tailscale_get_status()
    logging.debug(json.dumps([asdict(device) for device in last_state], indent=2))

    timeToSleep: int = int(os.getenv("CHECK_FREQUENCY")) if os.getenv("CHECK_FREQUENCY") else 30
    checksUntilNotify: int = int(os.getenv("REPORT_IF_MISSING_AFTER")) if os.getenv("REPORT_IF_MISSING_AFTER") else 3
    while True:
        logging.info(f"Sleeping {timeToSleep}s...")
        time.sleep(timeToSleep)
        logging.info("Fetching currnet state...")
        state = tailscale_get_status()
        previous = {device.id: device for device in last_state}
        for device in state:
            old = previous.get(device.id)
            if old:
                device.checksUntilNotify = old.checksUntilNotify
                device.notified = old.notified
            if not device.notified:
                if device.checksUntilNotify == 0:
                    notifyOfDeadDevice(device.name)
                    device.notified = True
                if not device.connectedToControl:
                    device.checksUntilNotify -= 1
                if device.connectedToControl and device.checksUntilNotify != checksUntilNotify:
                    device.checksUntilNotify = checksUntilNotify
            else:
                if device.connectedToControl:
                    device.checksUntilNotify = checksUntilNotify
        logging.debug(json.dumps([asdict(device) for device in state], indent=2))
        last_state: list[Device] = state

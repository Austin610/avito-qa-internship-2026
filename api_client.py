"""Small standard-library client; never logs credentials or bearer tokens."""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Response:
    status: int
    body: object
    content_type: str


class Client:
    def __init__(self):
        self.base_url = os.getenv("BASE_URL", "https://qa-internship.avito.com").rstrip("/")
        self.timeout = float(os.getenv("API_TIMEOUT", "20"))
        self.token = None

    def request(self, method, path, body=None, authenticated=True):
        headers = {"Accept": "application/json"}
        if authenticated and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read().decode("utf-8")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw
            return Response(response.status, parsed, response.headers.get("Content-Type", ""))

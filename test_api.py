"""Case IDs correspond to TESTCASES.md. Run: python -m unittest -v."""

import http.client
import re
import secrets
import unittest
import urllib.error
import uuid

from api_client import Client


UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.item_ids = []
        self.addCleanup(self.cleanup_items)

    def cleanup_items(self):
        failures = []
        for item_id in self.item_ids:
            # Cleanup DELETE is idempotent: retry a transport failure once. Never retry
            # registration/create or the operation under test, nor HTTP error responses.
            for attempt in range(2):
                try:
                    response = self.client.request("DELETE", f"/api/2/item/{item_id}")
                    if response.status not in (200, 404):
                        failures.append(f"{item_id}: HTTP {response.status}")
                    break
                except (http.client.RemoteDisconnected, urllib.error.URLError, TimeoutError) as error:
                    if attempt == 1:
                        failures.append(f"{item_id}: {type(error).__name__}")
                except Exception as error:
                    failures.append(f"{item_id}: {type(error).__name__}")
                    break
        self.assertFalse(failures, f"Cleanup failed; remaining test ads: {failures}")

    def expect_status(self, response, expected):
        # Response bodies are deliberately excluded: auth errors may echo secrets.
        self.assertEqual(response.status, expected, f"Expected HTTP {expected}, got {response.status}")

    def credentials(self):
        return {"username": "student_" + uuid.uuid4().hex, "password": secrets.token_urlsafe(24)}

    def register(self, credentials):
        response = self.client.request("POST", "/api/1/register", credentials, authenticated=False)
        self.expect_status(response, 201)
        self.assertTrue(isinstance(response.body, dict), "Expected registration JSON object (body redacted)")
        self.assertIs(type(response.body.get("id")), int)
        self.assertEqual(response.body.get("username"), credentials["username"])
        self.assertFalse("password" in response.body, "Registration response must not contain password")
        return response.body["id"]

    def account(self):
        credentials = self.credentials()
        self.seller_id = self.register(credentials)
        response = self.client.request("POST", "/api/1/autorize", credentials, authenticated=False)
        self.expect_status(response, 200)
        self.assertTrue(isinstance(response.body, dict), "Expected authorization JSON object (body redacted)")
        self.assertTrue(response.body.get("tokenType") == "Bearer", "Expected tokenType=Bearer")
        token = response.body.get("accessToken")
        self.assertTrue(isinstance(token, str) and bool(token.strip()), "Missing bearer token")
        self.assertFalse("password" in response.body, "Authorization response must not contain password")
        self.client.token = token
        return credentials

    def payload(self, **changes):
        data = {"sellerID": self.seller_id, "name": "QA_" + uuid.uuid4().hex,
                "price": 9900, "statistics": {"likes": 21, "viewCount": 11, "contacts": 43}}
        data.update(changes)
        return data

    def create(self, payload):
        response = self.client.request("POST", "/api/1/item", payload)
        # Track any returned UUID before assertions so malformed success responses can be cleaned up.
        match = UUID_PATTERN.search(str(response.body))
        if match:
            self.item_ids.append(match.group())
        self.expect_status(response, 200)
        self.assertIsInstance(response.body, dict)
        self.assertIsNotNone(match, "Create response must contain the new ad UUID")
        self.assertEqual(str(uuid.UUID(match.group())), match.group().lower())
        return match.group()

    def read(self, item_id):
        response = self.client.request("GET", f"/api/1/item/{item_id}")
        self.expect_status(response, 200)
        self.assertIn("application/json", response.content_type)
        self.assertIsInstance(response.body, list)
        self.assertEqual(len(response.body), 1)
        self.assertIsInstance(response.body[0], dict)
        return response.body[0]

    def assert_item(self, actual, expected, item_id):
        self.assertEqual(actual.get("id"), item_id)
        self.assertEqual(actual.get("sellerId"), expected["sellerID"], "Response sellerId must match request sellerID")
        for field in ("name", "price"):
            with self.subTest(field=field):
                self.assertEqual(actual.get(field), expected[field])

    def test_TC01_registration(self):
        self.register(self.credentials())

    def test_TC02_duplicate_registration(self):
        credentials = self.credentials()
        self.register(credentials)
        self.expect_status(self.client.request("POST", "/api/1/register", credentials, False), 409)

    def test_TC03_authorization(self):
        self.account()

    def test_TC04_wrong_password(self):
        credentials = self.credentials()
        self.register(credentials)
        credentials["password"] = secrets.token_urlsafe(24)
        self.expect_status(self.client.request("POST", "/api/1/autorize", credentials, False), 401)

    def test_TC05_unknown_user(self):
        self.expect_status(self.client.request("POST", "/api/1/autorize", self.credentials(), False), 401)

    def test_TC08_create_read(self):
        self.account()
        payload = self.payload()
        item_id = self.create(payload)
        self.assert_item(self.read(item_id), payload, item_id)

    def test_TC09_empty_seller(self):
        self.account()
        response = self.client.request("GET", f"/api/1/{self.seller_id}/item")
        self.expect_status(response, 200)
        self.assertEqual(response.body, [])

    def test_TC10_seller_list(self):
        self.account()
        payloads = [self.payload(), self.payload(price=12000)]
        ids = [self.create(payload) for payload in payloads]
        response = self.client.request("GET", f"/api/1/{self.seller_id}/item")
        self.expect_status(response, 200)
        self.assertIsInstance(response.body, list)
        self.assertEqual(len(response.body), 2)
        self.assertEqual({item["id"] for item in response.body}, set(ids))
        for item_id, payload in zip(ids, payloads):
            self.assert_item(next(item for item in response.body if item["id"] == item_id), payload, item_id)

    def test_TC11_duplicate_payload_unique_ids(self):
        self.account()
        payload = self.payload()
        first, second = self.create(payload), self.create(payload)
        self.assertNotEqual(first, second)
        self.assert_item(self.read(first), payload, first)
        self.assert_item(self.read(second), payload, second)

    def statistics(self, version):
        self.account()
        payload = self.payload()
        item_id = self.create(payload)
        response = self.client.request("GET", f"/api/{version}/statistic/{item_id}")
        self.expect_status(response, 200)
        self.assertIsInstance(response.body, list)
        self.assertEqual(len(response.body), 1)
        for field in ("likes", "contacts"):
            self.assertEqual(response.body[0].get(field), payload["statistics"][field], field)
        # No ad GET preceded this request, so a view increment is not assumed.
        self.assertEqual(response.body[0].get("viewCount"), payload["statistics"]["viewCount"])

    def test_TC12_statistics_v1(self):
        self.statistics(1)

    def test_TC13_statistics_v2(self):
        self.statistics(2)

    def test_TC14_delete(self):
        self.account()
        item_id = self.create(self.payload())
        self.expect_status(self.client.request("DELETE", f"/api/2/item/{item_id}"), 200)
        self.expect_status(self.client.request("GET", f"/api/1/item/{item_id}"), 404)
        response = self.client.request("GET", f"/api/1/{self.seller_id}/item")
        self.expect_status(response, 200)
        self.assertEqual(response.body, [])

    def test_TC15_repeat_delete(self):
        self.account()
        item_id = self.create(self.payload())
        self.expect_status(self.client.request("DELETE", f"/api/2/item/{item_id}"), 200)
        self.expect_status(self.client.request("DELETE", f"/api/2/item/{item_id}"), 404)

    def test_TC18_unicode_roundtrip(self):
        self.account()
        payload = self.payload(name="Диван QA — café 🛋 " + uuid.uuid4().hex)
        item_id = self.create(payload)
        self.assert_item(self.read(item_id), payload, item_id)

    def test_TC19_minimum_positive_price(self):
        self.account()
        payload = self.payload(price=1)
        item_id = self.create(payload)
        self.assert_item(self.read(item_id), payload, item_id)

    def test_TC20_zero_statistics(self):
        self.account()
        payload = self.payload(statistics={"likes": 0, "viewCount": 0, "contacts": 0})
        item_id = self.create(payload)
        for version in (1, 2):
            with self.subTest(version=version):
                response = self.client.request("GET", f"/api/{version}/statistic/{item_id}")
                self.expect_status(response, 200)
                self.assertEqual(response.body, [payload["statistics"]])

    def test_TC21_e2e(self):
        self.account()
        payload = self.payload()
        item_id = self.create(payload)
        self.assert_item(self.read(item_id), payload, item_id)
        response = self.client.request("GET", f"/api/1/statistic/{item_id}")
        self.expect_status(response, 200)
        self.assertIsInstance(response.body, list)
        self.assertEqual(len(response.body), 1)
        self.assertTrue(all(type(response.body[0].get(key)) is int and response.body[0][key] >= 0
                            for key in ("likes", "viewCount", "contacts")))
        self.expect_status(self.client.request("DELETE", f"/api/2/item/{item_id}"), 200)
        self.expect_status(self.client.request("GET", f"/api/1/item/{item_id}"), 404)


def invalid_credentials(endpoint, field, value):
    def test(self):
        credentials = self.credentials()
        if value is None:
            del credentials[field]
        else:
            credentials[field] = value
        self.expect_status(self.client.request("POST", endpoint, credentials, False), 400)
    return test


for case, endpoint in (("TC06", "/api/1/register"), ("TC07", "/api/1/autorize")):
    for field in ("username", "password"):
        for label, value in (("missing", None), ("empty", "")):
            setattr(ApiTests, f"test_{case}_{field}_{label}", invalid_credentials(endpoint, field, value))


def unknown_item(path):
    def test(self):
        self.account()
        self.expect_status(self.client.request("GET", path.format(id=uuid.uuid4())), 404)
    return test


for label, path in (("item", "/api/1/item/{id}"), ("stats_v1", "/api/1/statistic/{id}"),
                    ("stats_v2", "/api/2/statistic/{id}")):
    setattr(ApiTests, f"test_TC16_unknown_{label}", unknown_item(path))


def missing_auth(method, path):
    def test(self):
        self.account()
        payload = self.payload()
        item_id = self.create(payload)
        response = self.client.request(method, path.format(id=item_id, seller=self.seller_id),
                                       payload if method == "POST" else None, authenticated=False)
        self.expect_status(response, 401)
    return test


for label, method, path in (
    ("create", "POST", "/api/1/item"), ("read", "GET", "/api/1/item/{id}"),
    ("seller", "GET", "/api/1/{seller}/item"), ("stats_v1", "GET", "/api/1/statistic/{id}"),
    ("stats_v2", "GET", "/api/2/statistic/{id}"), ("delete", "DELETE", "/api/2/item/{id}"),
):
    setattr(ApiTests, f"test_TC17_missing_auth_{label}", missing_auth(method, path))


if __name__ == "__main__":
    unittest.main(verbosity=2)

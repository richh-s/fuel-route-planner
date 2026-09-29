from django.test import SimpleTestCase
from django.urls import reverse


class ApiDocsTests(SimpleTestCase):
    def test_openapi_schema_describes_trip_endpoint(self):
        response = self.client.get(reverse("schema"), HTTP_ACCEPT="application/vnd.oai.openapi+json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        operations = schema["paths"]["/api/v1/route/"]
        self.assertEqual(set(operations), {"get", "post"})
        self.assertIn("422", operations["get"]["responses"])

    def test_swagger_ui_is_served(self):
        self.assertEqual(self.client.get(reverse("swagger-ui")).status_code, 200)

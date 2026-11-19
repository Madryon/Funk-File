import os
import unittest
from unittest.mock import patch
from app import app, get_database_url


class DatabaseConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_health_endpoint_includes_database_status(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("database", data)
        self.assertTrue(data["database"]["connected"])
        self.assertEqual(data["database"]["status"], "healthy")
        self.assertIn("dialect", data["database"])

    @patch.dict(os.environ, {}, clear=True)
    def test_fallback_sqlite(self):
        url = get_database_url()
        self.assertTrue(url.startswith("sqlite:///"))
        self.assertIn("app.db", url)

    @patch.dict(os.environ, {"DATABASE_URL": "postgres://avnadmin:pass123@pg-test.aivencloud.com:21356/defaultdb"}, clear=True)
    def test_postgres_protocol_normalization_and_ssl_enforcement(self):
        url = get_database_url()
        self.assertTrue(url.startswith("postgresql+psycopg2://"))
        self.assertIn("sslmode=require", url)
        self.assertIn("pg-test.aivencloud.com:21356/defaultdb", url)

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://avnadmin:pass123@pg-test.aivencloud.com:21356/defaultdb?sslmode=verify-full"}, clear=True)
    def test_preserves_existing_sslmode(self):
        url = get_database_url()
        self.assertTrue(url.startswith("postgresql+psycopg2://"))
        self.assertIn("sslmode=verify-full", url)
        self.assertNotIn("sslmode=require", url)

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://localuser:localpass@127.0.0.1:5432/testdb"}, clear=True)
    def test_localhost_does_not_force_ssl(self):
        url = get_database_url()
        self.assertTrue(url.startswith("postgresql+psycopg2://"))
        self.assertNotIn("sslmode=require", url)

    @patch.dict(
        os.environ,
        {
            "PGHOST": "pg-cluster.aivencloud.com",
            "PGPORT": "21356",
            "PGUSER": "avnadmin",
            "PGPASSWORD": "p@ss/word#123",
            "PGDATABASE": "defaultdb",
            "PGSSLMODE": "require",
        },
        clear=True,
    )
    def test_discrete_pg_env_vars_and_password_encoding(self):
        url = get_database_url()
        self.assertTrue(url.startswith("postgresql+psycopg2://"))
        # Password special characters should be percent-encoded
        self.assertIn("p%40ss%2Fword%23123", url)
        self.assertIn("pg-cluster.aivencloud.com:21356/defaultdb", url)
        self.assertIn("sslmode=require", url)


    def test_homepage_resilient_to_database_down(self):
        from app import invalidate_site_status_cache
        invalidate_site_status_cache()
        with app.app_context():
            with patch("models.SiteSetting.query") as mock_query:
                mock_query.filter_by.side_effect = Exception("DB Connection refused")
                res = self.client.get("/")
                self.assertEqual(res.status_code, 200)
                self.assertIn(b"Funk Files", res.data)

                invalidate_site_status_cache()
                res_status = self.client.get("/api/site-status")
                self.assertEqual(res_status.status_code, 200)
                data = res_status.get_json()
                self.assertFalse(data["announcement"]["active"])
                self.assertFalse(data["maintenance"]["active"])


if __name__ == "__main__":
    unittest.main()

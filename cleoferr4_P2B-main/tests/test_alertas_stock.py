import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import app, get_connection_tienda


class TestAlertasStockConsistency:
    """Tests to ensure alert count matches products with low stock."""

    def setup_method(self):
        self.conn = get_connection_tienda()
        self.cursor = self.conn.cursor(dictionary=True)

    def teardown_method(self):
        self.conn.close()

    def test_alertas_count_matches_bajo_stock_count(self):
        """Test that the dashboard alertas count matches the productos con stock < 15 count."""
        with app.test_client() as client:
            # Get the alertas count from the dashboard route logic
            cursor = self.conn.cursor(dictionary=True)

            # Query used in productos() route (updated): count unresolved stock alerts
            cursor.execute("""
                SELECT COUNT(*) AS cnt FROM alerta
                WHERE resuelta = FALSE AND id_tipo_alerta = 1
                AND id_producto IN (SELECT id_producto FROM producto WHERE stock < 15)
            """)
            alertas_count = cursor.fetchone()['cnt']

            # Query for productos con stock < 15
            cursor.execute("""
                SELECT COUNT(*) AS cnt FROM producto WHERE stock < 15
            """)
            bajo_stock_count = cursor.fetchone()['cnt']

            # Both counts should be equal
            assert alertas_count == bajo_stock_count, (
                f"Mismatch: alertas_count={alertas_count} vs bajo_stock_count={bajo_stock_count}"
            )

            # Verify expected values (with fixture data: 3 products with stock < 15)
            assert alertas_count == 3, (
                f"Expected 3 alerts/products with low stock, got {alertas_count}"
            )

    def test_no_stale_alerts_for_products_above_stock_threshold(self):
        """Test that there are no unresolved alerts for products with stock >= 15."""
        self.cursor.execute("""
            SELECT COUNT(*) AS cnt FROM alerta
            WHERE resuelta = FALSE AND id_tipo_alerta = 1
            AND id_producto NOT IN (SELECT id_producto FROM producto WHERE stock < 15)
        """)
        stale_count = self.cursor.fetchone()['cnt']

        assert stale_count == 0, (
            f"Found {stale_count} stale alerts for products with stock >= 15"
        )

    def test_all_unresolved_alerts_are_stock_bajo_type(self):
        """Test that all unresolved alerts are of type stock_bajo (id_tipo_alerta = 1)."""
        self.cursor.execute("""
            SELECT COUNT(*) AS cnt FROM alerta
            WHERE resuelta = FALSE AND id_tipo_alerta != 1
        """)
        non_stock_alerts = self.cursor.fetchone()['cnt']

        assert non_stock_alerts == 0, (
            f"Found {non_stock_alerts} non-stock alerts in unresolved"
        )
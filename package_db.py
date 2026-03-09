"""
package_db.py — DeliveryIQ Package Storage
===========================================
Provides the Package dataclass and a SQLite-backed PackageDB for persistent
package management.  Packages represent physical parcels to be delivered;
they carry a weight, a delivery address, and a delivery status.

Usage:
    from package_db import Package, PackageDB, DeliveryStatus

    db = PackageDB()                          # opens/creates packages.db
    pkg = db.add_package("Shevchenka 1", 2.5) # returns Package
    db.set_status(pkg.id, DeliveryStatus.IN_TRANSIT)
    pending = db.get_by_status(DeliveryStatus.PENDING)
"""

from __future__ import annotations

import sqlite3
import uuid
import datetime
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
#  STATUS ENUM
# ──────────────────────────────────────────────────────────────────────────────

class DeliveryStatus(str, Enum):
    PENDING     = "pending"       # Waiting to be dispatched
    IN_TRANSIT  = "in_transit"    # Currently being delivered
    DELIVERED   = "delivered"     # Successfully delivered


# ──────────────────────────────────────────────────────────────────────────────
#  PACKAGE DATACLASS
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Package:
    """A parcel to be delivered."""
    id:         str                      # UUID4 hex string
    address:    str                      # Delivery address (human-readable)
    weight_kg:  float                    # Weight in kilograms
    status:     DeliveryStatus           # Current delivery status
    created_at: datetime.datetime        # UTC creation timestamp
    lat:        Optional[float] = None   # Geocoded latitude  (set on dispatch)
    lon:        Optional[float] = None   # Geocoded longitude (set on dispatch)

    @property
    def status_label(self) -> str:
        labels = {
            DeliveryStatus.PENDING:    "⏳ Pending",
            DeliveryStatus.IN_TRANSIT: "🚚 In Transit",
            DeliveryStatus.DELIVERED:  "✅ Delivered",
        }
        return labels.get(self.status, self.status.value)

    @property
    def status_color(self) -> str:
        colors = {
            DeliveryStatus.PENDING:    "#f59e0b",
            DeliveryStatus.IN_TRANSIT: "#3b82f6",
            DeliveryStatus.DELIVERED:  "#10b981",
        }
        return colors.get(self.status, "#6b7280")


# ──────────────────────────────────────────────────────────────────────────────
#  DATABASE
# ──────────────────────────────────────────────────────────────────────────────

_DB_PATH = Path(__file__).parent / "packages.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS packages (
    id          TEXT    PRIMARY KEY,
    address     TEXT    NOT NULL,
    weight_kg   REAL    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL,
    lat         REAL,
    lon         REAL
);
"""


class PackageDB:
    """Thin wrapper around a SQLite database for Package persistence."""

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._path = db_path
        self._bootstrap()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    @staticmethod
    def _row_to_package(row: sqlite3.Row) -> Package:
        return Package(
            id=row["id"],
            address=row["address"],
            weight_kg=float(row["weight_kg"]),
            status=DeliveryStatus(row["status"]),
            created_at=datetime.datetime.fromisoformat(row["created_at"]),
            lat=float(row["lat"]) if row["lat"] is not None else None,
            lon=float(row["lon"]) if row["lon"] is not None else None,
        )

    # ── public API ────────────────────────────────────────────────────────────

    def add_package(
        self,
        address: str,
        weight_kg: float,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> Package:
        """Create and persist a new package.  Returns the saved Package."""
        pkg = Package(
            id=uuid.uuid4().hex,
            address=address,
            weight_kg=weight_kg,
            status=DeliveryStatus.PENDING,
            created_at=datetime.datetime.utcnow(),
            lat=lat,
            lon=lon,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO packages (id, address, weight_kg, status, created_at, lat, lon) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pkg.id, pkg.address, pkg.weight_kg, pkg.status.value,
                 pkg.created_at.isoformat(), pkg.lat, pkg.lon),
            )
        return pkg

    def get_all(self) -> list[Package]:
        """Return all packages ordered by creation time (newest first)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM packages ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_package(r) for r in rows]

    def get_by_status(self, status: DeliveryStatus) -> list[Package]:
        """Return packages filtered by status."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM packages WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            ).fetchall()
        return [self._row_to_package(r) for r in rows]

    def get_by_id(self, package_id: str) -> Optional[Package]:
        """Return a single package by ID, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM packages WHERE id = ?", (package_id,)
            ).fetchone()
        return self._row_to_package(row) if row else None

    def set_status(self, package_id: str, status: DeliveryStatus) -> None:
        """Update the delivery status of a package."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE packages SET status = ? WHERE id = ?",
                (status.value, package_id),
            )

    def set_coordinates(self, package_id: str, lat: float, lon: float) -> None:
        """Attach geocoded coordinates to a package."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE packages SET lat = ?, lon = ? WHERE id = ?",
                (lat, lon, package_id),
            )

    def delete_package(self, package_id: str) -> None:
        """Permanently remove a package from the database."""
        with self._connect() as conn:
            conn.execute("DELETE FROM packages WHERE id = ?", (package_id,))

    def count_by_status(self) -> dict[DeliveryStatus, int]:
        """Return a status → count mapping for all packages."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM packages GROUP BY status"
            ).fetchall()
        result = {s: 0 for s in DeliveryStatus}
        for row in rows:
            result[DeliveryStatus(row["status"])] = row["n"]
        return result

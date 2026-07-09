"""SQLAlchemy Core table definitions for the control-plane stores.

One ``MetaData`` drives both SQLite and Postgres (see postgres-backend.md). The four stores
(registry / artifacts / proposals / aggregation) share one backing database, so their tables share
one metadata — ``metadata.create_all(engine)`` from any store creates the full set (idempotent, and
it only creates what is absent, so existing SQLite files keep working).

Columns match the original hand-written schemas exactly. Timestamps ride as ``Text`` (ISO strings);
JSON-ish blobs the store code (de)serialises by hand also ride as ``Text`` — except the aggregation
``payload``, which is a real ``JSON`` column because the rollups extract fields from it in SQL
(dialect-portable ``JSON_EXTRACT`` on SQLite, ``->>`` on Postgres) rather than in Python.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
)

metadata = MetaData()

# --- registry -----------------------------------------------------------------

instances = Table(
    "instances",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("endpoint", Text, nullable=False),
    Column("connection", Text, nullable=False, default="direct"),
    Column("token_ref", Text, nullable=False, default=""),
    Column("tier", Text, nullable=False, default="read"),
    Column("token_fingerprint", Text, nullable=False, default=""),
    Column("token_hash", Text, nullable=False, default=""),
    Column("token_minted_at", Text),
    Column("schema_version", Text, nullable=False, default=""),
    Column("capabilities", Text, nullable=False, default="{}"),
    Column("health", Text, nullable=False, default="unknown"),
    Column("last_seen", Text),
    Column("created_at", Text, nullable=False),
)

commands = Table(
    "commands",
    metadata,
    Column("cmd_id", Text, primary_key=True),
    Column("instance_id", Text, nullable=False),
    Column("verb", Text, nullable=False),
    Column("args", Text, nullable=False, default="{}"),
    Column("status", Text, nullable=False, default="queued"),
    Column("output", Text),
    Column("error", Text),
    Column("created_at", Text, nullable=False),
    Column("dispatched_at", Text),
    Column("result_at", Text),
    Index("idx_commands_instance", "instance_id", "status"),
)

# --- artifacts ----------------------------------------------------------------

artifact_versions = Table(
    "artifact_versions",
    metadata,
    Column("kind", Text, nullable=False),
    Column("id", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("authored_by", Text, nullable=False, default=""),
    Column("schema_version", Text, nullable=False, default=""),
    Column("created_at", Text, nullable=False),
    Column("seq", Integer, nullable=False),
    PrimaryKeyConstraint("kind", "id", "version"),
)

deployments = Table(
    "deployments",
    metadata,
    Column("instance_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("id", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    PrimaryKeyConstraint("instance_id", "kind", "id"),
)

reported_artifacts = Table(
    "reported_artifacts",
    metadata,
    Column("instance_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("id", Text, nullable=False),
    Column("version", Text, nullable=False, default=""),
    Column("content_hash", Text, nullable=False, default=""),
    Column("reported_at", Text, nullable=False),
    PrimaryKeyConstraint("instance_id", "kind", "id"),
)

# --- proposals ----------------------------------------------------------------

proposals = Table(
    "proposals",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("artifact_id", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("status", Text, nullable=False, default="pending"),
    Column("proposed_by", Text, nullable=False, default=""),
    Column("signal", Text, nullable=False, default=""),
    Column("eval_summary", Text, nullable=False, default="{}"),
    Column("approved_by", Text, nullable=False, default=""),
    Column("reason", Text, nullable=False, default=""),
    Column("published_version", Text, nullable=False, default=""),
    Column("created_at", Text, nullable=False),
    Column("decided_at", Text),
    Index("idx_proposals_status", "status", "created_at"),
)

# --- aggregation --------------------------------------------------------------

agg_records = Table(
    "agg_records",
    metadata,
    Column("instance_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("record_id", Text, nullable=False),
    Column("ts", Text, nullable=False, default=""),
    Column("payload", JSON, nullable=False, default=dict),
    PrimaryKeyConstraint("instance_id", "kind", "record_id"),
    Index("idx_agg_kind_ts", "kind", "ts"),
)

# --- observed state (fleet enrollment Phase 1) --------------------------------

# The last full InstanceState the panel pulled from an instance (GET /fleet/state). Cached so the
# instance's inventory stays inspectable when it's offline. One row per instance (latest wins).
instance_state = Table(
    "instance_state",
    metadata,
    Column("instance_id", Text, primary_key=True),
    Column("state", JSON, nullable=False, default=dict),
    Column("synced_at", Text, nullable=False),
)

# --- fleet membership credential (enrollment Phase 2) -------------------------

# The membership credential the panel received from an instance's /fleet/register — the per-fleet
# API key the panel uses to call that instance. The secret is stored ENCRYPTED (never plaintext,
# never a file); the CredentialStore wraps a SecretBox. One row per instance.
instance_credential = Table(
    "instance_credential",
    metadata,
    Column("instance_id", Text, primary_key=True),
    Column("membership_id", Text, nullable=False),
    Column("fleet_id", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("ciphertext", Text, nullable=False),  # encrypted membership secret
    Column("created_at", Text, nullable=False),
)

# --- fleet identity (design 21) -----------------------------------------------

# This panel's own fleet identity: one Ed25519 keypair. The public key + derived fleet_id are
# non-secret; the private key is stored ENCRYPTED (SecretBox, same as membership credentials). A
# singleton row (id = "default"). fleet_id = fleet:<base32 sha256 pubkey> — self-certifying.
fleet_identity = Table(
    "fleet_identity",
    metadata,
    Column("id", Text, primary_key=True),
    Column("public_key", Text, nullable=False),  # base64 Ed25519 public key
    Column("private_key_ciphertext", Text, nullable=False),  # encrypted base64 private key
    Column("display_name", Text, nullable=False, default=""),
    Column("created_at", Text, nullable=False),
)

# --- monotonic deploy sequence (design 22 downgrade guard) --------------------

# A single per-panel counter the fleet stamps into each deploy signature (strictly increasing over
# time). The instance rejects any deploy whose sequence isn't newer than the last it applied, so an
# old validly-signed deploy can't be replayed over a newer version. Singleton row (id = "default").
fleet_deploy_seq = Table(
    "fleet_deploy_seq",
    metadata,
    Column("id", Text, primary_key=True),
    Column("value", Integer, nullable=False),
)

# --- Mode B join codes (enrollment Phase 2) -----------------------------------

# One-time join codes the panel mints for the instance-initiated (Mode B) handshake: a NAT'd
# instance calls POST /fleet/join with a code the operator handed it, and the panel creates its
# Instance + issues the connector→panel credential. Only the code's hash is stored (high-entropy
# secret, plain hash suffices); a code is single-use (consumed_at) and TTL-bounded (expires_at).
join_code = Table(
    "join_code",
    metadata,
    Column("code_hash", Text, primary_key=True),
    Column("name", Text, nullable=False, default=""),  # display name for the joining instance
    Column("endpoint", Text, nullable=False, default=""),  # optional advertised endpoint hint
    Column(
        "tier", Text, nullable=False, default="read"
    ),  # granted connector tier (bounds commands)
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("consumed_at", Text),  # set once, on the single successful join
)

__all__ = [
    "agg_records",
    "artifact_versions",
    "commands",
    "deployments",
    "fleet_deploy_seq",
    "fleet_identity",
    "instance_credential",
    "instance_state",
    "instances",
    "join_code",
    "metadata",
    "proposals",
    "reported_artifacts",
]

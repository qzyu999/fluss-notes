# Apache Fluss — Comprehensive Beginner Guide

> **Audience:** Someone new to Fluss *and* Rust, aiming to contribute to the Python client first and eventually grow into Rust.

---

## Table of Contents

1. [What is Fluss?](#1-what-is-fluss)
2. [Core Concepts (Rigorous Definitions)](#2-core-concepts)
3. [Architecture Overview](#3-architecture-overview)
4. [Repository Layout (fluss-rust)](#4-repository-layout)
5. [Running Fluss Locally](#5-running-fluss-locally)
6. [Rust 101 — Just Enough to Read the Codebase](#6-rust-101)
7. [The Python Client — Deep Dive](#7-the-python-client)
8. [Complete Python Examples](#8-complete-python-examples)
9. [Contributing to the Python Client](#9-contributing-to-the-python-client)
10. [Growing Into Rust](#10-growing-into-rust)
11. [Glossary](#11-glossary)
12. [Resources](#12-resources)

---

## 1. What is Fluss?

**Apache Fluss™ (Incubating)** is a **streaming storage system** built for real-time analytics. It serves as the **real-time data layer** for Lakehouse architectures.

### The Problem Fluss Solves

In a traditional data lakehouse, data flows through batch pipelines with significant latency (minutes to hours). If you need **sub-second** or **low-latency** access to fresh data — for dashboards, ML feature stores, or real-time applications — you need a streaming storage layer that sits between your ingestion sources and your lakehouse tables.

Fluss fills this gap:

```
┌─────────┐     ┌─────────┐     ┌─────────────────────┐     ┌──────────────┐
│ Sources │ ──▶ │  Fluss  │ ──▶ │ Lakehouse (Paimon/  │ ──▶ │  Analytics   │
│(Kafka,  │     │(stream  │     │  Iceberg snapshots)  │     │  (Flink,     │
│ CDC, …) │     │ storage)│     │                     │     │   Spark, …)  │
└─────────┘     └─────────┘     └─────────────────────┘     └──────────────┘
                     │
                     ▼
              Real-time queries
              (low-latency reads)
```

### Why Not Just Use Kafka?

| Feature | Kafka | Fluss |
|---|---|---|
| Append-only logs | ✅ | ✅ (Log Tables) |
| Primary key upserts | ❌ (requires Kafka Streams/ksqlDB) | ✅ (PK Tables, native) |
| Point lookups by key | ❌ | ✅ |
| Columnar storage format | ❌ | ✅ (Arrow-native) |
| Lakehouse integration | Manual (connectors) | Built-in (Paimon, Iceberg snapshots) |
| Partial updates | ❌ | ✅ |

Fluss is not a *replacement* for Kafka — it's a **streaming storage** that understands table semantics (schemas, primary keys, partitions) natively.

---

## 2. Core Concepts

### 2.1 Table Types

Fluss has exactly two table types. Every table is one or the other.

#### Log Table (Append-Only)

- **Definition:** A table *without* a primary key. Records are immutable once written. New records are *appended* to the end of the log.
- **Use cases:** Event streams, audit trails, click-streams, system logs.
- **Semantics:** Each record gets a monotonically increasing **offset** within its bucket. You can never update or delete a record — only append new ones.

```
Bucket 0:  [offset=0: {id:1, event:"click"}] → [offset=1: {id:2, event:"view"}] → ...
Bucket 1:  [offset=0: {id:3, event:"buy"}]   → ...
```

#### Primary Key (PK) Table

- **Definition:** A table *with* a primary key. Supports **upsert** (insert-or-update), **delete**, and **point lookup** by key.
- **Use cases:** User profiles, product catalogs, materialized views, any dimension table.
- **Semantics:** The latest value for each key is maintained. Under the hood, Fluss stores a changelog log *plus* a KV store for lookups.

```
PK Table "users" (primary_key = user_id):
  upsert({user_id: 1, name: "Alice"})  → stored
  upsert({user_id: 1, name: "Alice2"}) → overwrites previous
  delete({user_id: 1})                  → removes the row
  lookup({user_id: 1})                  → None
```

### 2.2 Bucket

- **Definition:** The unit of parallelism within a table. Analogous to a **Kafka partition**.
- Each table has one or more buckets (configurable via `bucket_count`).
- Records are distributed across buckets using a hashing strategy (by key for PK tables, by `bucket_keys` or round-robin/sticky for log tables).
- **Readers subscribe to individual buckets.** Each bucket maintains its own offset sequence.

### 2.3 Partition

- **Definition:** A way to organize data by column values (e.g., by date, region).
- Each partition contains its own set of buckets.
- **Partitions must be created explicitly before writing data.** If you write to a partition that doesn't exist, the client will retry indefinitely by default.
- For PK tables, partition columns must be part of the primary key.

```
Table "events" (partition_keys=["region"], bucket_count=2):

  Partition "US":
    Bucket 0: [offset=0, ...] [offset=1, ...]
    Bucket 1: [offset=0, ...]
  
  Partition "EU":
    Bucket 0: [offset=0, ...]
    Bucket 1: [offset=0, ...] [offset=1, ...]
```

### 2.4 Offset

- **Definition:** The position of a record within a bucket's log. Offsets are **0-indexed** and **monotonically increasing** within each bucket.
- Special constants:
  - `EARLIEST_OFFSET` (`-2`): Start reading from the very first record.
  - Use `list_offsets(OffsetSpec.latest())` to get the current end-of-log offset for "only read new data" semantics.

### 2.5 Schema

- Fluss tables are **strongly typed**. You define a schema with named, typed columns before creating a table.
- Underlying type system maps to Apache Arrow types.
- In Python, you define schemas using **PyArrow** types.

### 2.6 ChangeType

When reading from a PK table's log, each record carries a **change type** indicating what happened:

| ChangeType | Symbol | Meaning |
|---|---|---|
| `AppendOnly` | `+A` | Record appended to a log table |
| `Insert` | `+I` | New key inserted |
| `UpdateBefore` | `-U` | Previous value *before* an update |
| `UpdateAfter` | `+U` | New value *after* an update |
| `Delete` | `-D` | Key was deleted |

This is the **changelog semantics** — you can replay the full history of a PK table by processing these change events in order.

### 2.7 Database

Tables live inside a **database** (namespace). The default database is `"fluss"`. You can create custom databases.

```
Database "fluss"       Database "analytics"
  ├── events             ├── user_profiles
  ├── clicks             └── product_catalog
  └── orders
```

### 2.8 Lake Snapshot

Fluss can periodically snapshot data to a lakehouse format (Paimon, Iceberg). A **LakeSnapshot** records the offset-per-bucket at the time of the snapshot. After reading the snapshot from the lake, you can resume streaming from those offsets to get only new data.

---

## 3. Architecture Overview

### Cluster Components

```
┌───────────────────────────────────────────────────┐
│                 Fluss Cluster                     │
│                                                   │
│  ┌──────────────────┐   ┌──────────────────────┐ │
│  │ CoordinatorServer│   │   TabletServer(s)    │ │
│  │  (metadata mgmt, │   │  (data storage,      │ │
│  │   leader elect.) │   │   read/write paths)  │ │
│  └──────────────────┘   └──────────────────────┘ │
│                                                   │
│              backed by ZooKeeper                  │
└───────────────────────────────────────────────────┘
         ▲                        ▲
         │    RPC (protobuf)      │
    ┌────┴────┐             ┌─────┴──────┐
    │  Admin  │             │  Table I/O │
    │  Client │             │  Client    │
    └─────────┘             └────────────┘
```

- **CoordinatorServer:** Manages metadata (databases, tables, schemas), handles leader election for buckets.
- **TabletServer(s):** Stores actual data. Each bucket is assigned a leader tablet server for writes. Replicas provide fault tolerance.
- **Clients** communicate with the cluster over an RPC protocol using Protobuf.

### Client SDKs

| SDK | Location | Async Runtime | Build System |
|---|---|---|---|
| **Rust** | `crates/fluss` | Tokio | Cargo |
| **Python** | `bindings/python` | asyncio | maturin (PyO3) |
| **C++** | `bindings/cpp` | Sync (Tokio internal) | CMake / Bazel |
| **Java** | [main repo](https://github.com/apache/fluss) | — | Maven |

> **Key insight for Python contributors:** The Python client is a *thin wrapper* around the Rust client, built using [PyO3](https://pyo3.rs/). Python calls → Rust FFI → Rust async code → cluster RPC. This means your Python contributions will involve both `.py` files and `.rs` files.

---

## 4. Repository Layout

```
fluss-rust/
├── Cargo.toml                    # Workspace root — defines all crates
├── DEVELOPMENT.md                # Build & test instructions
├── crates/
│   ├── fluss/                    # 🔵 Core Rust client (the "engine")
│   │   ├── Cargo.toml
│   │   ├── build.rs              # Protobuf code generation
│   │   └── src/
│   │       ├── lib.rs            # Public API surface
│   │       ├── client/           # Admin, Table, Connection logic
│   │       ├── config.rs         # Configuration types
│   │       ├── row/              # Row types, GenericRow, data types
│   │       ├── record/           # Record batch handling
│   │       ├── rpc/              # Protobuf RPC layer
│   │       ├── metadata/         # Table metadata, schema
│   │       └── ...
│   └── examples/                 # Rust usage examples
│       └── src/
│           └── example_table.rs
├── bindings/
│   ├── python/                   # 🐍 Python client (YOUR STARTING POINT)
│   │   ├── Cargo.toml            # Rust deps for the Python binding
│   │   ├── pyproject.toml        # Python packaging (maturin + uv)
│   │   ├── src/                  # Rust source (PyO3 bindings)
│   │   │   ├── lib.rs            # Module registration
│   │   │   ├── config.rs         # Config → Python
│   │   │   ├── connection.rs     # FlussConnection → Python
│   │   │   ├── admin.rs          # FlussAdmin → Python
│   │   │   ├── table.rs          # FlussTable, scanners, writers → Python
│   │   │   ├── metadata.rs       # TableInfo, Schema, etc. → Python
│   │   │   ├── error.rs          # FlussError → Python
│   │   │   ├── lookup.rs         # Lookuper → Python
│   │   │   ├── upsert.rs         # UpsertWriter → Python
│   │   │   └── utils.rs          # Conversion helpers (Arrow ↔ PyArrow)
│   │   ├── fluss/                # Python package (importable)
│   │   │   ├── __init__.py       # Re-exports
│   │   │   ├── __init__.pyi      # Type stubs (for IDE autocomplete)
│   │   │   └── py.typed          # PEP 561 marker
│   │   ├── example/
│   │   │   └── example.py        # Comprehensive Python example
│   │   └── test/
│   │       ├── conftest.py       # Pytest fixtures (testcontainers)
│   │       ├── test_admin.py
│   │       ├── test_log_table.py
│   │       ├── test_kv_table.py
│   │       └── test_sasl_auth.py
│   └── cpp/                      # C++ bindings
├── website/                      # Docusaurus docs site
│   └── docs/
│       ├── index.md
│       └── user-guide/
│           ├── python/           # Python user guide
│           ├── rust/             # Rust user guide
│           └── cpp/              # C++ user guide
├── scripts/                      # CI/release scripts
└── justfile                      # Task runner commands
```

---

## 5. Running Fluss Locally

### 5.1 Prerequisites

| Tool | Version | Installation |
|---|---|---|
| **Java** | 17+ | `brew install openjdk@17` (macOS) |
| **Rust** | 1.85+ | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| **protobuf** | latest | `brew install protobuf` (macOS) |
| **Python** | 3.9+ | System Python or `pyenv` |
| **uv** | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

### 5.2 Start a Fluss Cluster

```bash
# 1. Download the latest Fluss release (requires Java 17+)
curl -LO https://archive.apache.org/dist/incubator/fluss/0.8.0-incubating/fluss-0.8.0-incubating-bin.tgz
tar -xzf fluss-0.8.0-incubating-bin.tgz
cd fluss-0.8.0-incubating/

# 2. Make sure JAVA_HOME is set
export JAVA_HOME=$(/usr/libexec/java_home -v 17)  # macOS

# 3. Start the local cluster (CoordinatorServer + TabletServer + ZooKeeper)
./bin/local-cluster.sh start

# The cluster is now running at 127.0.0.1:9123
```

To stop the cluster later:
```bash
./bin/local-cluster.sh stop
```

### 5.3 Build the Rust Client

```bash
cd /path/to/fluss-rust

# Build everything
cargo build

# Run unit tests
cargo test --workspace

# Run the Rust example (requires a running Fluss cluster)
cargo build --example example-table --release
./target/release/examples/example-table
```

### 5.4 Build the Python Client (for development)

```bash
cd /path/to/fluss-rust/bindings/python

# Install dev dependencies (creates a .venv automatically)
uv sync --all-extras

# Build the Rust bindings and install in dev mode
source .venv/bin/activate
uv run maturin develop

# Verify it works
python -c "import fluss; print('Fluss Python bindings ready!')"

# Run the example (requires a running Fluss cluster)
uv run python example/example.py
```

### 5.5 Running Tests

```bash
# Python tests (use testcontainers — requires Docker)
cd bindings/python
uv run pytest test/ -v

# Rust unit tests
cargo test --workspace

# Rust integration tests (requires a running Fluss cluster)
cargo test --features integration_tests --workspace
```

---

## 6. Rust 101

> You don't need to master Rust to contribute to the Python client, but you *do* need to read the Rust files in `bindings/python/src/`. Here's the minimum you need to know.

### 6.1 Ownership & Borrowing (The Big Idea)

Rust's key difference from Python: **no garbage collector**. Instead, the compiler tracks who *owns* each piece of data and when it can be freed.

```rust
let s = String::from("hello");  // `s` owns the string
let r = &s;                      // `r` borrows a reference (read-only)
let m = &mut s;                  // `m` borrows a mutable reference (read-write)
// You can have EITHER one &mut OR many & at a time, never both
```

**Why it matters for Python bindings:** PyO3 types like `PyRef<'_, Self>` and `PyRefMut<'_, Self>` map to Rust's borrowing rules — they ensure Python doesn't accidentally hold two mutable references to the same Rust object.

### 6.2 Common Types You'll See

```rust
// Option<T> — like Python's Optional[T]
let maybe: Option<i32> = Some(42);   // has a value
let nope: Option<i32> = None;         // no value

// Result<T, E> — like Python's try/except
let ok: Result<i32, String> = Ok(42);
let err: Result<i32, String> = Err("oops".to_string());

// Vec<T> — like Python's list
let v: Vec<i32> = vec![1, 2, 3];

// HashMap<K, V> — like Python's dict
let m: HashMap<String, i32> = HashMap::new();

// String vs &str
// String = owned, heap-allocated (like Python str)
// &str   = borrowed reference to string data (like a pointer/slice)
```

### 6.3 Async Rust (Tokio)

Fluss uses **Tokio** for async I/O. The pattern is similar to Python's `asyncio`:

```rust
// Python:                         // Rust:
// async def foo():                async fn foo() -> Result<()> {
//     result = await bar()            let result = bar().await?;
//     return result                   Ok(result)
//                                 }
```

The `?` operator is Rust's equivalent of "if this is an error, return it immediately" — similar to raising an exception in Python.

### 6.4 PyO3 — The Bridge Between Python and Rust

[PyO3](https://pyo3.rs/) lets you write Rust code that Python can import as a native module.

```rust
use pyo3::prelude::*;

#[pyclass]                          // Exposes this struct as a Python class
struct MyConfig {
    #[pyo3(get, set)]               // Python can read/write this field
    bootstrap_servers: String,
}

#[pymethods]                        // Methods callable from Python
impl MyConfig {
    #[new]                           // __init__
    fn new(bootstrap_servers: String) -> Self {
        MyConfig { bootstrap_servers }
    }

    fn connect(&self) -> PyResult<()> {  // PyResult = can raise Python exception
        // ... Rust code ...
        Ok(())
    }
}
```

When you run `maturin develop`, it compiles the Rust code with PyO3 and creates a Python-importable `.so` file.

### 6.5 How to Read the Python Binding Code

The binding files in `bindings/python/src/` follow a consistent pattern:

1. **Each `.rs` file** wraps one or more Rust types from `crates/fluss` as Python classes.
2. **`#[pyclass]`** = a Python-visible class.
3. **`#[pymethods]`** = the methods you can call from Python.
4. **`#[pyo3(name = "...")]`** = rename in Python.
5. **`fn<'py>(..., py: Python<'py>)`** = the function needs access to the Python GIL.
6. **`pyo3_async_runtimes::tokio::future_into_py(...)`** = converts a Rust async future into a Python awaitable.

Example from `bindings/python/src/connection.rs`:
```rust
#[pymethods]
impl FlussConnection {
    #[staticmethod]
    fn create(py: Python, config: &FlussConfig) -> PyResult<Bound<PyAny>> {
        // Convert Rust async → Python awaitable
        let conn_config = config.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let conn = fluss::FlussConnection::new(conn_config).await;
            Ok(FlussConnection { inner: Arc::new(conn) })
        })
    }
}
```

This is what allows you to write `conn = await FlussConnection.create(config)` in Python.

---

## 7. The Python Client — Deep Dive

### 7.1 How It's Built

```
Python code you write        fluss/__init__.py (re-exports)
         │                          │
         ▼                          ▼
   import fluss  ──────▶  fluss._fluss  (compiled Rust module)
                                    │
                                    ▼
                     bindings/python/src/*.rs  (PyO3 wrappers)
                                    │
                                    ▼
                        crates/fluss/src/**  (core Rust client)
                                    │
                                    ▼
                          Fluss cluster (RPC)
```

**The compilation pipeline:**
1. `maturin` reads `pyproject.toml` (which says `module-name = "fluss._fluss"`)
2. It compiles `bindings/python/src/lib.rs` and all its dependencies (including the entire `crates/fluss` crate)
3. Output: `fluss/_fluss.cpython-*.so` — a native shared library
4. `fluss/__init__.py` re-exports everything from `fluss._fluss`

### 7.2 Connection Lifecycle

```python
import fluss

# 1. Configure
config = fluss.Config({"bootstrap.servers": "127.0.0.1:9123"})

# 2. Connect (async — creates TCP connections to CoordinatorServer)
conn = await fluss.FlussConnection.create(config)

# 3. Get interfaces
admin = await conn.get_admin()     # For DDL operations (create/drop/list)
table = await conn.get_table(path) # For DML operations (read/write)

# 4. Cleanup
conn.close()

# Or use context manager:
with await fluss.FlussConnection.create(config) as conn:
    ...
```

### 7.3 Admin Operations

```python
admin = await conn.get_admin()

# Databases
await admin.create_database("my_db", ignore_if_exists=True)
dbs = await admin.list_databases()              # → ["fluss", "my_db"]
exists = await admin.database_exists("my_db")   # → True
await admin.drop_database("my_db", ignore_if_not_exists=True, cascade=True)

# Tables
table_path = fluss.TablePath("fluss", "events")
schema = fluss.Schema(pa.schema([
    pa.field("id", pa.int32()),
    pa.field("name", pa.string()),
]))
descriptor = fluss.TableDescriptor(schema, bucket_count=3)
await admin.create_table(table_path, descriptor, ignore_if_exists=True)

info = await admin.get_table_info(table_path)
tables = await admin.list_tables("fluss")       # → ["events"]
await admin.drop_table(table_path, ignore_if_not_exists=True)
```

### 7.4 Data Types

| PyArrow Type | Fluss Type | Python Read Type |
|---|---|---|
| `pa.bool_()` | Boolean | `bool` |
| `pa.int8()` / `int16()` / `int32()` / `int64()` | TinyInt / SmallInt / Int / BigInt | `int` |
| `pa.float32()` / `float64()` | Float / Double | `float` |
| `pa.string()` | String | `str` |
| `pa.binary()` | Bytes | `bytes` |
| `pa.date32()` | Date | `datetime.date` |
| `pa.time32("ms")` | Time | `datetime.time` |
| `pa.timestamp("us")` | Timestamp (NTZ) | `datetime.datetime` |
| `pa.timestamp("us", tz="UTC")` | TimestampLTZ | `datetime.datetime` |
| `pa.decimal128(precision, scale)` | Decimal | `decimal.Decimal` |

### 7.5 Writing Data

#### Log Tables (Append)

```python
table = await conn.get_table(table_path)
writer = table.new_append().create_writer()

# Single rows (dict, list, or tuple)
writer.append({"id": 1, "name": "Alice"})
writer.append([2, "Bob"])

# Bulk writes
writer.write_arrow(pa_table)          # PyArrow Table
writer.write_arrow_batch(record_batch) # PyArrow RecordBatch
writer.write_pandas(df)                # Pandas DataFrame

# IMPORTANT: flush() sends all pending writes to the server
await writer.flush()

# Per-record acknowledgment (for critical writes)
handle = writer.append({"id": 3, "name": "Charlie"})
await handle.wait()  # blocks until server confirms THIS write
```

#### PK Tables (Upsert/Delete)

```python
writer = table.new_upsert().create_writer()

writer.upsert({"user_id": 1, "name": "Alice", "age": 25})
await writer.flush()

# Delete by primary key
handle = writer.delete({"user_id": 1})
await handle.wait()

# Partial update (update only specific columns)
partial = table.new_upsert().partial_update_by_name(["user_id", "age"]).create_writer()
partial.upsert({"user_id": 1, "age": 26})  # only updates age, name unchanged
await partial.flush()
```

### 7.6 Reading Data

#### Two Scanner Types

| Scanner | Created By | Returns | Best For |
|---|---|---|---|
| **Batch scanner** | `create_record_batch_log_scanner()` | Arrow Tables, DataFrames | Analytics, batch processing |
| **Record scanner** | `create_log_scanner()` | Individual `ScanRecord` objects | Streaming, per-record processing |

#### Two Reading Modes

| Mode | Methods | Behavior |
|---|---|---|
| **One-shot** | `to_arrow()`, `to_pandas()` | Reads all data up to current latest offset, then returns |
| **Continuous** | `poll_arrow(ms)`, `poll(ms)`, `poll_record_batch(ms)` | Returns whatever is available within timeout; call in a loop |

#### Example: Batch Read

```python
info = await admin.get_table_info(table_path)
scanner = await table.new_scan().create_record_batch_log_scanner()
scanner.subscribe_buckets({i: fluss.EARLIEST_OFFSET for i in range(info.num_buckets)})

# One-shot: reads everything, returns
df = scanner.to_pandas()
arrow_table = scanner.to_arrow()
```

#### Example: Continuous Streaming

```python
scanner = await table.new_scan().create_log_scanner()
scanner.subscribe(bucket_id=0, start_offset=fluss.EARLIEST_OFFSET)

while True:
    records = scanner.poll(timeout_ms=5000)
    for record in records:
        print(f"offset={record.offset}, row={record.row}")
```

#### Example: Only New Records

```python
offsets = await admin.list_offsets(table_path, [0], fluss.OffsetSpec.latest())
scanner = await table.new_scan().create_record_batch_log_scanner()
scanner.subscribe(bucket_id=0, start_offset=offsets[0])
```

### 7.7 Point Lookups (PK Tables Only)

```python
lookuper = table.new_lookup().create_lookuper()
result = await lookuper.lookup({"user_id": 1})
# result is dict | None
if result:
    print(result["name"])  # "Alice"
```

### 7.8 Error Handling

```python
import fluss

try:
    await admin.create_table(table_path, descriptor)
except fluss.FlussError as e:
    print(f"Code: {e.error_code}, Message: {e.message}")

    if e.error_code == fluss.ErrorCode.TABLE_ALREADY_EXIST:
        print("Table already exists")
    elif e.error_code == fluss.ErrorCode.CLIENT_ERROR:
        print("Client-side error (connection, type mismatch, etc.)")
```

Key error codes:
| Code | Constant | Meaning |
|---|---|---|
| `-2` | `CLIENT_ERROR` | Client-side error (not from server) |
| `1` | `NETWORK_EXCEPTION` | Server disconnected |
| `7` | `TABLE_NOT_EXIST` | Table not found |
| `8` | `TABLE_ALREADY_EXIST` | Table already exists |
| `25` | `REQUEST_TIME_OUT` | Timed out |
| `36` | `PARTITION_NOT_EXISTS` | Partition not found |
| `46` | `AUTHENTICATE_EXCEPTION` | Auth failed |

### 7.9 Configuration Reference

```python
config = fluss.Config({
    "bootstrap.servers": "127.0.0.1:9123",         # Cluster address
    "writer.batch-size": "2097152",                 # 2 MB batch size
    "writer.batch-timeout-ms": "100",               # Max wait for batch fill
    "writer.acks": "all",                           # Wait for all replicas
    "writer.retries": "3",                          # Retry count
    "scanner.log.max-poll-records": "500",          # Max records per poll()
    "connect-timeout": "120000",                    # TCP timeout (ms)
    # SASL Auth:
    "security.protocol": "sasl",
    "security.sasl.mechanism": "PLAIN",
    "security.sasl.username": "admin",
    "security.sasl.password": "admin-secret",
})
```

---

## 8. Complete Python Examples

### 8.1 End-to-End: Log Table

```python
import asyncio
import fluss
import pyarrow as pa

async def log_table_demo():
    config = fluss.Config({"bootstrap.servers": "127.0.0.1:9123"})
    conn = await fluss.FlussConnection.create(config)
    admin = await conn.get_admin()

    # Create table
    schema = fluss.Schema(pa.schema([
        pa.field("id", pa.int32()),
        pa.field("event", pa.string()),
        pa.field("value", pa.float64()),
    ]))
    path = fluss.TablePath("fluss", "demo_events")
    await admin.create_table(path, fluss.TableDescriptor(schema), ignore_if_exists=True)

    # Write data
    table = await conn.get_table(path)
    writer = table.new_append().create_writer()
    for i in range(10):
        writer.append({"id": i, "event": f"click_{i}", "value": i * 1.5})
    await writer.flush()

    # Read data
    info = await admin.get_table_info(path)
    scanner = await table.new_scan().create_record_batch_log_scanner()
    scanner.subscribe_buckets({i: fluss.EARLIEST_OFFSET for i in range(info.num_buckets)})
    df = scanner.to_pandas()
    print(df)

    # Cleanup
    await admin.drop_table(path, ignore_if_not_exists=True)
    conn.close()

asyncio.run(log_table_demo())
```

### 8.2 End-to-End: Primary Key Table

```python
import asyncio
import fluss
import pyarrow as pa

async def pk_table_demo():
    config = fluss.Config({"bootstrap.servers": "127.0.0.1:9123"})
    conn = await fluss.FlussConnection.create(config)
    admin = await conn.get_admin()

    # Create PK table
    schema = fluss.Schema(
        pa.schema([
            pa.field("user_id", pa.int32()),
            pa.field("name", pa.string()),
            pa.field("score", pa.int64()),
        ]),
        primary_keys=["user_id"],
    )
    path = fluss.TablePath("fluss", "demo_users")
    await admin.create_table(path, fluss.TableDescriptor(schema, bucket_count=3), ignore_if_exists=True)

    table = await conn.get_table(path)

    # Upsert
    writer = table.new_upsert().create_writer()
    writer.upsert({"user_id": 1, "name": "Alice", "score": 100})
    writer.upsert({"user_id": 2, "name": "Bob", "score": 200})
    await writer.flush()

    # Lookup
    lookuper = table.new_lookup().create_lookuper()
    print(await lookuper.lookup({"user_id": 1}))  # {'user_id': 1, 'name': 'Alice', 'score': 100}

    # Update
    handle = writer.upsert({"user_id": 1, "name": "Alice", "score": 150})
    await handle.wait()
    print(await lookuper.lookup({"user_id": 1}))  # score is now 150

    # Delete
    handle = writer.delete({"user_id": 2})
    await handle.wait()
    print(await lookuper.lookup({"user_id": 2}))  # None

    # Cleanup
    await admin.drop_table(path, ignore_if_not_exists=True)
    conn.close()

asyncio.run(pk_table_demo())
```

---

## 9. Contributing to the Python Client

### 9.1 Development Setup (Step by Step)

```bash
# 1. Clone the repo
git clone https://github.com/apache/fluss-rust.git
cd fluss-rust

# 2. Install Rust (if not already)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# 3. Install protobuf (macOS)
brew install protobuf

# 4. Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 5. Set up the Python dev environment
cd bindings/python
uv sync --all-extras

# 6. Build the Rust bindings in dev mode
source .venv/bin/activate
uv run maturin develop

# 7. Verify
python -c "import fluss; print('Ready!')"
```

### 9.2 The Development Loop

```bash
# After editing Rust code in bindings/python/src/ or crates/fluss/src/:
uv run maturin develop          # Recompile (takes ~30s-2min)

# After editing Python code in fluss/ or test/:
# No recompilation needed

# Run tests:
uv run pytest test/ -v          # Requires Docker for testcontainers

# Format & lint:
uv run ruff format .            # Python formatting
uv run ruff check .             # Python linting
cargo fmt --all                 # Rust formatting
cargo clippy --all-targets      # Rust linting
```

### 9.3 File-by-File Guide: Where to Make Changes

| What you want to do | Files to edit |
|---|---|
| Add a new Python method to an existing class | `bindings/python/src/<class>.rs` (add `#[pymethods]`), `fluss/__init__.pyi` (add type stub) |
| Add a new Python class | `bindings/python/src/<new_file>.rs` + register in `lib.rs`, `fluss/__init__.pyi` |
| Fix a Python API bug | Track from `bindings/python/src/*.rs` → `crates/fluss/src/client/*.rs` |
| Add a new test | `bindings/python/test/test_*.py` |
| Update docs | `website/docs/user-guide/python/` |
| Fix a core Rust bug | `crates/fluss/src/**` |

### 9.4 How Tests Work

Tests use **testcontainers** to spin up a Fluss cluster in Docker:

```python
# test/conftest.py sets up fixtures like:
@pytest.fixture(scope="session")
async def fluss_connection():
    # Starts a Fluss container, returns a connection
    ...
```

You need **Docker running** to execute the integration tests.

### 9.5 CI Checks

Your PR must pass:
1. `cargo fmt --all -- --check` — Rust formatting
2. `cargo clippy --all-targets` — Rust linting
3. `cargo test --workspace` — Rust unit tests
4. `cargo deny check licenses` — License compliance
5. Python formatting/linting via `ruff`
6. Integration tests (CI environment)

---

## 10. Growing Into Rust

### 10.1 Recommended Learning Path

1. **Read [The Rust Book](https://doc.rust-lang.org/book/)** — Chapters 1-10 cover everything you need
2. **Read the `bindings/python/src/` code** — It's the simplest Rust in the project
3. **Trace one Python API call end-to-end:**
   - Start in `bindings/python/src/admin.rs` → `create_table()`
   - Follow it into `crates/fluss/src/client/admin.rs`
   - See how RPC is dispatched in `crates/fluss/src/rpc/`
4. **Make a small Rust change** — Add a missing property getter, improve an error message
5. **Read [PyO3 docs](https://pyo3.rs/)** — Understand the `#[pyclass]`, `#[pymethods]` macros

### 10.2 Key Rust Concepts to Learn (in order)

1. **Ownership & borrowing** — `&`, `&mut`, `move`
2. **Error handling** — `Result<T, E>`, `?` operator, `.unwrap()`
3. **Pattern matching** — `match`, `if let`, `while let`
4. **Traits** — like Python ABCs / interfaces, but more powerful
5. **Generics** — `fn foo<T: Display>(x: T)` — similar to Python's `TypeVar`
6. **Lifetimes** — `'a` annotations — usually the compiler infers them
7. **Async/await** — same keywords as Python, but stricter
8. **Macros** — `macro_rules!`, proc macros — you'll see these in PyO3

### 10.3 Tools for Rust Development

```bash
# Recommended IDE: VS Code + rust-analyzer extension
# Or: RustRover (JetBrains, free for open source)

# Useful commands:
cargo check           # Fast type-checking (no binary output)
cargo test            # Run tests
cargo doc --open      # Generate and view API docs
cargo clippy          # Lint
cargo fmt             # Format
```

---

## 11. Glossary

| Term | Definition |
|---|---|
| **Arrow** | Apache Arrow — columnar in-memory data format. Fluss uses it internally and in its API. |
| **Bucket** | Unit of parallelism in a table (like a Kafka partition). |
| **Changelog** | The sequence of insert/update/delete events for a PK table. |
| **CoordinatorServer** | The Fluss server that manages metadata and leader election. |
| **Lake Snapshot** | A point-in-time snapshot of Fluss data exported to a lakehouse format. |
| **Log Table** | Append-only table (no primary key). |
| **maturin** | Build tool for Rust-based Python packages (uses PyO3). |
| **Offset** | Position of a record within a bucket. |
| **Partition** | Data subdivision by column values (e.g., by region). |
| **PK Table** | Primary Key table — supports upsert, delete, lookup. |
| **PyO3** | Rust library for building Python extensions. |
| **TabletServer** | The Fluss server that stores and serves data. |
| **Tokio** | Rust async runtime (like Python's asyncio event loop). |
| **uv** | Fast Python package manager (replacement for pip/pip-tools). |

---

## 12. Resources

| Resource | URL |
|---|---|
| Fluss Website | https://fluss.apache.org/ |
| Fluss Docs (main) | https://fluss.apache.org/docs/ |
| fluss-rust GitHub | https://github.com/apache/fluss-rust |
| fluss (main, Java) GitHub | https://github.com/apache/fluss |
| Fluss Downloads | https://fluss.apache.org/downloads/ |
| Rust Installation | https://www.rust-lang.org/tools/install |
| The Rust Book | https://doc.rust-lang.org/book/ |
| PyO3 Documentation | https://pyo3.rs/ |
| maturin Documentation | https://www.maturin.rs/ |
| uv Documentation | https://docs.astral.sh/uv/ |
| Fluss Slack | https://join.slack.com/t/apache-fluss/shared_invite/zt-33wlna581-QAooAiCmnYboJS8D_JUcYw |
| Deploy Local Cluster | https://fluss.apache.org/docs/install-deploy/deploying-local-cluster/ |

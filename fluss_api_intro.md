# Understanding Apache Fluss: Architecture and Python API

Apache Fluss is a streaming storage engine engineered for real-time analytics and designed to act as the storage layer for streaming lakehouses. It bridges the gap between traditional message queues (like Kafka) and analytical databases.

This document breaks down the end-to-end architecture, the difference between local vs. production setups, and how to rigorously work with the Python API (including why `async` is mandated).

---

## 1. The Big Picture Architecture

At a high level, Fluss consists of two server types, a metadata coordinator, and client applications.

### 1.1 The Services
*   **Coordinator Server (Master Node):** Manages the cluster topology, handles metadata (databases, tables, schemas, partitions), monitors Tablet Server health, and performs load balancing. It relies internally on ZooKeeper for leader election and strict high-availability consistency.
*   **Tablet Server (Worker Node):** The absolute workhorses of Fluss. Tablet servers physically own the data 'buckets'. When a client writes or reads data, they talk directly to the Tablet Server containing the relevant bucket. They handle the low-level log writing, memory buffering, caching, and serving.
*   **Tiering Service:** (Optional in local, vital in prod) A background compaction and archival service. It continuously sweeps cold log records from the fast local SSDs of the Tablet Servers and uploads them as columnar files to cloud object stores (S3, GCS) for cheap, unified lakehouse querying.
*   **ZooKeeper:** The underlying nervous system of the clustered State.

### 1.2 The Clients
Clients are how your applications talk to the Fluss servers.
*   **Java Client (Native):** The primary, feature-complete client. Typically used entirely within Apache Flink integration (Flink SQL, Flink DataStream API).
*   **Python Client (`fluss-python` / `fluss-rust`):** A high-performance Rust core wrapped in Python bindings (`PyO3`/`maturin`). This allows Python microservices, AI inference pipelines, or Jupyter notebooks to directly read/write streaming data without needing a heavy JVM or Flink cluster.

---

## 2. Bootstrapping: Local vs. Production

### 2.1 The Local Developer Setup (What we are doing now)
For local development, we want all the power of the cluster running in a single process to save memory and complexity.

*   You download the `fluss-*-bin.tgz` release.
*   You run `./bin/local-cluster.sh` or `./bin/start-cluster.sh`.
*   **What happens under the hood:** The script launches a single Java Virtual Machine (JVM). Inside this JVM, it spins up an embedded ZooKeeper instance, one Coordinator Server thread, and one Tablet Server thread. It binds to local TCP ports (typically 2181 for ZK, and dynamic or 9123 for RPCs).
*   **Admin/Execution:** You have two choices. You can use the Java CLI (`./bin/fluss-admin.sh`) to create databases and tables. **OR**, because the Python API is full-featured, you can bootstrap the *entire structural state* directly via Python's `get_admin()`.

### 2.2 The Production Microservices Architecture
In an enterprise environment (AWS, GCP, Kubernetes), the setup is vastly different. You never use `local-cluster.sh`.

1.  **Deployment:** A dedicated ZooKeeper cluster (or managed service) is spun up.
2.  **Coordinator Deployment:** A Kubernetes deployment of 3 Coordinator Servers (for HA) is started. They talk to ZooKeeper to elect a leader.
3.  **Tablet Deployment:** A Kubernetes StatefulSet of 10-100 Tablet Servers is started. They attach to fast NVMe local SSDs. They register their IP addresses with ZooKeeper.
4.  **Admin Bootstrapping:** Production pipelines *rarely* create tables via application code (Python). Instead, DevOps engineers use **Terraform** or the **Java REST/CLI tools** integrated into a CI/CD pipeline to create databases, define schemas, and configure bucket counts (partitioning) before the applications ever run.
5.  **Execution (Python):** The Python microservices running in Docker containers boot up. They load `Config.bootstrap_servers` with the DNS name of the Coordinator Servers. They do *not* create tables. They simply call `client.get_table()` and start writing or reading.

---

## 3. The Python API & Asynchronous I/O (`asyncio`)

The Fluss Python Client uses `async`/`await` pervasively. Why? Because streaming storage revolves entirely around high-throughput network I/O.

If the client blocked the Python thread every time it sent a batch of records or asked for the next log chunk over TCP, the Global Interpreter Lock (GIL) and CPU would stall. By using `asyncio` built on top of Rust's `tokio` asynchronous runtime, the client can multiplex thousands of network requests concurrently without blocking.

### 3.1 Establishing the Connection
The initialization is always async because making a TCP handshake to a remote cluster takes time.

```python
import asyncio
from fluss import FlussConnection, Config

async def connect():
    conf = Config()
    conf.bootstrap_servers = "coordinator.production.internal:9123"
    # This yields control back to the event loop while the TCP handshake occurs
    client = await FlussConnection.create(conf)
    return client
```

### 3.2 The `FlussAdmin` API (DDL)
`FlussAdmin` is used for **Data Definition Language (DDL)** operations. It manages the structural layout of your streaming warehouse.

*   `create_database(name)`
*   `create_table(path, descriptor)`
*   `list_partition_offsets(path, partition, buckets, offset_spec)`

In production, you rarely use these in Python. In local development or testing, they are invaluable for quickly spinning up ephemeral tables.

### 3.3 The Write Path: `TableAppend` and `UpsertWriter` (DML)
Once a table exists, you do not write to the "schema" directly. You create a session (a Writer) attached to the specific table.

```python
table = await client.get_table(table_path)

# Create an append-only writer (for logging/event sourcing)
writer = table.new_append().create_writer()

# The append operation is actually SYNCHRONOUS in Python.
# Why? Because internally, Rust immediately writes the row to a local memory buffer
# and returns instantly. It does not wait for network I/O.
writer.append({"id": 1, "action": "click"})
writer.append({"id": 2, "action": "scroll"})

# The flush operation is ASYNC. This forces the Rust thread to take all buffered
# records, serialize them into Apache Arrow format, construct a gRPC/RPC packet,
# and send it over the wire to the Tablet Server. You MUST await this.
await writer.flush()
```

### 3.4 The Read Path: `TableScan` and `LogScanner`
Reading data is complex because Fluss tables are distributed across "Buckets". A single table might have 10 buckets spread across 5 Tablet Servers.

To read data, you configure a `TableScan`, instantiate a `LogScanner`, and conceptually "subscribe" to specific shards of the data.

```python
table = await client.get_table(table_path)

# Configure the scanner (e.g., project only specific columns to save bandwidth)
scan_builder = table.new_scan().project_by_name(["action"])

# Instantiate the engine
scanner = await scan_builder.create_log_scanner()

# Tell the backend exactly which bucket and starting offset you want to listen to.
from fluss import EARLIEST_OFFSET
scanner.subscribe(bucket_id=0, start_offset=EARLIEST_OFFSET)
```

#### The Polling Problem & Issue #424
Currently, reading records out of the `LogScanner` requires you to loop manually and pass a timeout. It looks like this:

```python
while True:
    # We call poll() and pass a timeout (e.g. 5000ms).
    # If no data arrives from the server in 5s, it returns an empty set.
    records = scanner.poll(timeout_ms=5000)
    for index, record in enumerate(records):
        print(record.row)
```
This is clunky and non-Pythonic. Modern Python utilizes asynchronous iterators (`__aiter__` and `__anext__`) to seamlessly yield values as they arrive over the network, drastically simplifying the code:

```python
# The goal of Issue #424!
async for record in scanner:
    print(record.row)
```

Under the hood, the Rust `__anext__` implementation must call `poll()` against the internal `tokio` runtime, buffer the results, and asynchronously yield them back across the FFI boundary to Python one by one.

---

## 4. Summary of the Flow

1.  **Cluster Configuration:** Start servers (local script, or K8s).
2.  **Metadata Definition:** Create Database -> Create Table -> Define Schema/Buckets (via Python `admin` API locally, or Java/Terraform in Prod).
3.  **Connection:** Python `FlussConnection` reads config and handshakes with the Coordinator.
4.  **Producing:** Get `FlussTable` -> Create Writer -> synchronously `append()` to memory -> asynchronously `flush()` to Tablet Servers.
5.  **Consuming:** Get `FlussTable` -> Configure `TableScan` -> Create `LogScanner` -> `subscribe()` to buckets -> enter `poll()` or `async for` loop to continuously receive streaming Arrow batches from Tablet Servers over TCP.

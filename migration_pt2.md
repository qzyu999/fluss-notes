# ContainerClaw Migration Plan: Part 2 - Simplified Fluss Integration

This plan focuses on replacing the existing "Mock Fluss" (JSONL-based Flask app) with a real **Apache Fluss** cluster and updating the `claw-agent` to use the native Python bindings for event streaming.

## 1. Goal
Integrate a real Fluss cluster into the `containerclaw` stack and update the single-agent communication to be fluss-native, without yet adding multi-agent complexity or elections.

## 2. Infrastructure Changes (`docker-compose.yml`)

We will adopt the official Fluss Docker deployment strategy. This includes a `shared-tmpfs` volume which Fluss uses as a mock remote storage tier.

### New Configuration Snippet:
```yaml
services:
  zookeeper:
    image: zookeeper:3.9.2
    restart: always

  coordinator-server:
    image: apache/fluss:0.9.0-incubating
    command: coordinatorServer
    depends_on:
      - zookeeper
    environment:
      - |
        FLUSS_PROPERTIES=
        zookeeper.address: zookeeper:2181
        bind.listeners: INTERNAL://coordinator-server:0, CLIENT://coordinator-server:9123
        advertised.listeners: CLIENT://coordinator-server:9123
        internal.listener.name: INTERNAL
        remote.data.dir: /tmp/fluss/remote-data
    ports:
      - "9123:9123"

  tablet-server:
    image: apache/fluss:0.9.0-incubating
    command: tabletServer
    depends_on:
      - coordinator-server
    environment:
      - |
        FLUSS_PROPERTIES=
        zookeeper.address: zookeeper:2181
        bind.listeners: INTERNAL://tablet-server:0, CLIENT://tablet-server:9123
        advertised.listeners: CLIENT://tablet-server:9123
        internal.listener.name: INTERNAL
        tablet-server.id: 0
        kv.snapshot.interval: 0s
        data.dir: /tmp/fluss/data
        remote.data.dir: /tmp/fluss/remote-data
    ports:
        - "9124:9123"
    volumes:
      - shared-tmpfs:/tmp/fluss

volumes:
  shared-tmpfs:
    driver: local
    driver_opts:
      type: "tmpfs"
      device: "tmpfs"
```

## 3. Integration & Wiring

For these services to coexist with the existing ContainerClaw stack, they must be correctly wired through Docker networks and environmental variables.

### 3.1 The Dependency Chain
1.  **Zookeeper** is the "source of truth" for the cluster. It must start first.
2.  **Coordinator-Server** connects to Zookeeper (`zookeeper:2181`) to register itself and manage cluster metadata.
3.  **Tablet-Server** connects to both Zookeeper and the Coordinator. It uses the `shared-tmpfs` volume to store data that it "offloads" (simulating tiered storage).
4.  **Claw-Agent** (your existing agent) is updated to point to `coordinator-server:9123` via the `FLUSS_BOOTSTRAP_SERVERS` environment variable.

### 3.2 Networking (Internal vs. External)
We use a dual-listener approach in the configuration:
- **INTERNAL**: Used for high-speed communication between the coordinator and tablet servers.
- **CLIENT**: Used by the `claw-agent` and your notebooks.
    - Inside Docker: Agents use `coordinator-server:9123`.
    - Outside Docker: Notebooks use `localhost:9123` (bridged via `ports` mapping).

## 4. Agent Changes (`agent/src/main.py`)

### 3.1 Dependencies
Update `agent/requirements.txt` to include:
- `fluss`
- `pyarrow`

### 3.2 Initialization
The `AgentService` will initialize a `FlussConnection` on startup and ensure the `chatroom` table exists.

### 3.3 Emit Logic (`_emit` method)
Replace the `requests.post` call with a native Fluss append:
```python
async def _emit_fluss(self, session_id, e_type, content):
    # Prepare row for Arrow-compatible schema
    row = {
        "timestamp": datetime.now(timezone.utc),
        "session_id": session_id,
        "actor_id": "claw-agent",
        "event_type": e_type,
        "content": content
    }
    await self.fluss_writer.append(row)
```

## 4. Verification Plan

### 4.1 Automated Tests
Update `scripts/verify_agent.py` to use a `FlussConnection` and `LogScanner` to verify that the "finish" event arrives in the `chatroom` table.

### 4.2 Manual Verification
1. `docker compose up --build`
2. Run `scripts/verify_agent.py`
3. Check `tablet-server` logs to ensure data is being written to the WAL.

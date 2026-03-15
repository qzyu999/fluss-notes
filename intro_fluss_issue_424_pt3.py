#!/usr/bin/env python
# coding: utf-8

# # Demonstrating the Implementation of Issue #424: Async LogScanner Iteration
# 
# In this notebook, we will demonstrate the new Python API ergonomics for reading from an Apache Fluss `LogScanner`.
# Previously, achieving record consumption required manually polling batches (`scanner.poll()`), checking timeouts, extracting iterators from internal structures (`ScanRecords`), and unwieldy while-loops.
# 
# On our new PyO3 Rust branch `feat/424-python-async-iterator`, we have implemented the Python standard `__aiter__` and `__anext__` dunder methods on the `LogScanner`.
# 
# ### Under the Hood
# Because the Python `await` unblocks the internal event loop, the Rust boundary cannot simply hold a borrowed `&mut self` pointing back to the PyO3 class. To solve this, we wrapped the internal state inside a `Arc<tokio::sync::Mutex<ScannerState>>`.
# 
# This notebook gives a true "Before vs. After" side-by-side comparison using a live local server!

# ### Step 1: Connecting to the Cluster
# Make sure your Fluss cluster is running locally. (In another terminal, ensure the server is bound to localhost).

# In[1]:


import fluss
print(fluss.__file__)


# In[2]:


import asyncio
import traceback
import pyarrow as pa

# This automatically grabs the native Rust-compiled wheel you built via `maturin develop`
import fluss

SERVER_URL = "localhost:9123" # Or adjust to your local port

async def setup_cluster():
    conf = fluss.Config()
    conf.bootstrap_servers = SERVER_URL
    
    connection = await fluss.FlussConnection.create(conf)
    admin = await connection.get_admin()
    
    print("Successfully connected to the admin endpoint!")
    return connection, admin

connection, admin = await setup_cluster()


# In[3]:


table_path = fluss.TablePath("fluss", "async_iterator_demo")

async def provision_table():
    if await admin.table_exists(table_path):
        await admin.drop_table(table_path, ignore_if_not_exists=True)
        
    schema = fluss.Schema(
        pa.schema([
            pa.field("id", pa.int32()),
            pa.field("event_name", pa.string()),
            pa.field("value", pa.float64())
        ])
    )
    
    descriptor = fluss.TableDescriptor(schema)
    await admin.create_table(table_path, descriptor, ignore_if_exists=False)
    print(f"Provisioned '{table_path}' successfully.")

    table = await connection.get_table(table_path)
    writer = table.new_append().create_writer()
    
    print("Publishing records...")
    for i in range(10):
        writer.append({"id": i, "event_name": f"user_click_{i}", "value": i * 2.5})
    await writer.flush()
    print("Records generated!")

await provision_table()


# ### Step 2: The Old Way (Before Issue #424)
# This was the legacy looping mechanism. It feels very Java-like and demands timeout injection, explicit extraction, and breaks pythonic semantics.

# In[4]:


async def demo_legacy_polling():
    table = await connection.get_table(table_path)
    scanner = await table.new_scan().create_log_scanner()
    
    num_buckets = (await admin.get_table_info(table_path)).num_buckets
    scanner.subscribe_buckets({i: fluss.EARLIEST_OFFSET for i in range(num_buckets)})
    
    print("----- Legacy Reading Start -----")
    # Note we aren't `await`ing anything besides custom loops
    polled_count = 0
    for _ in range(3): # Poll loop
        scan_records = scanner.poll(2000) # blocking 2 second timeout 
        
        for record in scan_records:
            print(f"Polled Row: {record.row}")
            polled_count += 1
            
        if polled_count >= 10:
            break

    print("----- Legacy Reading Finish -----")
    
await demo_legacy_polling()


# ### Step 3: The New Pythonic Way (After Issue #424)
# Now we can just use `async for`! The stream will cleanly yield items back into the Python EventLoop one by one until the backend detects closure. No manual timeout parsing, no nested loops.

# In[5]:


async def demo_async_for_loop():
    table = await connection.get_table(table_path)
    scanner = await table.new_scan().create_log_scanner()
    
    num_buckets = (await admin.get_table_info(table_path)).num_buckets
    scanner.subscribe_buckets({i: fluss.EARLIEST_OFFSET for i in range(num_buckets)})
    
    print("----- Async Iteration Reading Start -----")
    
    count = 0
    try:
        # Beautiful and idiomatic!
        async for record in scanner:
            print(f"Yielded Row asynchronously: {record.row}")
            count += 1
            
            if count >= 10:
                break
                
    except Exception as e:
        print(f"Stream complete or error triggered: {e}")
        traceback.print_exc()

    print("----- Async Iteration Reading Finish -----")

await demo_async_for_loop()


# ### Step 4: Compiling and Testing the Core Locally
# If you want to compile these changes locally and push them to Github, here is the official workflow as documented in `DEVELOPMENT.md`:
# 
# ```bash
# # 1. Initialize UV bindings 
# cd bindings/python
# uv sync --all-extras
# source .venv/bin/activate
# 
# # 2. Re-compile the PyO3 Rust extension specifically into your local virtual environment
# maturin develop
# 
# # 3. Run the automated integration test matrix
# # Ensure you have Docker running locally as `testcontainers` invokes `docker run`
# pytest test/test_log_table.py -k "test_async_iterator" -v
# ```
# 

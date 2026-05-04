# Issue #387: End-to-End MAP Column Support in fluss-rust

> **Depends on:** [#386 (Array support)](https://github.com/apache/fluss-rust/issues/386) — merged via commit `f0e17a4`
>
> **Java references:**
> - [`BinaryMap.java`](https://github.com/apache/fluss/blob/main/fluss-common/src/main/java/org/apache/fluss/row/BinaryMap.java)
> - [`MapSerializer.java`](https://github.com/apache/fluss/blob/main/fluss-common/src/main/java/org/apache/fluss/row/serializer/MapSerializer.java)

---

## 1. Overview

A Map in Fluss is stored as two parallel `BinaryArray`s (keys + values) prefixed by the key array's byte size.
This builds directly on the `BinaryArray` infrastructure from #386.

### Binary Layout (from `BinaryMap.java`)

```text
[4 bytes: keyArraySizeInBytes] + [Key BinaryArray bytes] + [Value BinaryArray bytes]
```

The Java `BinaryMap.pointTo()` method:
1. Reads 4 bytes at offset → `keyArraySizeInBytes`
2. Points the key array to `offset + 4` with length `keyArraySizeInBytes`
3. Points the value array to `offset + 4 + keyArraySizeInBytes` with length `totalSize - 4 - keyArraySizeInBytes`

### Java `BinaryMap.valueOf()` (construction)

```java
public static BinaryMap valueOf(BinaryArray keyArray, BinaryArray valueArray) {
    // [4 bytes key size] + [key array bytes] + [value array bytes]
    byte[] keyBytes = keyArray.toBytes();
    byte[] valueBytes = valueArray.toBytes();
    byte[] data = new byte[4 + keyBytes.length + valueBytes.length];
    UNSAFE.putInt(data, BYTE_ARRAY_BASE_OFFSET, keyBytes.length);
    System.arraycopy(keyBytes, 0, data, 4, keyBytes.length);
    System.arraycopy(valueBytes, 0, data, 4 + keyBytes.length, valueBytes.length);
    // ... pointTo(data, ...)
}
```

### Java `MapSerializer` (serialization)

```java
public BinaryMap serialize(InternalMap map) {
    BinaryArray keyArray  = keySerializer.serialize(map.keyArray());
    BinaryArray valueArray = valueSerializer.serialize(map.valueArray());
    return BinaryMap.valueOf(keyArray, valueArray);
}
```

The serializer delegates to two `ArraySerializer` instances (one for keys, one for values), then combines them with `BinaryMap.valueOf()`.

---

## 2. Current State of the Codebase

### What exists

| Component | Array Support | Map Support | Location |
|-----------|:---:|:---:|----------|
| `DataType::Map(MapType)` | n/a | ✅ | `metadata/datatype.rs` |
| `DataTypes::map()` factory | n/a | ✅ | `metadata/datatype.rs:1190` |
| `MapType` struct (key/value types) | n/a | ✅ | `metadata/datatype.rs:925` |
| `Datum` enum variant | ✅ `Array` | ❌ | `row/datum.rs` |
| `InternalRow::get_map()` | ✅ `get_array()` | ❌ | `row/mod.rs:131` |
| `GenericRow` match arm | ✅ | ❌ | `row/mod.rs:289-296` |
| `FlussArray` binary format | ✅ | n/a | `row/binary_array.rs` |
| `FlussMap` binary format | n/a | ❌ | needs new code |
| `BinaryWriter::write_map()` | n/a | ❌ | `row/binary/binary_writer.rs` |
| `CompactedRowWriter::write_map()` | n/a | ❌ | `row/compacted/compacted_row_writer.rs` |
| `CompactedRowDeserializer` Map arm | ✅ Array arm | ❌ | `row/compacted/compacted_row_reader.rs:185-189` |
| `CompactedRow::get_map()` | ✅ `get_array()` | ❌ | `row/compacted/compacted_row.rs:170` |
| `ValueWriter` / `InnerValueWriter::Map` | ✅ `Array` | ❌ | `row/binary/binary_writer.rs:138,178` |
| `FieldGetter` / `InnerFieldGetter::Map` | ✅ `Array` | ❌ TODO on line 85 | `row/field_getter.rs` |
| `CompactedKeyWriter` Map rejection | n/a | ✅ | `row/compacted/compacted_key_writer.rs:54` |
| Key encoder Map test | n/a | ✅ | `row/encode/compacted_key_encoder.rs:321-335` |
| `calculate_fix_length_part_size` for Map | n/a | ✅ returns 8 | `row/binary_array.rs:64` |
| `ColumnWriter` (Arrow) Map variant | ✅ `List` | ❌ | `row/column_writer.rs` |
| `FlussArray::get_map()` | n/a | ❌ | `row/binary_array.rs` |

### Existing TODOs referencing Map

1. `field_getter.rs:85` — *"TODO: add Map and Row variants when get_map/get_row are available in InternalRow."*
2. `field_getter.rs:186` — *"TODO: add Map and Row field getter support once their binary forms are implemented."*
3. `compacted_key_encoder.rs:366` — *"TODO: Add support for MAP type"* (in the all-data-types test)

### What's already handled

- **Key encoder rejection**: `CompactedKeyWriter::create_value_writer()` already rejects `DataType::Map(_)` with an error (line 54 of `compacted_key_writer.rs`).
- **Key encoder test**: `test_map_type_rejected_as_key` already validates this behavior.

---

## 3. Implementation Plan

### 3.1 New struct: `FlussMap` (in `row/binary_map.rs`)

A `FlussMap` wraps the binary layout `[4B key_array_size] + [key_array] + [value_array]` and provides access to its constituent `FlussArray`s.

```rust
/// A Fluss binary map, wire-compatible with Java's `BinaryMap`.
///
/// Binary layout:
/// [4 bytes: key array size] + [Key BinaryArray] + [Value BinaryArray]
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize)]
pub struct FlussMap {
    data: Bytes,
    key_array: FlussArray,
    value_array: FlussArray,
}

impl FlussMap {
    /// Construct from raw bytes (validates layout).
    pub fn from_bytes(data: &[u8]) -> Result<Self> { ... }

    /// Construct from owned Bytes (zero-copy).
    pub fn from_owned_bytes(data: Bytes) -> Result<Self> { ... }

    /// Construct from two FlussArrays (creates the binary representation).
    pub fn from_arrays(key_array: &FlussArray, value_array: &FlussArray) -> Self { ... }

    /// Number of entries.
    pub fn size(&self) -> usize { self.key_array.size() }

    /// Returns the key array.
    pub fn key_array(&self) -> &FlussArray { &self.key_array }

    /// Returns the value array.
    pub fn value_array(&self) -> &FlussArray { &self.value_array }

    /// Returns the raw bytes.
    pub fn as_bytes(&self) -> &[u8] { &self.data }
}
```

**Key design decisions:**
- Like `FlussArray`, uses `Bytes` for O(1) cloning
- `from_arrays` mirrors Java's `BinaryMap.valueOf()` — builds the `[4B][keys][values]` buffer
- Validation: ensures data ≥ 4 bytes, key array size is valid, both sub-arrays parse

### 3.2 `Datum` enum: add `Map` variant

In `row/datum.rs`:

```rust
pub enum Datum<'a> {
    // ... existing variants ...
    Array(FlussArray),
    Map(FlussMap),  // NEW
    // ...
}
```

Add:
- `is_map()` / `as_map()` accessors
- `From<FlussMap> for Datum`
- `append_to` Arrow conversion (uses Arrow `MapArray` or `MapBuilder`)
- `Display` / `Debug` formatting

### 3.3 `InternalRow` trait: add `get_map()`

In `row/mod.rs`:

```rust
pub trait InternalRow: Send + Sync {
    // ... existing methods ...

    /// Returns the array value at the given position
    fn get_array(&self, pos: usize) -> Result<FlussArray>;

    /// Returns the map value at the given position
    fn get_map(&self, pos: usize) -> Result<FlussMap>;  // NEW
}
```

### 3.4 `GenericRow`: implement `get_map()`

In `row/mod.rs`, add a match arm in the `InternalRow` impl for `GenericRow`:

```rust
fn get_map(&self, pos: usize) -> Result<FlussMap> {
    match self.get_value(pos)? {
        Datum::Map(m) => Ok(m.clone()),
        other => Err(IllegalArgument {
            message: format!("type mismatch at position {pos}: expected Map, got {other:?}"),
        }),
    }
}
```

### 3.5 `BinaryWriter` trait: add `write_map()`

In `row/binary/binary_writer.rs`:

```rust
pub trait BinaryWriter {
    // ... existing ...
    fn write_array(&mut self, value: &[u8]);
    fn write_map(&mut self, value: &[u8]);  // NEW — same wire format as write_array
}
```

Both `write_array` and `write_map` delegate to `write_bytes` — they're byte-blobs of different semantic types.

### 3.6 `CompactedRowWriter`: implement `write_map()`

In `row/compacted/compacted_row_writer.rs`:

```rust
fn write_map(&mut self, value: &[u8]) {
    self.write_bytes(value)
}
```

### 3.7 `CompactedKeyWriter`: delegate `write_map()`

In `row/compacted/compacted_key_writer.rs`, add the delegation:

```rust
fn write_map(&mut self, value: &[u8]);  // delegate to self.delegate
```

### 3.8 `InnerValueWriter`: add `Map` variant

In `row/binary/binary_writer.rs`:

```rust
pub enum InnerValueWriter {
    // ... existing ...
    Array,
    Map,  // NEW
}
```

In `create_inner_value_writer`:
```rust
DataType::Map(_) => Ok(InnerValueWriter::Map),
```

In `write_value`:
```rust
(InnerValueWriter::Map, Datum::Map(m)) => {
    writer.write_map(m.as_bytes());
}
```

### 3.9 `CompactedRowDeserializer`: add Map deserialization

In `row/compacted/compacted_row_reader.rs`, after the `DataType::Array` arm:

```rust
DataType::Map(_) => {
    let (bytes, next) = reader.read_bytes(cursor)?;
    let map = crate::row::binary_map::FlussMap::from_bytes(bytes)?;
    (Datum::Map(map), next)
}
```

### 3.10 `CompactedRow`: implement `get_map()`

In `row/compacted/compacted_row.rs`:

```rust
fn get_map(&self, pos: usize) -> Result<crate::row::FlussMap> {
    self.decoded_row()?.get_map(pos)
}
```

### 3.11 `FieldGetter` / `InnerFieldGetter`: add Map variant

In `row/field_getter.rs`:

```rust
// In the create() match:
DataType::Map(_) => InnerFieldGetter::Map { pos },

// New variant:
Map { pos: usize },

// In get_field():
InnerFieldGetter::Map { pos } => Datum::Map(row.get_map(*pos)?),

// In pos():
| Self::Map { pos } => *pos,
```

### 3.12 `FlussArray`: add `get_map()` method

In `row/binary_array.rs`, add a method to read a nested map (same as `get_array` but parses as `FlussMap`):

```rust
pub fn get_map(&self, pos: usize) -> Result<FlussMap> {
    let (start, len) = self.read_var_len_span(pos)?;
    FlussMap::from_owned_bytes(self.data.slice(start..start + len))
}
```

And in the `InternalRow` impl for `FlussArray`:
```rust
fn get_map(&self, pos: usize) -> Result<FlussMap> {
    self.get_map(pos)
}
```

### 3.13 `FlussArrayWriter`: add `write_map()` method

```rust
pub fn write_map(&mut self, pos: usize, value: &FlussMap) {
    self.write_bytes_to_var_len_part(pos, value.as_bytes());
}
```

### 3.14 Module exports

In `row/mod.rs`:
```rust
pub mod binary_map;
pub use binary_map::FlussMap;
```

### 3.15 Key encoder all-types test

Update `test_all_data_types_java_compatible` in `compacted_key_encoder.rs` to include a Map column (the existing TODO on line 366). Note: Maps are **rejected** as key types, so this test would verify that behavior, or the Map is added as a non-key column if the test structure permits.

---

## 4. File Change Summary

| File | Change Type | Description |
|------|:-----------:|-------------|
| `row/binary_map.rs` | **NEW** | `FlussMap` struct with `from_bytes`, `from_arrays`, accessors |
| `row/mod.rs` | MODIFY | Add `pub mod binary_map`, `pub use FlussMap`, `get_map()` to `InternalRow` trait + `GenericRow` impl |
| `row/datum.rs` | MODIFY | Add `Map(FlussMap)` variant, `is_map()`, `as_map()`, `From`, `append_to` |
| `row/binary/binary_writer.rs` | MODIFY | Add `write_map()` to `BinaryWriter` trait, `Map` variant to `InnerValueWriter` |
| `row/compacted/compacted_row_writer.rs` | MODIFY | Implement `write_map()` |
| `row/compacted/compacted_key_writer.rs` | MODIFY | Delegate `write_map()` |
| `row/compacted/compacted_row_reader.rs` | MODIFY | Add `DataType::Map` arm in deserializer |
| `row/compacted/compacted_row.rs` | MODIFY | Add `get_map()` delegation |
| `row/field_getter.rs` | MODIFY | Add `Map` variant to both `FieldGetter::create` and `InnerFieldGetter` |
| `row/binary_array.rs` | MODIFY | Add `get_map()` to `FlussArray`, `write_map()` to `FlussArrayWriter`, `get_map()` to `InternalRow` impl |
| `row/column_writer.rs` | MODIFY | Add `Map` TypedWriter variant (Arrow `MapBuilder`) |
| `row/encode/compacted_key_encoder.rs` | MODIFY | Update all-types test to cover Map |

---

## 5. Implementation Order (Dependency-Driven)

```
1. binary_map.rs        (FlussMap — no deps on other changes)
2. datum.rs             (Map variant — needs FlussMap)
3. mod.rs               (InternalRow::get_map, GenericRow, exports — needs Datum::Map)
4. binary_writer.rs     (BinaryWriter::write_map, InnerValueWriter::Map — needs Datum::Map)
5. compacted_row_writer (write_map impl — needs BinaryWriter change)
6. compacted_key_writer (delegate write_map — needs BinaryWriter change)
7. compacted_row_reader (Map deserialization — needs FlussMap, Datum::Map)
8. compacted_row.rs     (get_map delegation — needs InternalRow::get_map)
9. field_getter.rs      (Map variant — needs InternalRow::get_map)
10. binary_array.rs     (get_map, write_map, InternalRow impl — needs FlussMap)
11. column_writer.rs    (Arrow Map support — needs InternalRow::get_map)
12. key_encoder tests   (update all-types test)
```

---

## 6. Key Design Considerations

### 6.1 Why Map Reuses the Array Machinery

The binary representation of a Map is just two `BinaryArray`s glued together. This means:
- No new binary format primitives are needed
- `FlussMap` can be built entirely from `FlussArray` operations
- Serialization is `write_bytes(map.as_bytes())` — identical to how arrays are written

### 6.2 Why Maps Are Rejected as Key Types

Maps don't have a deterministic sort order — iterating a `HashMap` yields arbitrary ordering, which makes them unsuitable for key encoding. The `CompactedKeyWriter::create_value_writer` already enforces this (line 54).

### 6.3 Arrow Conversion Strategy

Arrow represents maps as `MapArray`, which is internally a `ListArray` of `StructArray<[key, value]>`. The `ColumnWriter` needs a `Map` variant that:
1. Calls `get_map(pos)` to get the `FlussMap`
2. Iterates over key and value arrays in parallel
3. Appends entries to a `MapBuilder`

### 6.4 Null Handling

- Map keys are **never null** (enforced by Fluss schema validation at table creation)
- Map values **can be null** (tracked by the value array's null bitmap)
- The map column itself can be null (tracked by the row's null bitmap)

---

## 7. Test Plan

### Unit Tests (in `binary_map.rs`)

| Test | Description |
|------|-------------|
| `test_round_trip_int_to_string_map` | Map<INT, STRING> — write then read |
| `test_round_trip_string_to_int_map` | Map<STRING, INT> |
| `test_empty_map` | 0-entry map serialization/deserialization |
| `test_map_with_null_values` | Map<INT, nullable STRING> — verify null bitmap |
| `test_from_arrays` | `FlussMap::from_arrays` matches `from_bytes` round-trip |
| `test_invalid_data` | Short data, corrupt key array size |

### Integration Tests (in `compacted_row.rs`)

| Test | Description |
|------|-------------|
| `test_compacted_row_int_string_map` | Write/read Map<INT, STRING> through compacted row |
| `test_compacted_row_map_with_nulls` | Nullable map column (null at row level) |
| `test_compacted_row_nested_map` | Map<STRING, ARRAY<INT>> |
| `test_compacted_row_empty_map` | Empty map through compacted row |

### Field Getter Tests (in `field_getter.rs`)

| Test | Description |
|------|-------------|
| `test_field_getter_map` | Map datum through FieldGetter |
| `test_field_getter_nullable_map` | Null map through FieldGetter |

### Key Encoder Tests (in `compacted_key_encoder.rs`)

| Test | Description |
|------|-------------|
| `test_map_type_rejected_as_key` | Already exists ✅ |
| Update `test_all_data_types_java_compatible` | Add Map as a non-key column |

---

## 8. Reference: How Array Was Added (Commit `f0e17a4`)

The Array implementation from #386 serves as the template. The pattern for each touched file:

1. **`binary_array.rs`**: Core struct (`FlussArray`) + writer (`FlussArrayWriter`) + `InternalRow` impl
2. **`datum.rs`**: `Array(FlussArray)` variant + `as_array()` + `From` impl + `append_to` Arrow logic
3. **`mod.rs`**: `get_array()` on `InternalRow` trait + `GenericRow` implementation
4. **`binary_writer.rs`**: `write_array()` on `BinaryWriter` trait + `InnerValueWriter::Array`
5. **`compacted_row_writer.rs`**: `write_array()` → delegates to `write_bytes()`
6. **`compacted_row_reader.rs`**: `DataType::Array` arm → `read_bytes` then `FlussArray::from_bytes`
7. **`compacted_row.rs`**: `get_array()` → `self.decoded_row()?.get_array(pos)`
8. **`field_getter.rs`**: `InnerFieldGetter::Array` variant
9. **`compacted_key_writer.rs`**: `write_array()` delegation
10. **`column_writer.rs`**: `TypedWriter::List` variant for Arrow conversion

Map follows this exact same pattern — the only difference is the `FlussMap` struct wraps two `FlussArray`s instead of being a standalone binary format, and the Arrow conversion uses `MapBuilder`/`MapArray` instead of `ListArray`.

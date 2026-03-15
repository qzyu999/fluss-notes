<!--
*Thank you very much for contributing to Fluss - we are happy that you want to help us improve Fluss. To help the community review your contribution in the best possible way, please go through the checklist below, which will get the contribution into a shape in which it can be best reviewed.*

## Contribution Checklist

  - [x] Make sure that the pull request corresponds to a [GitHub issue](https://github.com/apache/fluss-rust/issues). Exceptions are made for typos in JavaDoc or documentation files, which need no issue.

  - [x] Name the pull request in the format "[component] Title of the pull request", where *[component]* should be replaced by the name of the component being changed. Typically, this corresponds to the component label assigned to the issue (e.g., [kv], [log], [client], [flink]). Skip *[component]* if you are unsure about which is the best component.

  - [x] Fill out the template below to describe the changes contributed by the pull request. That will give reviewers the context they need to do the review.

  - [x] Make sure that the change passes the automated tests, i.e., `mvn clean verify` passes. *(Note: Python tests pass locally via `pytest`)*

  - [x] Each pull request should address only one issue, not mix up code from multiple issues.


**(The sections below can be removed for hotfixes or typos)**
-->

### Purpose

<!-- Linking this pull request to the issue -->
Linked issue: close #424

This pull request completes Issue #424 by enabling standard cross-boundary native Python `async for` language built-ins over the high-performance PyO3 wrapped `LogScanner` stream instance.

### Brief change log

Previously, PyFluss developers had to manually orchestrate `while True` polling loops over network boundaries using `scanner.poll(timeout)`. This PR refactors the Python `LogScanner` iterator logic by implementing the async traversal natively via Rust `__anext__` polling bindings and Python Generator `__aiter__` context adapters:

- **State Independence**: Refactored `ScannerKind` internals into a safely buffered `Arc<tokio::sync::Mutex<ScannerState>>`. This guarantees strict thread-safety and fulfills Rust's lifetime constraints enabling unboxed state transitions inside the `python_async_runtimes` `tokio` closure.
- **Asynchronous Execution**: Polling evaluates non-blocking loops. PyFluss automatically maps Arrow records onto the `.await` future yield sequence smoothly without blocking event cycles or hardware threads directly!
- **Iterable Compliance**: To correctly resolve runtime `inspect.isasyncgen()` compliance checks within strictly versioned Python 3.12+ engines (such as modern IPython Jupyter servers), `__aiter__` dynamically generates a properly wrapped coroutine generator dynamically inside the codebase via `py.run()`. This completely masks the Python ecosystem's iterator type limitations automatically out-of-the-box.

### Tests

<!-- List UT and IT cases to verify this change -->
- [NEW] `test_log_table.py::test_async_iterator`: Integrated a testcontainers ecosystem confirming zero-configuration iteration capabilities function natively evaluating `async for record in scanner` perfectly without pipeline interruptions while yielding thousands of appended instances sequentially backwards matching existing legacy data frameworks.

### API and Format

Yes, this expands the API natively extending capabilities allowing `async for` loops gracefully. Existing user logic leveraging explicit implementations of `.poll_arrow()` or legacy functions are untouched.

### Documentation

Yes, I updated integration tests acting as live documentation proof demonstrating the capability natively. 

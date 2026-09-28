# 🧪 Mock Testing Platform — Project Plan
> A virtual hardware simulation layer to test improved code without physical instruments connected.

---

## 📋 Overview

This plan covers how to use **Claude Code** to build a mock/simulation testing platform for your codebase. The goal is to run full test suites against improved code **without requiring physical instruments, sensors, or external hardware** to be connected.

---

## 🎯 Claude Code Prompt

Copy and paste this prompt directly into Claude Code:

---

```
You are a senior software engineer. I need you to build a **mock testing platform** for this project so I can test my improved code without any physical instruments or hardware connected.

Please do the following:

### 1. Analyze the Codebase
- Scan the project and identify all hardware interfaces, instrument drivers, serial/USB/network connections, sensors, or external device dependencies.
- List every module, class, or function that communicates with physical hardware.

### 2. Create Mock/Stub Layer
- For each hardware interface found, create a corresponding **mock class or module** that:
  - Simulates realistic responses (fake sensor readings, fake status codes, fake data streams)
  - Supports configurable scenarios (e.g., normal operation, error states, timeouts, edge cases)
  - Logs all calls made to it so tests can assert behavior
- Use Python's `unittest.mock`, `pytest-mock`, or a dedicated mock module depending on the stack.

### 3. Build the Test Suite
- Create a `tests/` directory (if not present) with:
  - `conftest.py` — shared fixtures that inject mocks automatically
  - Unit tests for all core logic modules using mocks
  - Integration tests that wire mocked hardware through the full stack
  - Edge case tests: disconnection, bad data, timeouts, unexpected values
- Ensure tests are **isolated** — no test should depend on another.

### 4. Install All Dependencies
- Check the current environment for missing packages.
- Generate a `requirements-test.txt` (or update `pyproject.toml` / `package.json`) with all testing dependencies.
- Run the installation command automatically:
  - Python: `pip install -r requirements-test.txt`
  - Node.js: `npm install --save-dev`
- Verify all packages installed correctly.

### 5. Create a Test Runner Script
- Create a `run_tests.sh` (or `run_tests.bat` for Windows) script that:
  - Sets any required environment variables (e.g., `MOCK_MODE=true`)
  - Runs the full test suite
  - Outputs a clean summary report (pass/fail/coverage)
- Also configure a `Makefile` target: `make test`

### 6. Documentation
- Add a `TESTING.md` file explaining:
  - How to run tests in mock mode
  - How to add new mock instruments
  - How to toggle between mock and real hardware

Please start by analyzing the project structure, then proceed step by step. Ask me for clarification only if you find an ambiguous hardware interface that could be interpreted multiple ways.
```

---

## 🗂️ Expected Project Structure After Setup

```
project-root/
├── src/                        # Your application code
│   ├── instruments/            # Real hardware drivers
│   └── ...
├── tests/
│   ├── conftest.py             # Global fixtures & mock injection
│   ├── mocks/
│   │   ├── mock_instrument_a.py
│   │   ├── mock_instrument_b.py
│   │   └── mock_serial_port.py
│   ├── unit/
│   │   ├── test_core_logic.py
│   │   └── test_data_processing.py
│   └── integration/
│       └── test_full_pipeline.py
├── requirements-test.txt       # Test-only dependencies
├── run_tests.sh                # One-click test runner
├── Makefile                    # `make test` support
└── TESTING.md                  # How to use the mock platform
```

---

## 📦 Dependencies Claude Code Will Install

### Python Projects
| Package | Purpose |
|---|---|
| `pytest` | Test runner |
| `pytest-mock` | Mock injection via fixtures |
| `pytest-cov` | Code coverage reports |
| `pytest-asyncio` | Async test support |
| `unittest.mock` | Built-in mocking (stdlib) |
| `faker` | Generate realistic fake data |
| `responses` | Mock HTTP/REST API calls |
| `pyserial` (mock mode) | Simulate serial port instruments |

### Node.js / JavaScript Projects
| Package | Purpose |
|---|---|
| `jest` | Test runner |
| `jest-mock` | Mocking utilities |
| `sinon` | Spies, stubs, fakes |
| `nock` | Mock HTTP requests |
| `@testing-library` | Component/integration testing |

---

## 🔄 Workflow: Test Without Hardware

```
┌─────────────────────────────────┐
│        Run: make test           │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│  MOCK_MODE=true is set          │
│  conftest.py patches all        │
│  hardware imports automatically │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│  Mock instruments return        │
│  simulated, configurable data   │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│  Full test suite runs           │
│  Coverage report generated      │
│  Zero hardware required ✅      │
└─────────────────────────────────┘
```

---

## ✅ Definition of Done

- [ ] All hardware interfaces identified and mocked
- [ ] Test suite runs with `make test` or `./run_tests.sh`
- [ ] 0 tests require physical hardware
- [ ] Coverage report generated
- [ ] All dependencies documented and auto-installed
- [ ] `TESTING.md` written with clear instructions
- [ ] CI/CD ready (tests can run in GitHub Actions, GitLab CI, etc.)

---

## 💡 Tips for Best Results with Claude Code

- Run Claude Code from the **root of your project** so it can see all files.
- If you have a specific instrument (e.g., oscilloscope, PLC, spectrometer), mention it by name in the prompt for better mock fidelity.
- After Claude Code generates the mocks, run `make test` once and paste any errors back — Claude Code will fix them iteratively.
- Add `MOCK_MODE=true` to your `.env` file to permanently enable mock mode during development.

---

*Generated plan — ready to use with Claude Code*

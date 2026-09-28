# 📦 Claude Code — Windows Installer + Git Setup with Auto Documentation

Paste the prompt below into Claude Code from your project root.

---

## ✅ Claude Code Prompt

```
You are a senior software engineer and DevOps specialist. I need you to do 
two things for my instrument control software:

1. Create a portable Windows installer package
2. Initialize a Git repository with proper documentation and workflow

---

## PART 1 — WINDOWS INSTALLER

### Goal:
Someone on a fresh Windows machine (with real instruments connected) should 
be able to run ONE script and have everything working in under 10 minutes.

### Step 1: Audit all dependencies
- Scan the entire project and collect ALL imports
- Cross-reference with stdlib to identify third-party packages
- Check for any instrument-specific libraries (PySerial, PyVISA, 
  pyqtgraph, PyQt5/PySide2, etc.)
- Check config/address.py for any hardware communication protocols
  (USB, GPIB, RS232, Ethernet) to know which drivers are needed

### Step 2: Generate requirements.txt
- Create requirements.txt with ALL third-party dependencies
- Pin exact versions (use pip freeze format) for reproducibility:
  PyQt5==5.15.9
  pyqtgraph==0.13.3
  pyserial==3.5
  ...
- Add a comment above each group explaining what it's for:
  # GUI
  # Instrument communication
  # Data processing
  # Testing (optional)

### Step 3: Create install_windows.bat
Create a Windows batch file that:
  1. Checks Python is installed (>= 3.8), tells user to install if not
  2. Creates a virtual environment: python -m venv venv
  3. Activates it: venv\Scripts\activate
  4. Upgrades pip: pip install --upgrade pip
  5. Installs all requirements: pip install -r requirements.txt
  6. Checks for VISA runtime (needed for GPIB/USB instruments):
     - Detects if NI-VISA or Keysight VISA is installed
     - If not, prints a clear warning with download URL:
       https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html
  7. Checks for instrument-specific drivers based on what's in address.py
  8. Copies config/address.py.example → config/address.py if not exists
  9. Runs a quick sanity check: python -c "import PyQt5; print('OK')"
  10. Prints a SUCCESS message with instructions to run the software

### Step 4: Create config/address.py.example  
- Copy current address.py but replace all real addresses/ports with 
  placeholder values like:
    EUROTHERM_PORT = "COM3"        # Change to your COM port
    PRESSURE_ADDRESS = "GPIB::1"   # Change to your instrument address
  - Add a comment at the top:
    # Copy this file to address.py and fill in your instrument addresses
    # Use Device Manager on Windows to find COM ports

### Step 5: Create run.bat
A simple launcher for Windows:
  @echo off
  call venv\Scripts\activate
  python main.py
  pause

### Step 6: Create README_INSTALL.md
A clear, non-technical installation guide:
  - Prerequisites (Python 3.8+, where to download)
  - Step by step: run install_windows.bat
  - How to configure address.py for their instruments
  - How to find COM ports on Windows (Device Manager)
  - How to find GPIB addresses
  - How to launch: run.bat or python main.py
  - Troubleshooting section (common errors + fixes)

---

## PART 2 — GIT REPOSITORY SETUP

### Goal:
Initialize a proper Git repository with full history documentation, 
branching strategy, and automatic changelog generation.

### Step 1: Initialize Git
  git init
  
### Step 2: Create .gitignore
Create a comprehensive .gitignore for Python/PyQt/Windows:
  # Python
  __pycache__/
  *.py[cod]
  *.pyo
  venv/
  .env
  
  # Config with real addresses (NEVER commit real instrument addresses)
  config/address.py      ← real addresses stay local, never on Git
  
  # Logs and data (can be large)
  logs/
  *.log
  data/
  *.csv
  
  # IDE
  .vscode/
  .idea/
  *.swp
  
  # Windows
  Thumbs.db
  Desktop.ini
  
  # PyInstaller
  dist/
  build/
  *.spec

### Step 3: Create CHANGELOG.md
Initialize with all the work already done in this project:
  # Changelog
  All notable changes to this project are documented here.
  Format: [version] - date - description
  
  ## [0.3.0] - TODAY'S DATE
  ### Added
  - ⏱ Sync Ramp Calculator tab with critical zone support for Au
  - Material profiles system (config-driven, JSON)
  - Staggered start delay calculation for synchronized cell arrival
  - Visual timeline preview in Sync Ramp tab
  - Script generator with copy-per-tab output
  
  ## [0.2.0] - TODAY'S DATE  
  ### Added
  - Logs tab for instrument log visualization
  - Pressure moved to dedicated tab with clean timestamp axis (mbar)
  - Temperature ramp preview (offline/mock mode)
  ### Fixed
  - Script window cannot reopen after all windows closed
  - Follow-up plot stops updating after buffer limit
  - Eurotherm layout scaling for multiple controllers
  
  ## [0.1.0] - PROJECT START
  ### Added
  - Initial instrument control platform
  - Eurotherm temperature control
  - Pressure monitoring
  - Script execution engine (single-threaded per tab)
  - Power ramp and temperature ramp commands

### Step 4: Create a COMMIT_GUIDE.md
A simple guide for writing good commit messages:

  # How to commit changes
  
  ## Format:
  git commit -m "type: short description"
  
  ## Types:
  feat:     new feature
  fix:      bug fix
  docs:     documentation only
  refactor: code restructure, no behavior change
  test:     adding tests
  config:   configuration changes
  
  ## Examples:
  git commit -m "feat: add InGa material profile with critical zone"
  git commit -m "fix: pressure plot axis now shows correct mbar scale"
  git commit -m "config: update Au critical zone rate to 2°C/min"
  
  ## Updating the changelog:
  Always add an entry to CHANGELOG.md before committing a new feature.
  Use today's date and bump the version number (0.3.0 → 0.4.0)

### Step 5: Make the first commit
Stage and commit everything properly:
  git add .gitignore
  git commit -m "init: add gitignore"
  
  git add requirements.txt
  git commit -m "config: add pinned requirements for Windows deployment"
  
  git add config/address.py.example install_windows.bat run.bat README_INSTALL.md
  git commit -m "feat: add Windows installer and deployment scripts"
  
  git add CHANGELOG.md COMMIT_GUIDE.md
  git commit -m "docs: initialize changelog and commit guide"
  
  git add .
  git commit -m "feat: initial full project commit with all current features"

### Step 6: Create a develop branch
  git checkout -b develop
  
  Print instructions:
  "From now on:
   - Work on: develop branch (git checkout develop)
   - Stable code: main branch
   - To save a stable version: git checkout main && git merge develop
   - To push to GitHub later: git remote add origin <url> && git push -u origin main"

---

## FINAL CHECKLIST OUTPUT

After completing everything, print this checklist:

  ✅ DEPLOYMENT READY
  ─────────────────────────────────────────
  📦 Install files:
     [ ] requirements.txt
     [ ] install_windows.bat
     [ ] run.bat
     [ ] config/address.py.example
     [ ] README_INSTALL.md
  
  📁 Git repository:
     [ ] .gitignore (address.py excluded)
     [ ] CHANGELOG.md (full history)
     [ ] COMMIT_GUIDE.md
     [ ] Initial commits on main
     [ ] develop branch created
  
  ⚠️  MANUAL STEPS REMAINING:
     1. Create GitHub/GitLab repo and push:
        git remote add origin <your-repo-url>
        git push -u origin main
        git push -u origin develop
     2. On the Windows instrument computer:
        - Install Python 3.8+ from python.org
        - Copy project folder
        - Run install_windows.bat
        - Fill in config/address.py with real instrument addresses
        - Run run.bat
  ─────────────────────────────────────────
```

---

## 💡 Important Notes

- **address.py is in .gitignore** — your real instrument addresses 
  (COM ports, GPIB addresses, IPs) will NEVER be accidentally pushed 
  to Git. Only address.py.example with placeholders gets committed.
- **Every future feature** → add to CHANGELOG.md + commit with `feat:`
- **Every bug fix** → add to CHANGELOG.md + commit with `fix:`
- To share with the instrument computer, either push to GitHub and 
  pull there, or copy the folder via USB — then just run install_windows.bat

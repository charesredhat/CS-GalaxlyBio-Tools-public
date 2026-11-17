# MicrorunQC Bioblend Test Framework - Usage Guide

## Overview
The `complete_microrunqc_test.py` script has been updated to work with existing Galaxy histories containing FASTQ data. It provides comprehensive testing capabilities for microrunQC automation using bioblend.

Related script: `microrunqc_bioblend_test.py` performs a simpler FASTQ search using Galaxy's dataset index with explicit pagination (faster than iterating every history on large instances).

## Prerequisites
- Galaxy instance running at http://127.0.0.1:8080/
- Python virtual environment with bioblend installed
- API key configured for Galaxy access

## Installation & Setup

### 1. Virtual Environment Setup
```bash
cd $PROJECT_DIR
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Verify Installation
```bash
source .venv/bin/activate
python -c "import bioblend; print('bioblend version:', bioblend.__version__)"
```

## Usage Options

### Option 1: Using Virtual Environment (Recommended)
```bash
cd $PROJECT_DIR
source .venv/bin/activate
python complete_microrunqc_test.py [OPTIONS]
```

### Option 2: Using Wrapper Script (Easiest)
```bash
cd $PROJECT_DIR
./run_microrunqc_test.sh [OPTIONS]
```

## Command Line Options

### Basic Usage
```bash
# Interactive mode - select from available histories
./run_microrunqc_test.sh --interactive

# Use specific history by name
./run_microrunqc_test.sh --history-name "MicrorunQC FASTQ Test"

# Use specific history by ID
./run_microrunqc_test.sh --history-id 03501d7626bd192f

# Default mode - lists available histories
./run_microrunqc_test.sh
```

### Help
```bash
./run_microrunqc_test.sh --help
```

## What the Script Does

### Step 1: History Selection and Analysis
- Lists available Galaxy histories
- Allows selection by name, ID, or interactive choice
- Analyzes datasets to find FASTQ files
- Detects paired-end reads (R1/R2)

### Step 2: Tool Requirements Check
- Verifies microrunQC tool availability
- Checks for MLST database requirements
- Reports any missing dependencies

### Step 3: Testing Execution
- **Primary**: Attempts microrunQC test with existing FASTQ data
- **Fallback**: If microrunQC fails (due to missing databases), runs FastQC instead
- **Monitoring**: Tracks job progress and reports results

## Expected Outputs

### Successful Run with Existing Data
```
🧬 MicrorunQC Bioblend Test Framework
✅ Found history: MicrorunQC FASTQ Test - 1761751515
✅ Found 2 FASTQ datasets:
   📄 test_sample_R1.fastq
   📄 test_sample_R2.fastq
⚠️  MicrorunQC may fail due to missing MLST databases
✅ Alternative QC test successful!
```

### Generated Files
- `microrunqc_test_report.txt`: Detailed test report
- `microrunqc_workflow_template.json`: Workflow template for future use

## Common Issues & Solutions

### Issue: "ModuleNotFoundError: No module named 'bioblend'"
**Solution**: Make sure to activate the virtual environment:
```bash
source .venv/bin/activate
python complete_microrunqc_test.py
```
Or use the wrapper script:
```bash
./run_microrunqc_test.sh
```

### Issue: "No FASTQ datasets found"
**Solution**: The selected history doesn't contain FASTQ files. Either:
1. Create test data: `python create_test_fastq_history.py`
2. Upload FASTQ files to Galaxy manually
3. Select a different history with FASTQ data

### Issue: "MLST databases not configured"
**Expected**: This is the expected behavior. The script demonstrates:
1. MicrorunQC requires MLST databases
2. Fallback to FastQC works correctly
3. Bioblend automation is functional

## File Structure
```
galaxy-tools/
├── complete_microrunqc_test.py          # Main test framework
├── run_microrunqc_test.sh               # Wrapper script
├── create_test_fastq_history.py         # Creates test data
├── microrunqc_test_report.txt           # Generated report
├── microrunqc_workflow_template.json    # Workflow template
└── .venv/                               # Virtual environment
    └── bin/python                       # Python with bioblend
```

## Next Steps

### For Production Use
1. **Configure MLST databases** in Galaxy to enable full microrunQC functionality
2. **Set up data managers** for automated database management
3. **Create workflows** for standardized QC pipelines

### For Development
1. **Extend the framework** to support additional QC tools
2. **Add workflow automation** for complete analysis pipelines
3. **Implement batch processing** for multiple samples

## Support
- Check Galaxy instance at: http://127.0.0.1:8080/
- Review test reports for detailed findings
- Use `--help` for command line options

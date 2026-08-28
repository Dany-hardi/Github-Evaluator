# Github-Evaluator

<div align="center">

![GitHub Academic Evaluator Banner](https://img.shields.io/badge/GitHub-Academic_Evaluator-blue?style=for-the-badge&logo=github)

**Automated Code Evaluation System for Academic Institutions**

[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

*Revolutionizing academic code evaluation with AI-powered analysis and real execution*

[Features](#-features) • [Installation](#-installation) • [Quick Start](#-quick-start) • [Documentation](#-documentation) • [Contributing](#-contributing)

</div>

---

##  Overview

**GitHub Academic Evaluator** is an intelligent, open-source CLI system designed to automate the evaluation of programming assignments hosted on GitHub. It combines static code analysis, real program execution, and documentation assessment to provide objective, reproducible, and detailed grading.

###  Why This Project?

In today's educational landscape, professors spend **20+ hours per semester** manually grading programming assignments. This project:

-  **Reduces grading time by 90%**
-  **Ensures consistent, bias-free evaluation**
-  **Provides instant feedback to students**
-  **Generates comprehensive analytics**
-  **Scales to hundreds of submissions**

---

##  Key Features

###  Real Code Execution
- **Compile & Run**: Automatically compiles C/C++, Java and executes programs
- **Multi-Language Support**: C, C++, Python, Java, JavaScript, and more
- **Safety First**: Sandboxed execution with timeouts
- **Performance Metrics**: Measures execution time and memory usage

###  Intelligent Analysis
- **Static Code Analysis**: Evaluates code quality, structure, and complexity
- **Documentation Review**: Analyzes README files, comments, and technical writing
- **Multi-Criteria Grading**: Weighted scoring system (Code 40% + Execution 30% + Docs 30%)
- **Detailed Reports**: JSON output with granular metrics

###  Professional Interfaces
- **Modern GUI**: Minimalist, responsive desktop application
- **CLI Tool**: Powerful command-line interface for automation
- **Batch Processing**: Evaluate multiple groups simultaneously
- **Progress Tracking**: Real-time logs and status updates

### 📈 Export & Reporting
- **CSV Export**: Simple tabular format for spreadsheets
- **Excel Export**: Beautifully formatted reports with colors and statistics
- **JSON Data**: Detailed machine-readable results
- **Analytics Ready**: Perfect for learning analytics research

---

##  Demo

### GUI Demo
```
┌────────────────────────────────────────────────────────────────┐
│ GitHub Academic Evaluator                        📊 Export     │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  📥 Add Groups                  📊 Evaluation Console          │
│  ┌──────────────────┐           ┌────────────────────────┐    │
│  │ Group #1         │           │ 🏢 GROUP 1             │    │
│  │                  │           │ ========================│    │
│  │ Code URL:        │           │ 🔧 Analyzing code...   │    │
│  │ [____________]   │           │   📄 main.c (C)        │    │
│  │                  │           │   🔧 Compiling...      │    │
│  │ Doc URL:         │           │   ✅ Compilation OK    │    │
│  │ [____________]   │           │   ⚙️ Executing...      │    │
│  │                  │           │   ✅ Execution OK      │    │
│  │ [➕ Add] [Clear] │           │   📊 Grade: 18.5/20    │    │
│  │                  │           │                        │    │
│  │ Groups Queue:    │           │ 📚 Analyzing docs...   │    │
│  │ • Group 1        │           │   📖 README.md         │    │
│  │ • Group 2        │           │   📊 Grade: 15.0/20    │    │
│  │                  │           │                        │    │
│  │ [▶ Start Eval]   │           │ 📊 FINAL: 17.2/20     │    │
│  └──────────────────┘           └────────────────────────┘    │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│ ● Ready                                     2 groups queued   │
└────────────────────────────────────────────────────────────────┘
```

### CLI Demo
```bash
$ gh-eval evaluate --code https://github.com/student1/tp-code \
                    --doc https://github.com/student1/tp-docs

╔══════════════════════════════════════════════════════════╗
║     🎓  GitHub Academic Evaluator                       ║
║     Automated Code Evaluation System                    ║
╚══════════════════════════════════════════════════════════╝

🏢 GROUP 1
======================================================
🔧 Analyzing code...
  📂 student1/tp-code
  ✅ 3 code file(s) found
    📄 main.c (C)
       🔧 Compiling with gcc...
       ✅ Compilation successful
       ⚙️ Executing program...
       ✅ Execution successful (0.15s)
       📊 Grade: 18.5/20

📚 Analyzing documentation...
  ✅ 2 documentation file(s) found
    📖 README.md
       📊 Grade: 15.0/20

📊 RESULTS:
  Code (static):    17.5/20
  Execution:        20.0/20
  Documentation:    15.0/20
  FINAL GRADE:      17.5/20

✓ Results saved: evaluation_group1_1735829450.json
```

---

## 📦 Installation

### Prerequisites
- **Python 3.7+**
- **Git**
- **GCC/G++** (for C/C++ compilation)
- **Java JDK** (optional, for Java support)
- **Node.js** (optional, for JavaScript support)

### Quick Install

```bash
git clone https://github.com/your-username/github-evaluator.git
cd github-evaluator
p
```



---

## 🚀 Quick Start

### Method 1: Graphical Interface (Recommended for Beginners)

```bash
# Launch the GUI
gh-eval gui
```

Then:
1. Enter code repository URL
2. Enter documentation repository URL
3. Click "Add Group"
4. Repeat for all groups
5. Click "Start Evaluation"
6. Export results to CSV/Excel




## 📊 Grading System

### Overall Grade Calculation

```
Final Grade = (Code × 40%) + (Execution × 30%) + (Documentation × 30%)
```

### Code Analysis (40% weight)

| Criterion | Weight | Description |
|-----------|--------|-------------|
| **Code Presence** | 25% | Non-empty source files |
| **Code Quality** | 20% | Indentation, naming conventions |
| **Comments** | 20% | Inline documentation |
| **Functions** | 20% | Modular structure |
| **Complexity** | 15% | Algorithmic sophistication |

**Scoring:**
- 0-5 points: Code present
- 6-9 points: Comments and functions
- 10-15 points: Quality and complexity
- **Maximum: 20 points**

### Execution (30% weight)

| Criterion | Points | Description |
|-----------|--------|-------------|
| **Compilation Success** | +5 | Code compiles without errors |
| **Execution Success** | +15 | Program runs without crashes |
| **Performance Bonus** | +0-5 | Fast execution time |
| **Error Handling** | Deduction | Crashes or timeouts |

**Scoring:**
- Compilation only: 5/20
- Successful execution: 20/20
- Partial execution: 10/20
- Failed: 0/20

### Documentation (30% weight)

| Criterion | Weight | Description |
|-----------|--------|-------------|
| **Length** | 20% | Word count (min 150 words) |
| **Structure** | 25% | Headers, sections, organization |
| **Technical Content** | 30% | Keywords, terminology, concepts |
| **Code Examples** | 15% | Embedded code blocks |
| **Readability** | 10% | Grammar, clarity, formatting |

**Scoring:**
- 0-7 points: Basic documentation
- 8-14 points: Good structure and content
- 15-20 points: Excellent comprehensive docs

---



## 📈 Export Formats

### CSV Export

Simple tabular format compatible with Excel, Google Sheets, etc.

```csv
Group,Name,Matricule,Code,Execution,Documentation,Final,Files,Compilation,Status
1,Group 1,G001,16.5,18.0,15.0,16.5,5,Yes,Success
2,Group 2,G002,14.0,12.0,16.0,14.0,3,Yes,Success
```

### Excel Export

Professional formatted report with:
- ✅ Color-coded grades (green/orange/red)
- ✅ Automatic statistics (average, min, max)
- ✅ Conditional formatting
- ✅ Ready-to-print layout

### JSON Export

Detailed machine-readable format for:
- Research data analysis
- Integration with LMS systems
- Custom reporting tools

```jso## 📁 Project Structure
│   │
│   ├── export/                # Export modules
│   │   ├── csv_exporter.py
│   │   └── excel_exporter.py
│   │
│   └── utils/                 # Utilities
│       └── helpers.py
│
├── tests/                     # Unit tests
├── docs/                      # Documentation
├── examples/                  # Example configs
├── setup.py                   # Package setup
├── requirements.txt           # Dependencies
├── README.md                  # This file
└── LICENSE                    # MIT License
```

---

## 🔧 Advanced Usage

### Batch Processing

```bash
# Evaluate 100 groups automatically
for i in {1..100}; do
  gh-eval evaluate \
    --code "https://github.com/group$i/code" \
    --doc "https://github.com/group$i/docs" \
    --output "results/group$i.json"
done

# Merge all results
gh-eval merge results/*.json --output final_report.xlsx
```

### Custom Weighting
```python
evaluator = UniversalGitHubEvaluator()

# Custom weights
evaluator.set_weights(
    code=0.50,      # 50%
    execution=0.30, # 30%
    documentation=0.20  # 20%
)
```

### Disable Code Execution

```bash
# Static analysis only (faster, safer)
gh-eval evaluate --config groups.json --no-execute
```

--- n
{
  "group": 1,
  "metadata": {
    "date": "2025-10-02T14:30:00",
    "analysis_time": 45.2
  },
  "grades": {
    "final": 16.5,
    "code": 16.5,
    "execution": 18.0,
    "documentation": 15.0
  },
  "details": {
    "code_files": [...],
    "execution_results": [...],
    "documentation_files": [...]
  }
}
```

---

## 🎓 Use Cases

### 1. University Professors

**Problem:** Spending 20+ hours grading 50+ student submissions

**Solution:**
```bash
# Evaluate entire class in 30 minutes
gh-eval evaluate --config class_fall2025.json --output grades.xlsx

# Upload to LMS
gh-eval export grades.xlsx --format canvas
```

**Benefits:**
- ⏰ Save 18+ hours per assignment
- 📊 Consistent, fair grading
- 📝 Detailed feedback for each student
- 📈 Track class performance over semester

### 2. Students (Self-Assessment)

**Problem:** Uncertain about code quality before submission

**Solution:**
```bash
# Check your own work
gh-eval evaluate \
  --code https://github.com/myusername/tp-code \
  --doc https://github.com/myusername/tp-docs

# Get instant feedback
# Grade: 14.5/20
# Issues: Missing comments, no error handling
```

**Benefits:**
- ✅ Know your grade before submitting
- 🔍 Identify weaknesses
- 📚 Learn best practices
- 💯 Improve incrementally

### 3. Researchers (Learning Analytics)

**Problem:** Need large-scale code quality data

**Solution:**
```python
# Analyze 1000+ student submissions
results = analyze_semester_data('fall2025/*.json')

# Generate insights
print(f"Average grade: {results.mean()}")
print(f"Correlation (comments, grade): {results.correlation()}")
print(f"Common errors: {results.top_errors()}")
```

**Benefits:**
- 📊 Large-scale data collection
- 🔬 Reproducible experiments
- 📈 Longitudinal studies
- 📄 Publication-ready datasets

### 4. Bootcamps & Online Courses

**Problem:** Scaling technical assessment for 100+ students

**Solution:**
```bash
# Automated weekly assessments
cron: 0 0 * * 0 gh-eval batch --cohort bootcamp2025 --week $WEEK
```

**Benefits:**
- 🚀 Scale to thousands of students
- ⚡ Instant automated feedback
- 💰 Reduce TA costs
- 📊 Track student progression

---

## 🛠️ Development

### Setup Development Environment

```bash
# Clone repository
git clone https://github.com/your-username/github-evaluator.git
cd github-evaluator

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/

# Check code quality
black github_evaluator/
flake8 github_evaluator/
mypy github_evaluator/
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=github_evaluator

# Run specific test
pytest tests/test_evaluator.py::test_compile_c_code
```

### Building Distribution

```bash
# Build package
python setup.py sdist bdist_wheel

# Upload to PyPI (test)
twine upload --repository testpypi dist/*

# Upload to PyPI (production)
twine upload dist/*
```

---

##  Contributing

We welcome contributions! Here's how you can help:

###  Report Bugs

Open an issue with:
- Bug description
- Steps to reproduce
- Expected vs actual behavior
- System info (OS, Python version)

###  Suggest Features

Open an issue with:
- Feature description
- Use case / motivation
- Proposed implementation

###  Submit Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

###  Improve Documentation

- Fix typos
- Add examples
- Translate README
- Write tutorials

---

##  License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

```
MIT License

Copyright (c) 2025 [Your Name]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software...
```


---

### 💬 Get Help

- 📧 **Email**: danyhardi06@gmail.com
---


---

<div align="center">



</div>

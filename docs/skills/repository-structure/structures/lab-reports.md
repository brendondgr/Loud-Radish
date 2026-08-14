# Lab Report Repository Structure

This document outlines the standard repository formatting and directory structure required for all quantitative, computational, and general Laboratory Report assignments.

## Directory Layout

```text
root/
├── data/       # Saved outputs, generated datasets, and input data
├── images/     # Graphs, plots, figures, and visual assets
├── src/        # All source code organized logically for the assignment
├── utils/      # Compartmentalized utilities in appropriate sub-directories
├── reports/    # LaTeX root, generated PDFs, and text documents
└── Makefile    # Functioning build system configuration for code and/or LaTeX
```

## Core Requirements

1. **Structured Source Code:** Code must be organized within `src/` (or functionally logical files if small) to keep the root directory clean. 
2. **Build System:** Always formulate a functioning build system or execution script (e.g., `Makefile`, `run.sh`, `pyproject.toml`) suitable for the language used.
3. **Visualization:** Ensure data parsing and plotting scripts (e.g., `plot.py`, Jupyter notebooks) are kept explicit and segregated from processing pipelines.
4. **Typesetting:** Write and render primary reports using **LaTeX** inside a dedicated `reports/` environment.
5. **Utilities Compartmentalization:** Auxiliary scripts belong in `utils/` to decouple helper logic from main algorithmic execution.

---

## Guidelines for Submitting Laboratory Reports

The following sets of instructions must be adhered to when finalizing laboratory submissions. Reports that deviate from format may suffer deductions or feedback delays.

### 1. General Preparation
* Carefully review the lab description.
* Accurately label your problems, equations, and tasks corresponding to the lab prompts.

### 2. Submission Requirements
Your final report payload generally requires the following parameters:
* **PDF Report**: Main submitted report file name must follow a predictable format, e.g., `Lab#-Lastname-Firstname.pdf`.
* **Archival**: When submitting the full repository (code, assets, raw inputs), create a self-contained **zip** archive matching the report name: `Lab#-Lastname-Firstname.zip`.
* **Exclude Junk Data**: Never include executable binaries (`.o`, `.exe`), temporary compilation artifacts, or massive unsanitized output caches in the final `.zip`.

### 3. Figures
* **Self-Contained**: Readers must be able to describe the contents using only the information in the graphics window, including axis titles, labels, and captions.
* **Legibility**: All labeling must be easy to read.
* **Scaling**: Axis scaling should be **linear** unless explicitly requested otherwise.
* **Numbering & Referencing**: Figures must be uniquely numbered and referenced within the text.

### 4. Tables
* **Titles**: Tables must have short, appropriate titles.
* **Labeling**: Rows and columns must be uniquely named using symbols used in the text.
* **Footnotes**: Column names must be explained using footnotes attached below the table body. Use a label (e.g., "a") to link the column name to its corresponding footnote.
* **Numbering & Referencing**: Tables must be uniquely numbered and referenced within the text.

### 5. Solutions and Code
* **Reported Quantities**: Scalar values, vectors, or matrices explicitly identified in the lab description must be reported.
* **Documentation**: Comment your code extensively so the execution flow can be followed without reading the executable lines.
* **Execution**: Ensure your code compiles and completes successfully. You may be required to demonstrate correct execution if the TA is unable to compile or run your code due to software access issues.

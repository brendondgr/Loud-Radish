# Plan Creation Reference

Guidelines for the AI to generate explicit, clear, and hierarchical step-by-step problem-solving plans.

## Trigger
When a user asks to "create a plan", "plan this out", or requests structured steps to solve a problem.

## Plan Structure Guidelines
Always output the plan in clean Markdown following the exact structure below:

---

### 1. Introduction
Write 1-2 paragraphs introducing the plan. Briefly summarize the problem being solved and the overall approach.
> **Example**: 
> This plan outlines the migration of the database from SQLite to PostgreSQL. The goal is to improve scalability and data integrity for the application's production environment. 
> 
> The approach involves setting up the PostgreSQL instance, updating the application's connection settings, and performing a data migration using a custom script.

---

### 2. Gaps & Unanswered Questions
Identify any gaps in requirements, edge cases, or questions left unanswered.
- **Basic/Simple Gaps**: Provide the most logical solution or assumption and proceed.
- **Complex Gaps**: Explicitly ask the question, but state clearly: "Human intervention is needed to answer this question."

> **Example**:
> - **Database Retention Policy**: It is unknown how long historical data should be kept. *Assumption*: Retain all data initially.
> - **Security Credentials**: How should secrets be handled in the new environment? *Human intervention is needed to answer this question.*

---

### 3. Hierarchical Step-by-Step Instructions
Detail the resolution sequentially. Each step must detail what needs to be done in order for the next step to occur.
For each step, you MUST include:
- **Locations**: Explicitly detail File Names, Classes, and Functions where changes will take place.
- **Rationale**: Explain *why* these exact steps need to happen.
- **NO Large Code Blocks**: Do not write out the code implementations. Only provide the names of the parts/files involved.
- **Validation & Push**: At the end of **every** step, state the following requirement:
  > *Action: Undergo the verification/tests/validation process for this phase. Once validated, push changes to GitHub stating: [Plan Name] ([Current Step] / [Total Steps]) Complete: [Sentence describing what was done]*

> **Example**:
> #### Step 1: Initialize Database Connection
> - **Locations**: src/database/config.py, DatabaseManager class, initialize_connection function.
> - **Rationale**: We need to update the connection string to point to the new PostgreSQL instance before any other database operations can occur.
> - **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push changes to GitHub stating: DB Migration (1/3) Complete: Updated the database connection settings to PostgreSQL.

---

### 4. Deliverables Table
After all steps are complete, create a table of deliverables detailing what needs to be done and where. 
- **Tests Requirement**: The deliverables *must* include tests that run a small subset of information. These tests should be written in a local utils/ or src/ directory to test the features.

> **Example**:
> | Deliverable | Description | Location (File/Path) |
> | --- | --- | --- |
> | Database Migration Script | Python script to migrate data between databases. | src/scripts/migrate_db.py |
> | Migration Unit Tests | Unit tests to verify the migration process. | src/tests/test_migration.py |
> | Validation Utility | Utility to check data integrity after migration. | utils/validate_data.py |

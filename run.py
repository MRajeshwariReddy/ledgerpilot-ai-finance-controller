"""
Single entry point for LedgerPilot.

Usage:
    python run.py setup     # generate synthetic data + run pipeline + load DB
    python run.py dashboard # launch the Streamlit dashboard
    python run.py test      # run the automated test suite
    python run.py eval      # re-run the pipeline and print evaluation metrics
"""
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))


def setup():
    print("Step 1/2: generating synthetic data...")
    subprocess.run([sys.executable, "-m", "app.data.generate_data"], check=True)
    print("\nStep 2/2: running full pipeline (data quality -> reconciliation -> "
          "priority) and loading SQLite...")
    subprocess.run([sys.executable, "-m", "evaluation.run_evaluation"], check=True)
    print("\nSetup complete. Run `python run.py dashboard` next.")


def dashboard():
    subprocess.run(["streamlit", "run", "frontend/app.py"], check=True)


def test():
    subprocess.run(["pytest", "tests/", "-v"], check=True)


def eval_():
    subprocess.run([sys.executable, "-m", "evaluation.run_evaluation"], check=True)


COMMANDS = {"setup": setup, "dashboard": dashboard, "test": test, "eval": eval_}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]]()

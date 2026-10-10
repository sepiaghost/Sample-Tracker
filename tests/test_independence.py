"""The tracker must stand alone: the forensics toolkit reads its published format, never its code."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_nothing_imports_the_toolkit():
    offenders = []
    for base in ("sample_tracker", "tests", "scripts"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, base)):
            for f in files:
                if f.endswith(".py") and f != "test_independence.py":
                    if re.search(r"^\s*(from|import)\s+genoforensics\b", open(os.path.join(dirpath, f)).read(), re.M):
                        offenders.append(os.path.join(dirpath, f))
    assert not offenders, offenders
    assert not os.path.exists(os.path.join(ROOT, "genoforensics"))

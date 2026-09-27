import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_test_badge_matches_the_collected_suite():
    """The portfolio quotes this badge, so it must equal what pytest collects."""
    listing = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    collected = int(re.search(r"(\d+) tests? collected", listing).group(1))
    badge = re.search(r"badge/tests-(\d+)%20passing", (ROOT / "README.md").read_text(encoding="utf-8"))
    assert badge, "README needs a tests-N%20passing badge"
    assert int(badge.group(1)) == collected, f"badge says {badge.group(1)}, pytest collects {collected}"

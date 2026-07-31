import sys
import pytest

if __name__ == "__main__":
    # Run pytest on the app/tests directory
    sys.exit(pytest.main(["-v", "app/tests"]))

import os
import tempfile

# Must run before the app is imported: isolated DB + offline data.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["DATA_SOURCE"] = "synthetic"

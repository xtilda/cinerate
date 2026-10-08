import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        "CineRate requires Python 3.10+. Activate the cinerate Conda environment: conda activate cinerate"
    )
from cinerate import create_app

app = create_app()
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050)

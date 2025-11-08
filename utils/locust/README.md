# Prerequisites

1. **Python**: Ensure Python 3.7+ is installed.
2. **Locust**: Install dependencies using the provided `requirements.txt`.

   ```bash
   pip install -r requirements.txt
   ```

---

# Environment Variables

The script requires the following environment variables:

- `BASE_URL`: The base URL of the server to test (e.g., `https://your-api-endpoint.dev`).

You can provide these variables in one of two ways:

## Option 1: Use a `.env` File
Create a `.env` file in the root of this folder with the following content:

```env
BASE_URL=https://your-api-endpoint.dev
```

## Option 2: Enter Variables at Runtime
If no `.env` file is found, you will be prompted to enter the `BASE_URL` when running the script.

---

# Running the Script

To run the script, use the following command:

```bash
python3 loadgen.py
```
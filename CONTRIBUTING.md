# Contributing to OmniBridge

Thank you for your interest in contributing! 🎉

## How to Contribute

### Reporting Bugs

1. **Search existing issues** first to avoid duplicates
2. Open a new issue with:
   - A clear, descriptive title
   - Steps to reproduce
   - Expected vs actual behavior
   - Your environment (OS, Python version, Docker version if applicable)

### Suggesting Features

Open an issue tagged `enhancement` with:
- The problem it solves
- Your proposed solution or approach
- Any relevant examples

### Submitting Pull Requests

1. **Fork** the repository and create a new branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Install dev dependencies** and ensure tests pass:
   ```bash
   pip install -r requirements.txt
   python -m pytest tests/ -v
   ```

3. **Follow the existing code style:**
   - Use type hints throughout
   - Add/update docstrings for new functions and classes
   - Keep functions focused and single-purpose
   - Match the existing logging conventions (`logger.info/warning/error`)

4. **For new providers,** follow the pattern in `app/providers/gemini/`:
   - Implement `AbstractProvider` from `app/providers/base.py`
   - Register in `app/main.py` lifespan
   - Add model definitions to `config.yaml`
   - Write at least one test

5. **Open a PR** against `main` with a clear description of what was changed and why.

## Development Setup

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/OmniBridge
cd OmniBridge

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install
pip install -r requirements.txt

# Run in dev mode
uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload

# Run tests
python -m pytest tests/ -v
```

## Code Structure

```
app/
├── main.py           # Entry point — add new providers here
├── config.py         # Settings and model definitions
├── database.py       # All DB operations (async SQLite)
├── models.py         # Pydantic schemas
├── auth.py           # Authentication logic
├── providers/
│   ├── base.py       # AbstractProvider interface — implement this
│   ├── pool.py       # Load balancing — generally no changes needed
│   └── gemini/       # Reference implementation
└── routes/           # FastAPI routers
```

## Questions?

Join our Telegram channel: [@Config_Vortex55](https://t.me/Config_Vortex55)

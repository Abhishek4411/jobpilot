"""Test that all external connections are working before running JobPilot."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


def test_gemini() -> bool:
    """Test Gemini API connection."""
    try:
        from core.llm_router import call
        result = call("Say the word OK and nothing else.", task_type="default", max_tokens=10)
        return bool(result)
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_groq() -> bool:
    """Test Groq API connection."""
    try:
        import os
        from openai import OpenAI
        key = os.environ.get("GROQ_API_KEY", "")
        if not key:
            print("  GROQ_API_KEY not set")
            return False
        client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=5,
        )
        return bool(resp.choices[0].message.content)
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_gmail_imap() -> bool:
    """Test Gmail IMAP connection."""
    import imaplib
    gmail = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail or not password:
        print("  GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set")
        return False
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(gmail, password)
        imap.logout()
        return True
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_sqlite() -> bool:
    """Test SQLite database write and read."""
    import sqlite3
    import tempfile
    import os
    # Use delete=False to avoid Windows file-locking issue with NamedTemporaryFile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        conn = sqlite3.connect(tmp.name)
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO test (val) VALUES ('hello')")
        conn.commit()
        result = conn.execute("SELECT val FROM test").fetchone()
        conn.close()
        return result[0] == "hello"
    except Exception as e:
        print(f"  Error: {e}")
        return False
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def test_telegram() -> bool:
    """Test Telegram Bot API connection."""
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("  TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set (optional)")
        return True  # Optional — don't fail overall suite if not configured
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id, "text": "JobPilot: connection test OK"}, timeout=8)
        return resp.ok
    except Exception as e:
        print(f"  Error: {e}")
        return False


def main() -> None:
    """Run all connection tests and print results."""
    tests = [
        ("SQLite", test_sqlite),
        ("Gemini API", test_gemini),
        ("Groq API", test_groq),
        ("Gmail IMAP", test_gmail_imap),
        ("Telegram Bot", test_telegram),
    ]

    all_pass = True
    print("\nJobPilot Connection Tests")
    print("-" * 40)
    for name, fn in tests:
        print(f"Testing {name}...", end=" ", flush=True)
        result = fn()
        status = "PASS" if result else "FAIL"
        print(status)
        if not result:
            all_pass = False

    print("-" * 40)
    print("All tests passed." if all_pass else "Some tests failed. Check your .env file.")


if __name__ == "__main__":
    main()

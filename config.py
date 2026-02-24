import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Reddit API
    REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
    REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
    REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "ResearchAssistant/1.0")

    # OpenAI API
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    # App settings
    DEBUG = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    PORT = int(os.environ.get("PORT", 5000))

    # Storage
    DB_PATH = os.environ.get("DB_PATH", "data/research.db")
    EXPORT_DIR = os.environ.get("EXPORT_DIR", "data/exports")

    # Collection defaults and limits
    DEFAULT_MAX_THREADS = 15
    MAX_THREADS_LIMIT = 25
    DEFAULT_MAX_COMMENTS_PER_THREAD = 100
    MAX_COMMENTS_PER_THREAD_LIMIT = 200
    TOTAL_COMMENTS_CAP = 750
    LLM_BATCH_SIZE = 20

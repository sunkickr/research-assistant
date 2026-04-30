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
    ALT_SUMMARY_MODEL = os.environ.get("ALT_SUMMARY_MODEL", "gpt-4.1-mini")

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

    # Product Hunt API
    PRODUCT_HUNT_API_TOKEN = os.environ.get("PRODUCT_HUNT_API_TOKEN", "")

    # Multi-source settings
    HN_MAX_STORIES = 10
    WEB_MAX_ARTICLES = 8
    PH_MAX_POSTS = 5

    # Max pages for HN/Web/Reviews pagination in "Find More" expand
    HN_MAX_EXPAND_PAGES = 3
    WEB_MAX_EXPAND_PAGES = 3
    REVIEWS_MAX_EXPAND_PAGES = 2

    # Job search settings
    JOB_SEARCH_DIR = os.environ.get("JOB_SEARCH_DIR", "data/job_searches")
    COMPANY_LISTS_DIR = os.environ.get("COMPANY_LISTS_DIR", "data/company_lists")
    JOB_SEARCH_BATCH_SIZE = int(os.environ.get("JOB_SEARCH_BATCH_SIZE", "15"))
    JOB_SEARCH_MAX_COMPANIES = int(os.environ.get("JOB_SEARCH_MAX_COMPANIES", "100"))

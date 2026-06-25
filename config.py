import os
from dotenv import load_dotenv

load_dotenv()

def _env(key, default=""):
    return os.getenv(key, default)

def _env_int(key, default=0):
    return int(os.getenv(key, str(default)))

def _env_float(key, default=0.0):
    return float(os.getenv(key, str(default)))

PROFILE = {
    "name": _env("YOUR_NAME"),
    "email": _env("YOUR_EMAIL"),
    "phone": _env("YOUR_PHONE"),
    "location": _env("YOUR_LOCATION"),
    "linkedin": _env("YOUR_LINKEDIN"),
    "github": _env("YOUR_GITHUB"),
    "portfolio": _env("YOUR_PORTFOLIO"),
    "education": _env("YOUR_EDUCATION"),
    "graduation_year": _env("YOUR_GRADUATION_YEAR"),
    "company_1_name": _env("COMPANY_1_NAME"),
    "company_1_role": _env("COMPANY_1_ROLE"),
    "company_1_location": _env("COMPANY_1_LOCATION"),
    "company_1_duration": _env("COMPANY_1_DURATION"),
    "company_2_name": _env("COMPANY_2_NAME"),
    "company_2_role": _env("COMPANY_2_ROLE"),
    "company_2_location": _env("COMPANY_2_LOCATION"),
    "company_2_duration": _env("COMPANY_2_DURATION"),
}

SMTP_ACCOUNTS = []
for i in range(1, 4):
    email = _env(f"SMTP_ACCOUNT_{i}")
    password = _env(f"SMTP_PASSWORD_{i}")
    if email and password:
        SMTP_ACCOUNTS.append({"email": email, "password": password})

DAILY_EMAIL_CAP = _env_int("DAILY_EMAIL_CAP", 1000)
PER_ACCOUNT_CAP = _env_int("PER_ACCOUNT_CAP", 500)
SEND_DELAY_MIN = _env_int("SEND_DELAY_MIN", 3)
SEND_DELAY_MAX = _env_int("SEND_DELAY_MAX", 10)
MIN_MATCH_CONFIDENCE = _env_float("MIN_MATCH_CONFIDENCE", 0.3)
MIN_EMAIL_CONFIDENCE = _env_float("MIN_EMAIL_CONFIDENCE", 0.3)
REPORT_TO_EMAIL = _env("REPORT_TO_EMAIL")
MODE = _env("MODE", "prod").lower()  # "dev" = India only, "prod" = international remote

def _load_numbered_keys(prefix):
    """Load API keys with _1, _2, ... suffixes into a list."""
    keys = []
    bare = os.getenv(prefix, "")
    if bare:
        keys.append(bare)
    for i in range(1, 10):
        key = os.getenv(f"{prefix}_{i}", "")
        if key and key not in keys:
            keys.append(key)
    return keys

GROQ_API_KEYS = _load_numbered_keys("GROQ_API_KEY")
GEMINI_API_KEYS = _load_numbered_keys("GEMINI_API_KEY")
GOOGLE_API_KEYS = _load_numbered_keys("GOOGLE_API_KEY")
GOOGLE_CSE_ID = _env("GOOGLE_CSE_ID")

INELIGIBLE_KEYWORDS = [
    "us citizen", "us work authorization", "us only", "usa only",
    "united states only", "u.s. only", "must be located in the us",
    "not available in india", "excludes india",
    "security clearance required", "active clearance",
    "no sponsorship", "will not sponsor",
]

DEV_TARGET_LOCATIONS = [
    "india", "remote", "bangalore", "bengaluru", "mumbai", "delhi",
    "hyderabad", "pune", "chennai", "kolkata", "noida", "gurgaon",
    "gurugram", "indore", "ahmedabad", "jaipur",
    "worldwide", "anywhere", "global", "apac", "asia",
]

PROD_TARGET_LOCATIONS = [
    "remote", "worldwide", "anywhere", "global", "international",
    "singapore", "uk", "united kingdom", "england", "london",
    "europe", "eu", "germany", "netherlands", "ireland", "sweden",
    "denmark", "finland", "norway", "switzerland", "austria",
    "france", "spain", "portugal", "italy", "belgium", "poland",
    "czech", "estonia", "latvia", "lithuania", "romania", "croatia",
    "australia", "new zealand", "canada",
    "uae", "dubai", "qatar", "saudi", "bahrain",
    "japan", "south korea", "hong kong", "taiwan",
    "india", "apac", "asia", "asia pacific",
    "emea", "latam", "americas",
]

TARGET_COUNTRIES = PROD_TARGET_LOCATIONS if MODE == "prod" else DEV_TARGET_LOCATIONS

TECH_KEYWORDS = [
    "developer", "engineer", "software", "frontend", "backend",
    "full stack", "fullstack", "full-stack", "devops", "sre",
    "data scientist", "data engineer", "data analyst", "machine learning",
    "ml engineer", "ai engineer", "deep learning", "nlp",
    "cloud", "infrastructure", "platform", "security", "cybersecurity",
    "qa", "quality assurance", "test engineer", "sdet",
    "mobile", "ios", "android", "react native", "flutter",
    "ui/ux", "ux engineer", "product designer",
    "python", "javascript", "typescript", "golang", "java", "rust",
    "react", "node", "vue", "angular", "django", "fastapi",
    "kubernetes", "docker", "aws", "azure", "gcp",
    "blockchain", "web3", "smart contract", "solidity",
]
